"""The prebuilt Windows binaries this image still fetches rather than builds.

MetaMod:Source and SourceMod are mostly built from source via rules_sourcemod
(MODULE.bazel), cross-compiled for Windows the same way counterstrikesource-server
and cs2-server build their own Linux addons, just with a different target
platform -- the Hidden's dedicated server (Steam app 205) only exists for
Windows (see README), so the addons that load into it have to be Windows
binaries too.

What's left here has no such build:

  - hidden: the mod itself. There is no source, only the beta release archive
    the mod's own site still serves.
  - auth_by_steam_group_windows: a lanofdoom-maintained SourceMod extension
    with its own repository and release process, not part of this build.
  - metamod_windows_loader: MetaMod:Source's own loader (addons/metamod/bin/
    server.dll) specifically -- see the "MetaMod loader" comment in
    BUILD.bazel for why this one piece is fetched prebuilt rather than
    built alongside the rest of MetaMod's core.
  - vc_redist_x86: Microsoft's own Visual C++ Redistributable installer, the
    source BUILD.bazel's "Real msvcp140.dll Layer" extracts one DLL out of --
    see that comment for why wine's own substitute isn't enough.

All four are pinned by checksum and fetched by Bazel, so two builds of this
repository at the same revision install the same bytes.
"""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive", "http_file")

_ALL_FILES = """\
filegroup(
    name = "all",
    srcs = glob(["**/*"], exclude = ["WORKSPACE", "BUILD.bazel"]),
    visibility = ["//visibility:public"],
)
"""

def repos(ctx):
    """Fetches the mod and its Windows addons."""

    # The Hidden: Source Beta 4b full release, from the mod's own site. There is
    # no Steam app to pin this to -- it was never distributed through Steam --
    # so the checksum is the only thing between the build and whatever that URL
    # serves later.
    #
    # The archive carries a `hidden/bin/server_i486.so` alongside `server.dll`,
    # which is a red herring: the mod has a Linux build but the 2006 engine no
    # longer does, so there is nothing to load it into.
    http_archive(
        name = "hidden",
        build_file_content = _ALL_FILES,
        sha256 = "ae73d35c42a8521dc0c0ebda99e85f3e731176d04abeb7c6263ca4bca0cfe7ae",
        urls = ["http://www.hidden-source.com/downloads/hsb4b-full.zip"],
    )

    http_archive(
        name = "auth_by_steam_group_windows",
        build_file_content = _ALL_FILES,
        sha256 = "2ce9f93038773affe8d7c7acc4fe156735f095f287889cafeb07fb78f512007d",
        urls = ["https://github.com/lanofdoom/auth-by-steam-group/releases/download/v2.3.0/auth_by_steam_group.zip"],
    )

    # AlliedModders' own official MetaMod:Source 1.12.0 Windows build, source
    # commit git1219 -- close in vintage to _METAMOD_COMMIT in rules_sourcemod,
    # and real MSVC-built. Only its loader (addons/metamod/bin/server.dll) is
    # used from this archive; see the "MetaMod loader" comment in BUILD.bazel.
    http_archive(
        name = "metamod_windows_loader",
        build_file_content = _ALL_FILES,
        sha256 = "fcd1561db37b6d9f9405088704fe724a0203eba050d17476993525c22c077899",
        urls = ["https://mms.alliedmods.net/mmsdrop/1.12/mmsource-1.12.0-git1219-windows.zip"],
    )

    # The x86 VC++ Redistributable bootstrapper, from Microsoft's own CDN
    # under Microsoft's distribution terms (same basis as rules_sourcemod's
    # xwin_sysroot fetch of the Windows SDK/CRT for this build's own
    # toolchain -- see windows/xwin_sysroot.bzl there). This exact URL is
    # what https://aka.ms/vs/17/release/vc_redist.x86.exe currently redirects
    # to; the sha256 is both verified here and embedded in the URL itself by
    # Microsoft's own CDN naming convention.
    #
    # It's a thin "web installer" -- the runtime DLLs it installs aren't
    # unpacked Microsoft CAB/MSI files sitting in the top-level archive the
    # way older, fully offline redistributable installers were; they're in a
    # second Cabinet ("WixAttachedContainer") stitched onto the end of the
    # PE image after its Authenticode signature, which is what the "Real
    # msvcp140.dll Layer" genrules in BUILD.bazel dig out.
    http_file(
        name = "vc_redist_x86",
        downloaded_file_path = "vc_redist.x86.exe",
        sha256 = "0c09f2611660441084ce0df425c51c11e147e6447963c3690f97e0b25c55ed64",
        urls = ["https://download.visualstudio.microsoft.com/download/pr/9d270333-8b7b-4f96-9458-6fcdb2ec0b25/0C09F2611660441084CE0DF425C51C11E147E6447963C3690F97E0B25C55ED64/VC_redist.x86.exe"],
    )

repos_bzlmod = module_extension(implementation = repos)
