# LAN of DOOM The Hidden: Source Server

Docker image for a preconfigured The Hidden: Source server as used by the LAN
of DOOM.

# Installation

Run `docker pull ghcr.io/kleinpa/hiddensource-server:latest`

# Installed Addons

- LAN of DOOM Authenticate by Steam Group
- MetaMod:Source
- SourceMod

# Configuration

The image runs the dedicated server under Wine via `srcds_headless.exe`,
streaming console output to container `stdout` and forwarding `cmd` arguments
directly to the engine. Configure it with command-line arguments and config
files, the same way `srcds.exe` itself is configured.

The image's entrypoint sets `-console`, `-game hidden`, `-strictbindport`, and
`+ip 0.0.0.0`. Override `cmd` (e.g. Kubernetes `args:`) to pass map and convar
settings:

```yaml
args:
  ["+map", "hdn_docks", "+hostname", "LAN of DOOM", "+sv_password", "hunter2"]
```

Pass convar values containing spaces as plain single elements (no inner quotes);
Wine's argv-to-Win32 translation adds the quoting `srcds.exe` expects.

Config files live in `/opt/game/hidden/cfg`; mount a ConfigMap or Secret over a
file there (e.g. `server.cfg`) to override it.

# Development

Build the image and load it into the local Docker daemon:

```sh
bazel run //:image_tarball
```

Run the test suite (starts the image in Docker and queries it over A2S and
RCON):

```sh
bazel test //...
```
