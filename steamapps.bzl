load("@rules_steam//:steam.bzl", "steam_app")

BUILD_srcds_2006 = "123345"

def repos(ctx):
    steam_app(
        name = "srcds_2006",
        depots = [
            {"app": "205", "depot": "205", "manifest": "29462157349265252"},
            {"app": "205", "depot": "206", "manifest": "8460749637658605693"},
            {"app": "205", "depot": "207", "manifest": "1025719905895126023"},
            {"app": "205", "depot": "208", "manifest": "3149153229693629388"},
            {"app": "205", "depot": "1004", "manifest": "3578600867548439549"},
        ],
    )

steamapps_bzlmod = module_extension(implementation = repos)
