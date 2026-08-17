# LAN of DOOM The Hidden: Source Server
Docker image for a private, preconfigured private The Hidden: Source server as
used by the LAN of DOOM.

# Installation
Run ``docker pull ghcr.io/kleinpa/hiddensource-server:latest``

# Installed Addons
*  LAN of DOOM Authenticate by Steam Group
*  MetaMod:Source
*  SourceMod

# Environmental Variables
``HIDDEN_HOSTNAME`` The name of the server as listed in Valve's server browser.

``HIDDEN_PASSWORD`` The password users must enter in order to join the server.

``HIDDEN_MAP`` The first map to run on the server. ``hdn_traindepot`` by
default.

``HIDDEN_MOTD`` The MOTD to use for the server.

``HIDDEN_PORT`` The port to use for the server. ``27015`` by default.

``RCON_PASSWORD`` The rcon password for the server.

``STEAM_GROUP_ID`` The Steam group to use for the allowlist of users joining the
server.

``STEAM_API_KEY`` The [Steam API key](https://steamcommunity.com/dev/apikey) to
use for the group membership checks with the Steam's Web API.

# TODO

MetaMod:Source's loader (`addons/metamod/bin/server.dll` -- the small bridge
that masquerades as the game DLL to the engine) is fetched prebuilt from
AlliedModders' official Windows release rather than cross-compiled from
source like the rest of MetaMod and SourceMod (see `MODULE.bazel`,
`repos.bzl`, and the "MetaMod loader Override Layer" in `BUILD.bazel`).

This is a workaround, not a fix. The clang-cl cross-compile of that same
loader source boots fine on its own -- DLLInit succeeds, Game.dll loads, the
manual GameInit/LevelInit/LevelShutdown hooks fire correctly -- but crashes
later, deep in steamclient.dll/tier0.dll on a background thread, jumping
through a corrupted pointer. Confirmed by swapping binaries in isolation:
official loader + this build's own MetaMod core works; this build's own
loader + official core crashes the same way every time. So the
miscompilation is specifically in `loader/*.cpp` in metamod-source, not in
MetaMod's core (where SourceHook lives, and where two real clang-cl codegen
bugs were already found and fixed this way -- see `sh_memfuncinfo.h` in
rules_sourcemod's metamod-source patches).

Finding the exact miscompiled line would need the same kind of disassembly
work that found the two core bugs, but starting from a much smaller
haystack (`loader/gamedll.cpp`, `loader/loader.cpp`, `loader/serverplugin.cpp`,
`loader/utility.cpp` -- none of them touch inline asm or naked functions, so
the bug is subtler than the ones already found). Once fixed, this override
layer and the `metamod_windows_loader` dependency it pulls in can go away.

## Resolved: SourceMod would not load

(Kept for context -- both root causes below are fixed, not a TODO.)

SourceMod's plugin failed to load with nothing useful in the console log:
`[META] Failed to load plugin addons/sourcemod/bin/sourcemod_mm: Module not
found. (failed to load bin/sourcepawn.jit.x86.dll)`, only visible at all by
adding `-condebug` and reading `console.log` directly -- the RCON/A2S path
this image normally relies on for observability never gets far enough to
show it. Two independent bugs, found and fixed in that order:

1. rules_sourcemod named its compiled SourcePawn JIT `sourcepawn.vm.dll`
   unconditionally. SourceMod's own runtime (`SOURCEPAWN_DLL` in
   `core/sourcemod.cpp`) only looks for that name on 64-bit; on 32-bit
   (`KE_ARCH_X86`, what every preset this build actually uses targets) it
   looks for `sourcepawn.jit.x86.dll` specifically. Fixed upstream in
   rules_sourcemod (`sourcemod/sdk.BUILD.bazel`'s `sourcepawn_vm_shared`
   rename, now `select()`ed on arch) -- no workaround needed here.

2. Once SourceMod's core could actually load, it aborted immediately with
   `wine: Call from ... to unimplemented function
   msvcp140.dll.?_Throw_Cpp_error@std@@YAXH@Z` -- SourcePawn's JIT links
   against real msvcp140.dll (the MSVC C++ standard library), and wine's own
   builtin substitute doesn't implement all of it. Fixed the same way as the
   existing `wine_zlib1_layer`: ship the real DLL next to srcds.exe
   (`//:msvcp140_layer`, extracted at build time from Microsoft's own VC++
   Redistributable installer -- see `repos.bzl`'s `vc_redist_x86` and the
   "Real msvcp140.dll Layer" comment in `BUILD.bazel` for how, since it
   isn't a plain archive) -- plus `WINEDLLOVERRIDES=msvcp140=n` (`//:image`'s
   `env`, and `entrypoint.sh`'s own default), since unlike zlib1.dll,
   msvcp140 *is* a wine builtin already and wine tries that first regardless
   of what's sitting next to the .exe.

Separately, rules_sourcemod's presets ship no bundled SourcePawn plugins by
design (a per-deployment choice -- see `sourcemod_game_server`'s docstring
there), so the plugins server.cfg actually wants (nextmap, mapchooser,
nominations, rockthevote) needed adding explicitly
(`//:sourcemod_plugins_layer`, and a new `sourcemod_sdk` module extension
call in `MODULE.bazel` to reach `@sourcemod_sdk` at all -- repo mapping
under bzlmod is per-module, so rules_sourcemod using that extension in its
own MODULE.bazel doesn't make the repo visible here). server.cfg's
`sm plugins load disabled/mapchooser` (and nominations, rockthevote) also
had to drop the `disabled/` prefix: rules_sourcemod installs those three to
plain `plugins/`, not `plugins/disabled/`, specifically so their
`#include`-based dependencies on each other resolve by filename -- see
`sourcemod/plugins.bzl`'s `PROMOTED_PLUGINS` in rules_sourcemod.

`bazel test //tests:server_test` is 7/8 green after all of the above. The
one still failing, `test_convar_values_may_contain_spaces`, is unrelated to
any of this: a hostname argument containing spaces comes back with stray
backslashes (`'\ a b c\""'` instead of `'a b c'`), which looks like `wine
start /wait`'s own command-line reconstruction re-escaping a quoted argument
it was simply supposed to pass through -- not investigated further, since it
reproduces the same way regardless of MetaMod/SourceMod being involved at
all.
