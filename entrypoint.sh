#!/bin/bash -ue
#
# Starts an X server, then hands off to srcds.exe under wine.
#
# The counterstrikesource-server and cs2-server images have no entrypoint script
# on purpose: the binary is the entrypoint, the arguments are the configuration,
# and a deployment overrides `cmd`. This image cannot do that, for one reason:
#
#   srcds.exe does not run headless. Started without a display it fails in
#   CTextConsoleWin32::GetLine with `!GetNumberOfConsoleInputEvents` and never
#   binds its port. That was measured against this exact build, both directly
#   and through `xvfb-run`; neither works. So something has to start Xvfb and
#   then start wine, and one process cannot do both.
#
# Everything else that used to live here is gone. In particular this script no
# longer installs wine: `apt-get update && apt-get install -y wine xvfb` ran on
# every container start, needed the Debian archive reachable from wherever the
# server was deployed, and pulled a different set of bytes each time. wine is in
# the image now -- see //base:bookworm.yaml. The mapcycle is generated at build
# time by //:mapcycle rather than by `ls | sed` here, and the SourceMod config
# files this used to `touch` are shipped as real (empty) files by //:config_layer.
#
# Arguments to this script are passed through to srcds.exe, which is what makes
# the image's `cmd` the configuration surface it is everywhere else.

# Configuration a deployment overrides by replacing the image's cmd.
HIDDEN_PORT="${HIDDEN_PORT:-27015}"

# wine writes to its prefix on every start, so it cannot live in the read-only
# image. It is created on first use; that costs a few seconds at boot.
export WINEPREFIX="${WINEPREFIX:-/tmp/wine}"

# wine's debug channels are noisy enough to bury the server's own output, and
# the server's output is the only thing anyone reads out of this container.
export WINEDEBUG="${WINEDEBUG:--all}"

# Forces the real msvcp140.dll (//:msvcp140_layer, shipped next to srcds.exe)
# to win over wine's own incomplete builtin -- see that layer's comment in
# BUILD.bazel. Already set by //:image's own env, this default only matters
# if something invokes this script with a stripped-down environment.
export WINEDLLOVERRIDES="${WINEDLLOVERRIDES:-msvcp140=n}"

# Source's console code asks for a terminal type before it has one.
export TERM="${TERM:-xterm}"

# Xvfb exists only to satisfy the check above; nothing is ever drawn to it, so
# the geometry is the smallest thing that works rather than a considered choice.
Xvfb :99 -screen 0 800x600x16 &
XVFB_PID=$!
export DISPLAY=:99.0

# Stop the X server when the server exits, so a crashed srcds does not leave the
# container alive and apparently healthy with nothing listening.
trap 'kill "${XVFB_PID}" 2>/dev/null || true' EXIT

# Wait for the display rather than sleeping a fixed second and hoping.
for _ in {1..50}; do
    [ -e /tmp/.X11-unix/X99 ] && break
    sleep 0.1
done

cd /opt/game

# `wine start /wait` rather than `wine srcds.exe`: the server needs a Win32
# console object, which it gets from `start` and not from being handed wine's
# stdio.
#
# Console output does NOT reach this container's stdout on its own, though --
# confirmed by hand against both forms, run the same way `docker run -d`
# actually runs a container (no tty attached): the only thing `docker logs`
# ever shows is wine's own one-time WINEPREFIX-creation message, nothing from
# srcds.exe itself, crash or no crash. wine's own console emulation writes
# through a Win32 console object of its own, which is not the same file
# descriptor as this process's stdout, and nothing bridges the two by
# default. -condebug plus tailing the log file it writes is: the engine
# mirrors everything it would otherwise only send to that console object into
# addons/../hidden/console.log (relative to -game, so
# hidden/console.log here), and a `tail -f` on that file, started before wine
# so it's already reading when the file's first line lands, inherits this
# script's own stdout -- the one fd `docker logs` actually captures.
#
# -strictbindport: refuse to silently drift to another port if ours is taken. A
# container that cannot bind its port should die and be rescheduled, not hide.
touch hidden/console.log
tail -f hidden/console.log &

exec wine start /wait srcds.exe \
    -console \
    -condebug \
    -game hidden \
    -port "${HIDDEN_PORT}" \
    -strictbindport \
    +ip 0.0.0.0 \
    "$@"
