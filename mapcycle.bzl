"""Builds the server's mapcycle from the maps that are actually installed.

The image this replaces generated the mapcycle inside its entrypoint, with

    ls /opt/game/hidden/maps/*.bsp | grep -v tutorial | sed -e '...' > cfg/mapcycle.txt

which works but decides at boot what the image contains. Doing it at build time
makes the mapcycle part of the image -- inspectable without running it, and
identical on every start.
"""

# The tutorial maps ship with the mod but are single-player walkthroughs, and
# `hidden_maps` in gameinfo.txt marks them as such. dev_physworld is a test map.
_EXCLUDED_MAP_PATTERN = "(tutorial|^dev_physworld$)"

def _mapcycle_file(ctx):
    output_file = ctx.actions.declare_file("{}.txt".format(ctx.label.name))

    ctx.actions.run_shell(
        inputs = ctx.files.map_archives,
        outputs = [output_file],
        command = """
set -euo pipefail
for archive in "$@"; do
    tar tf "$archive"
done |
    sed -n -E 's|^.*/hidden/maps/([^/]+)\\.bsp$|\\1|p' |
    grep -vE '{excluded}' |
    sort -u >'{output}'
test -s '{output}'
""".format(
            excluded = _EXCLUDED_MAP_PATTERN,
            output = output_file.path,
        ),
        arguments = [f.path for f in ctx.files.map_archives],
        mnemonic = "MapCycle",
        progress_message = "Generating mapcycle %{label}",
    )
    return [DefaultInfo(files = depset([output_file]))]

mapcycle_file = rule(
    implementation = _mapcycle_file,
    doc = "Writes a mapcycle.txt naming every level found under hidden/maps/ in `map_archives`.",
    attrs = {
        "map_archives": attr.label_list(
            allow_files = True,
            mandatory = True,
            doc = "Tar archives to scan for `hidden/maps/*.bsp` entries.",
        ),
    },
)
