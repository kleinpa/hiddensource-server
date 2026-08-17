"""Black-box test harness for a Source dedicated server container image.

The image under test is supplied by Bazel through two environment variables:

``SRCDS_IMAGE_LOADER``
    Runfiles path of an ``oci_load`` runnable that imports the image into the
    local container daemon. Executed once per test binary.

``SRCDS_REPO_TAG``
    The tag ``SRCDS_IMAGE_LOADER`` applies to the image.

Tests drive the server through :class:`Server`, which starts a container, waits
for the game to answer Valve's A2S query protocol, and removes the container
again. Containers get neither a tty nor an init process, as in a Kubernetes pod.
"""

import functools
import os
import shlex
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
_LOG_TIMEOUT_SECONDS = 60.0
_QUERY_TIMEOUT_SECONDS = 2.0
_RCON_TIMEOUT_SECONDS = 10.0
_EXIT_TIMEOUT_SECONDS = 5.0
_POLL_INTERVAL_SECONDS = 1.0

_GAME_PORT = 27015

# RCON packet types. The protocol reuses 2 for the server's answer to AUTH.
_RCON_AUTH = 3
_RCON_AUTH_RESPONSE = 2
_RCON_EXEC_COMMAND = 2
_RCON_RESPONSE_VALUE = 0

# Extra `docker run` flags for hosts whose container runtime needs them. The
# GitHub-hosted Ubuntu runners' default seccomp profile answers 32-bit (i386)
# socket syscalls with ENOSYS, preventing wineserver from starting ("wineserver:
# socket: Function not implemented") -- the image's userland is i386, see
# //base:bookworm.yaml. CI passes `--security-opt seccomp=unconfined` here.
_DOCKER_RUN_ARGS = shlex.split(os.environ.get("SRCDS_DOCKER_RUN_ARGS", ""))


class ServerError(AssertionError):
    """Raised when the server fails to start or to do what a test waits for."""


@functools.cache
def load_image() -> str:
    """Imports the image under test into the container daemon.

    Returns the repository tag the image was loaded under. The result is cached
    so that a test binary with many test cases only pays for one import.
    """
    tag = os.environ["SRCDS_REPO_TAG"]
    loader = runfiles.Create().Rlocation(os.environ["SRCDS_IMAGE_LOADER"])
    if loader is None or not os.path.exists(loader):
        raise ServerError(
            f"image loader {os.environ['SRCDS_IMAGE_LOADER']!r} missing from runfiles"
        )
    subprocess.run([loader], check=True, stdout=subprocess.DEVNULL)
    return tag


def _docker(*args: str, timeout=None) -> str:
    """Runs a docker command and returns its stdout."""
    result = subprocess.run(["docker", *args],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            errors="replace",
                            timeout=timeout)
    if result.returncode != 0:
        raise ServerError(f"docker {args[0]} failed:\n{result.stderr}")
    return result.stdout.strip()


class Server:
    """A running srcds container, queryable over A2S and RCON.

    Use as a context manager::

        with Server("+map", "hdn_docks") as server:
            self.assertEqual(server.info().map_name, "hdn_docks")

    `args` replaces the image's cmd, exactly as a Kubernetes `args:` does.

    Any exception leaving the `with` block carries the server's console log,
    which is gone along with the container by the time it is reported.
    """

    def __init__(self, *args: str):
        self._args = args
        self._container = None
        self._addresses = {}

    def __enter__(self) -> "Server":
        # The daemon picks the host ports, so that nothing can claim one
        # between it being chosen and being bound.
        self._container = _docker(
            "run", "--detach", *_DOCKER_RUN_ARGS,
            "--publish", f"127.0.0.1::{_GAME_PORT}/udp",
            "--publish", f"127.0.0.1::{_GAME_PORT}/tcp",
            load_image(), *self._args)  # yapf: disable
        try:
            self._wait_until_ready()
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise
        return self

    def __exit__(self, _exc_type, error, _traceback) -> None:
        try:
            if error is not None:
                error.add_note(f"--- srcds console log ---\n{self.logs()}")
        finally:
            _docker("rm", "--force", self._container)

    def logs(self) -> str:
        """Returns the container's combined stdout and stderr so far."""
        return subprocess.run(["docker", "logs", self._container],
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,
                              text=True,
                              errors="replace").stdout

    def info(self):
        """Returns the A2S_INFO response from the server."""
        return a2s.info(self._address("udp"), timeout=_QUERY_TIMEOUT_SECONDS)

    def rules(self):
        """Returns the A2S_RULES response as a name-to-value mapping."""
        return a2s.rules(self._address("udp"), timeout=_QUERY_TIMEOUT_SECONDS)

    def wait_for_logs(self, *lines: str) -> None:
        """Blocks until every one of `lines` has appeared in the log.

        The log runs behind A2S: the server answers queries as soon as the map
        is up, before server.cfg has finished loading plugins.
        """
        deadline = time.monotonic() + _LOG_TIMEOUT_SECONDS
        while True:
            logs = self.logs()
            missing = [line for line in lines if line not in logs]
            if not missing:
                return
            if time.monotonic() >= deadline:
                raise ServerError(f"never logged: {missing}")
            time.sleep(_POLL_INTERVAL_SECONDS)

    def rcon(self, password: str, command: str) -> str:
        """Runs a console command over RCON and returns its output."""
        with socket.create_connection(self._address("tcp"),
                                      timeout=_RCON_TIMEOUT_SECONDS) as sock:
            stream = sock.makefile("rb")

            def send(request_id, packet_type, body):
                payload = (request_id.to_bytes(4, "little") +
                           packet_type.to_bytes(4, "little") + body.encode() +
                           b"\x00\x00")
                sock.sendall(len(payload).to_bytes(4, "little") + payload)

            def read(count):
                data = stream.read(count)
                if len(data) != count:
                    raise ServerError("RCON connection closed mid-packet")
                return data

            def receive():
                packet = read(int.from_bytes(read(4), "little"))
                return (int.from_bytes(packet[0:4], "little", signed=True),
                        int.from_bytes(packet[4:8], "little"), packet[8:-2])

            send(0, _RCON_AUTH, password)
            # An empty RESPONSE_VALUE precedes the actual answer.
            request_id, packet_type, _ = receive()
            while packet_type != _RCON_AUTH_RESPONSE:
                request_id, packet_type, _ = receive()
            if request_id == -1:
                raise ServerError("RCON authentication was rejected")

            send(1, _RCON_EXEC_COMMAND, command)
            # The server answers a command with one or more packets and no
            # count, so a second, empty request is used as a fence: its reply
            # cannot arrive before the real one has been fully sent.
            send(2, _RCON_RESPONSE_VALUE, "")
            output = b""
            while True:
                request_id, _, body = receive()
                if request_id == 2:
                    return output.decode(errors="replace")
                output += body

    def kill(self, signal: str) -> int:
        """Signals the server and returns its exit code once it is gone."""
        _docker("kill", "--signal", signal, self._container)
        try:
            return int(
                _docker("wait", self._container, timeout=_EXIT_TIMEOUT_SECONDS))
        except subprocess.TimeoutExpired:
            raise ServerError(f"server survived {signal}") from None

    def _address(self, protocol: str) -> tuple:
        if protocol not in self._addresses:
            published = _docker("port", self._container,
                                f"{_GAME_PORT}/{protocol}")
            host, port = published.splitlines()[0].rsplit(":", 1)
            self._addresses[protocol] = (host, int(port))
        return self._addresses[protocol]

    def _wait_until_ready(self) -> None:
        """Blocks until the server answers A2S."""
        deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
        last_error = None
        while time.monotonic() < deadline:
            if _docker("inspect", "--format", "{{.State.Running}}",
                       self._container) != "true":
                raise ServerError("server exited during startup")
            try:
                self.info()
                return
            except Exception as error:  # socket timeouts, partial responses, ...
                last_error = error
            time.sleep(_POLL_INTERVAL_SECONDS)
        raise ServerError(
            f"server did not answer A2S within {_STARTUP_TIMEOUT_SECONDS:.0f}s "
            f"(last error: {last_error!r})")


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
