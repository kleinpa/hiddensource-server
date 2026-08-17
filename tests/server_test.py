"""End-to-end tests for The Hidden: Source server image.

These start the real image in a container (with no TTY attached, matching how
Kubernetes runs pods) and interrogate both its A2S/RCON endpoints and its
container stdout log stream.
"""

import unittest

import srcds
from srcds import Server

_RCON_PASSWORD = "hunter2"


class BootTest(unittest.TestCase):
    """The server comes up under wine, loads a map and answers the browser."""

    def test_default_command_answers_a2s(self):
        """With no arguments the image's own cmd has to produce a live server."""
        with Server() as server:
            server.wait_for_logs("Connection to Steam servers successful.")
            info = server.info()
            self.assertEqual(info.map_name, "hdn_traindepot")
            self.assertEqual(info.folder, "hidden")
            self.assertIn("Hidden", info.game)
            self.assertEqual(info.player_count, 0)
            self.assertEqual(info.server_type, "d", "server is not dedicated")
            self.assertEqual(info.platform, "w",
                             "server is not the Windows build")

    def test_args_override_the_default_map(self):
        """A deployment overriding cmd, as a Kubernetes `args:` does."""
        with Server("+map", "hdn_docks") as server:
            self.assertEqual(server.info().map_name, "hdn_docks")

    def test_convar_values_may_contain_spaces(self):
        """No quotes of our own: wine quotes arguments containing spaces."""
        with Server("+map", "hdn_traindepot", "+hostname", "a b c") as server:
            self.assertEqual(server.info().server_name, "a b c")

    def test_server_cfg_is_executed(self):
        """server.cfg only takes effect if the engine execs it on map load."""
        with Server() as server:
            rules = server.rules()
            self.assertEqual(rules["mp_friendlyfire"], "1")
            self.assertEqual(rules["hdn_hiddenrounds"], "3")

    def test_console_is_streamed_without_a_tty(self):
        """Both boot output and live post-boot console spew reach stdout."""
        with Server("+map", "hdn_traindepot", "+rcon_password",
                    _RCON_PASSWORD) as server:
            server.wait_for_logs("Console initialized.",
                                 "Loaded plugin auth_by_steam_group.smx")
            server.rcon(_RCON_PASSWORD, "echo console-sentinel")
            server.wait_for_logs("console-sentinel")

    def test_exits_when_signalled(self):
        """Ctrl-C and `docker stop` work, although wine runs as PID 1 and the
        kernel ignores signals that PID 1 does not handle."""
        for signal, exit_code in (("SIGINT", 130), ("SIGTERM", 143)):
            with self.subTest(signal), Server() as server:
                self.assertEqual(server.kill(signal), exit_code)


class WineTest(unittest.TestCase):
    """The parts of the wine arrangement that fail quietly."""

    def test_wine_is_baked_into_the_image(self):
        """The image must not reach for the Debian archive at boot."""
        with Server() as server:
            logs = server.logs()
            for marker in ("apt-get", "Get:1 http", "Unpacking "):
                self.assertNotIn(marker, logs,
                                 "image is installing packages at runtime")

    def test_no_wine_configuration_errors(self):
        """A missing wine dependency or broken console shim shows up in logs."""
        with Server() as server:
            logs = server.logs()
            self.assertNotIn("wine: cannot find", logs)
            self.assertNotIn("Bad EXE format", logs)
            self.assertNotIn("!GetNumberOfConsoleInputEvents", logs)
            self.assertNotIn("Failed to load Steam Service", logs)
            self.assertNotIn("unable to load trusted SSL root certificates",
                             logs)


class AddonTest(unittest.TestCase):
    """MetaMod, SourceMod, and all configured plugins load cleanly."""

    _EXPECTED_PLUGINS = (
        "basecommands.smx",
        "nextmap.smx",
        "mapchooser.smx",
        "nominations.smx",
        "rockthevote.smx",
        "auth_by_steam_group.smx",
    )

    def test_metamod_loads_sourcemod(self):
        """MetaMod loads SourceMod rather than silently reporting 0 plugins."""
        with Server() as server:
            server.wait_for_logs("[META] Loaded 1 plugin")
            self.assertNotIn("[META] Failed to load plugin", server.logs())

    def test_plugins_load(self):
        """Every configured plugin loads (including auth_by_steam_group's C++ ext)."""
        with Server() as server:
            server.wait_for_logs(*(f"Loaded plugin {plugin} successfully"
                                   for plugin in self._EXPECTED_PLUGINS))
            logs = server.logs()
            self.assertNotIn("[SM] Unable to load extension", logs)
            self.assertNotIn("[SM] Unable to load plugin", logs)
            self.assertNotIn("Unknown command", logs)

    def test_mapcycle_lists_the_installed_maps(self):
        """mapcycle.txt lists real multiplayer maps and excludes tutorials."""
        with Server("+map", "hdn_traindepot", "+rcon_password",
                    _RCON_PASSWORD) as server:
            server.wait_for_logs("Loaded plugin nextmap.smx successfully")
            maps = server.rcon(_RCON_PASSWORD, "listmaps").splitlines()
            self.assertIn("hdn_docks", maps)
            self.assertNotIn("hdn_tutorial", maps)
            self.assertNotIn("dev_physworld", maps)
            for m in maps:
                self.assertNotIn("tutorial", m)


if __name__ == "__main__":
    srcds.main()
