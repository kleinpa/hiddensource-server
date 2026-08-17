"""End-to-end tests for The Hidden: Source server image.

These start the real image in a container and interrogate it over Valve's A2S
query protocol, which is the same thing the Steam server browser does. If these
pass, a client can find and join the server.

There is more riding on them here than in the sibling images. This server is a
Windows binary running under wine on an X display that exists only to satisfy
it, and most of the ways that arrangement breaks -- no display, no Win32
console, a missing wine dependency, HL2 content the engine cannot mount -- do
not crash. They hang, or they stop at a message box nobody can click. A2S
answering at all is the assertion that separates those from a working server.
"""

import srcds


class BootTest(srcds.ServerTestCase):
    """The server comes up under wine, loads a map and answers the browser."""

    def test_default_command_answers_a2s(self):
        """With no arguments the image's own cmd has to produce a live server."""
        with self.server() as server:
            info = server.info()
            self.assertEqual(info.map_name, "hdn_traindepot")
            self.assertEqual(info.folder, "hidden")
            self.assertIn("Hidden", info.game)
            self.assertEqual(info.player_count, 0)
            self.assertEqual(info.server_type, "d", "server is not dedicated")

    def test_args_override_the_default_map(self):
        """A deployment overriding cmd, as a Kubernetes `args:` does."""
        with self.server(args=["+map", "hdn_docks"]) as server:
            self.assertEqual(server.info().map_name, "hdn_docks")

    def test_convar_values_may_contain_spaces(self):
        """No quotes of our own: wine adds exactly the ones needed.

        docker run's argv never goes through a shell, so this arrives at
        entrypoint.sh's `"$@"`, and then `wine start /wait`, as one argument
        containing literal spaces, not something pre-split or pre-quoted.
        wine itself has to re-serialize that into the single command-line
        string a Windows process actually receives (there is no argv at the
        Win32 level), and does so correctly on its own -- confirmed by hand
        against a running container. Adding our own quotes here to "help"
        breaks it: wine still sees an argument containing quote characters
        that themselves need escaping, on top of the space, and the result
        does not round-trip -- confirmed by hand too, as literal `\\ a b c\\""`
        coming back from the server instead of `a b c`.
        """
        with self.server(
                args=["+map", "hdn_traindepot", "+hostname",
                      "a b c"]) as server:
            self.assertEqual(server.info().server_name, "a b c")

    def test_server_cfg_is_executed(self):
        """server.cfg only takes effect if the engine execs it on map load."""
        with self.server() as server:
            rules = server.rules()
            self.assertEqual(rules["mp_friendlyfire"], "1")
            self.assertEqual(rules["hdn_hiddenrounds"], "3")


class WineTest(srcds.ServerTestCase):
    """The parts of the wine arrangement that fail quietly."""

    def test_wine_is_baked_into_the_image(self):
        """The image must not reach for the Debian archive at boot.

        The image this replaces installed wine from its entrypoint on every
        start. If that ever comes back, it shows up here as apt output in the
        log -- and on a host with no route to deb.debian.org, as a server that
        never starts at all.
        """
        with self.server() as server:
            logs = server.logs()
            for marker in ("apt-get", "Get:1 http", "Unpacking "):
                self.assertNotIn(marker, logs,
                                 "image is installing packages at runtime")

    def test_no_wine_configuration_errors(self):
        """A missing wine dependency shows up as a wine err:, not a crash."""
        with self.server() as server:
            self.assertNotIn("wine: cannot find", server.logs())
            self.assertNotIn("Bad EXE format", server.logs())


class AddonTest(srcds.ServerTestCase):
    """MetaMod and SourceMod load, and our plugins with them."""

    def test_sourcemod_plugins_load(self):
        """Their absence is quiet: an unhandled `rtv` and nothing in the log.

        Asked over RCON rather than read out of the log, because server.cfg runs
        during map activation and the console output of a running container
        stops partway through startup.

        Checked by each plugin's own display title, not its .smx filename --
        "sm plugins list" reports a successfully loaded plugin by the title
        its `public Plugin myinfo` declares, e.g. "Nextmap", never the
        filename a `sm plugins load <name>` command took to load it.
        """
        with self.server(args=["+map", "hdn_traindepot"],
                         rcon_password="test-rcon") as server:
            listing = server.rcon("sm plugins list")
            for plugin in ("Nextmap", "Authenticate by Steam Group"):
                self.assertIn(plugin, listing,
                              f"SourceMod did not load {plugin}")

    def test_mapcycle_is_generated_at_build_time(self):
        """//:mapcycle replaced an `ls | sed` that used to run at boot.

        The tutorial maps are excluded, so a mapcycle that still lists them is
        one that came from somewhere other than the build.
        """
        with self.server(args=["+map", "hdn_traindepot"],
                         rcon_password="test-rcon") as server:
            listing = server.rcon("sm_maplist")
            self.assertNotIn("tutorial", listing)


if __name__ == "__main__":
    srcds.main()
