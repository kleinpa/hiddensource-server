load("@rules_steam//steam:repos.bzl", "steam_app")

BUILD_steamworks_sdk_redist = "25329704"

def repos(ctx):
    steam_app(
        name = "steamworks_sdk_redist",
        depots = [
            {"app": "1007", "depot": "1004", "manifest": "7604377918839582995"},
        ],
    )
    return ctx.extension_metadata(reproducible = True)

steamapps_bzlmod = module_extension(implementation = repos)
