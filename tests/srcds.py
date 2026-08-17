"""Black-box test harness for a Source dedicated server container image.

The image under test is supplied by Bazel through two environment variables set
by the ``srcds_container_test`` macro:

``SRCDS_IMAGE_LOADER``
    Runfiles path of an ``oci_load`` runnable that imports the image into the
    local container daemon. Executed once per test binary.

``SRCDS_REPO_TAG``
    The tag ``SRCDS_IMAGE_LOADER`` applies to the image.

Tests drive the server through :class:`Server`, which starts a container, waits
for the game to answer Valve's A2S query protocol, and tears the container down
again. Server logs are attached to any failure, so a broken image reports the
srcds console output rather than a bare timeout.
"""

import contextlib
import functools
import os
import socket
import subprocess
import sys
import time
import unittest

import a2s
from python.runfiles import runfiles

# srcds needs to mount the map, start the engine and hand off to SourceMod
# before it answers A2S. Cold containers on a loaded machine can take a while.
_STARTUP_TIMEOUT_SECONDS = 120.0
_QUERY_TIMEOUT_SECONDS = 2.0
_POLL_INTERVAL_SECONDS = 1.0
_RCON_TIMEOUT_SECONDS = 10.0

_DOCKER = os.environ.get("SRCDS_DOCKER", "docker")


class ContainerError(AssertionError):
    """Raised when the container fails to start or fails to become ready."""


@functools.cache
def load_image() -> str:
    """Imports the image under test into the container daemon.

    Returns the repository tag the image was loaded under. The result is cached
    so that a test binary with many test cases only pays for one import.
    """
    tag = os.environ["SRCDS_REPO_TAG"]
    loader = runfiles.Create().Rlocation(os.environ["SRCDS_IMAGE_LOADER"])
    if loader is None or not os.path.exists(loader):
        raise ContainerError(
            f"image loader {os.environ['SRCDS_IMAGE_LOADER']!r} missing from runfiles"
        )
    subprocess.run([loader], check=True, stdout=subprocess.DEVNULL)
    return tag


def _recv_exactly(sock, count: int) -> bytes:
    """Reads exactly `count` bytes, since a stream socket may split them up."""
    chunks = []
    remaining = count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ContainerError("RCON connection closed mid-packet")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _free_udp_port() -> int:
    """Returns a probably-free UDP port on the loopback interface.

    Tests bind the container to an ephemeral host port so that concurrent Bazel
    test shards do not collide on 27015.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    """A running srcds container, queryable over A2S.

    Use as a context manager::

        with Server(args=["+map", "de_nuke"]) as server:
            self.assertEqual(server.info().map_name, "de_nuke")

    `args` replaces the image's cmd, exactly as a Kubernetes `args:` does.

    `rcon_password` is passed the same way, so supplying one also replaces the
    cmd: a server started with an rcon_password and no `args` never gets the
    image's default `+map` and so never activates a level.
    """

    def __init__(self, env=None, args=(), game_port=27015, rcon_password=None):
        self._env = dict(env or {})
        self._args = list(args)
        self._game_port = game_port
        self._rcon_password = rcon_password
        self._host_port = None
        self._container = None

    def __enter__(self) -> "Server":
        tag = load_image()
        self._host_port = _free_udp_port()

        command = [_DOCKER, "run", "--detach", "--init"]
        for key, value in self._env.items():
            command += ["--env", f"{key}={value}"]
        command += [
            "--publish",
            f"127.0.0.1:{self._host_port}:{self._game_port}/udp",
            # RCON rides on TCP at the same port number.
            "--publish",
            f"127.0.0.1:{self._host_port}:{self._game_port}/tcp",
            tag,
        ]
        command += self._args
        if self._rcon_password:
            command += ["+rcon_password", self._rcon_password]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise ContainerError(f"docker run failed:\n{result.stderr}")
        self._container = result.stdout.strip()

        try:
            self._await_ready()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc_info) -> None:
        if self._container:
            subprocess.run(
                [_DOCKER, "rm", "--force", self._container],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._container = None

    @property
    def address(self):
        return ("127.0.0.1", self._host_port)

    def logs(self) -> str:
        """Returns the container's combined stdout and stderr so far."""
        if not self._container:
            return "<container not running>"
        return self._logs()

    def info(self):
        """Returns the A2S_INFO response from the server."""
        return a2s.info(self.address, timeout=_QUERY_TIMEOUT_SECONDS)

    def players(self):
        """Returns the A2S_PLAYER response from the server."""
        return a2s.players(self.address, timeout=_QUERY_TIMEOUT_SECONDS)

    def rules(self):
        """Returns the A2S_RULES response as a name-to-value mapping."""
        return a2s.rules(self.address, timeout=_QUERY_TIMEOUT_SECONDS)

    def wait_for_map(self, map_name: str, timeout=60.0) -> None:
        """Blocks until the server reports `map_name`, or fails with its log."""
        deadline = time.monotonic() + timeout
        last_seen = None
        while time.monotonic() < deadline:
            if not self._is_running():
                raise ContainerError(
                    f"server exited while changing to {map_name!r}:\n{self._logs()}"
                )
            try:
                last_seen = self.info().map_name
                if last_seen == map_name:
                    return
            except Exception:
                pass  # unreachable while the level is being swapped
            time.sleep(_POLL_INTERVAL_SECONDS)
        raise ContainerError(
            f"server never reached map {map_name!r} (last saw {last_seen!r}):\n{self._logs()}"
        )

    def rcon(self, command: str, wait_for_response: bool = True) -> str:
        """Runs a console command over RCON and returns its output.

        Requires the server to have been started with an rcon_password. This
        speaks Valve's RCON protocol directly rather than pulling in a library:
        it is a length-prefixed request/response framing over TCP.

        Commands that tear the level down take the RCON connection with them;
        pass `wait_for_response=False` for those and poll for the result
        instead, e.g. with :meth:`wait_for_map`.
        """
        if not self._rcon_password:
            raise ContainerError(
                "Server was constructed without an rcon_password")

        auth, exec_command, response_value = 3, 2, 0
        with socket.create_connection(self.address,
                                      timeout=_RCON_TIMEOUT_SECONDS) as sock:

            def send(request_id, packet_type, body):
                payload = (request_id.to_bytes(4, "little", signed=True) +
                           packet_type.to_bytes(4, "little", signed=True) +
                           body.encode() + b"\x00\x00")
                sock.sendall(
                    len(payload).to_bytes(4, "little", signed=True) + payload)

            def receive():
                size = int.from_bytes(_recv_exactly(sock, 4),
                                      "little",
                                      signed=True)
                packet = _recv_exactly(sock, size)
                return (
                    int.from_bytes(packet[0:4], "little", signed=True),
                    int.from_bytes(packet[4:8], "little", signed=True),
                    packet[8:-2].decode(errors="replace"),
                )

            send(0, auth, self._rcon_password)
            request_id, _, _ = receive()
            if request_id == -1:
                raise ContainerError("RCON authentication was rejected")

            send(1, exec_command, command)
            if not wait_for_response:
                return ""
            # The server answers a command with one or more response packets and
            # no count, so a second, empty command is used as a fence: its reply
            # cannot arrive before the real one has been fully sent.
            send(2, response_value, "")
            output = []
            while True:
                request_id, _, body = receive()
                if request_id == 2:
                    return "".join(output)
                output.append(body)

    def _await_ready(self) -> None:
        """Blocks until the server answers A2S, or raises with the server log."""
        deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
        last_error = None
        while time.monotonic() < deadline:
            if not self._is_running():
                raise ContainerError(
                    f"server exited during startup:\n{self._logs()}")
            try:
                self.info()
                return
            except Exception as error:  # socket timeouts, partial responses, ...
                last_error = error
            time.sleep(_POLL_INTERVAL_SECONDS)
        raise ContainerError(
            f"server did not answer A2S within {_STARTUP_TIMEOUT_SECONDS:.0f}s "
            f"(last error: {last_error!r}):\n{self._logs()}")

    def _is_running(self) -> bool:
        result = subprocess.run(
            [
                _DOCKER, "inspect", "--format", "{{.State.Running}}",
                self._container
            ],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == "true"

    def _logs(self) -> str:
        result = subprocess.run(
            [_DOCKER, "logs", self._container],
            capture_output=True,
            text=True,
        )
        return result.stdout + result.stderr


def main() -> None:
    """Runs the tests, honouring Bazel's --test_filter.

    Bazel passes the filter in TESTBRIDGE_TEST_ONLY, which plain
    `unittest.main()` ignores -- so `--test_filter` would silently run the whole
    suite, and every run would cost a container per test case.
    """
    test_filter = os.environ.get("TESTBRIDGE_TEST_ONLY")
    argv = list(sys.argv)
    if test_filter:
        argv.insert(1, "-k" + test_filter)
    unittest.main(argv=argv)


class ServerTestCase(unittest.TestCase):
    """Base class that attaches server logs to failures."""

    @contextlib.contextmanager
    def server(self, **kwargs):
        """Starts a :class:`Server`, annotating failures with its console log."""
        with Server(**kwargs) as running:
            try:
                yield running
            except AssertionError as error:
                raise self.failureException(
                    f"{error}\n\n--- srcds console log ---\n{running._logs()}"
                ) from error
