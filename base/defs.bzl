"""The base system's layers, in the order the image stacks them.

Where counterstrikesource-server and cs2-server name each Debian package as its
own layer -- a handful of them, so that a libc bump re-pushes one small layer --
this one takes rules_distroless's `:flat` target, which is every installed
package and the dpkg status file merged into a single tar.

That is a deliberate trade, not laziness. The closure here is around 170
packages; enumerating them would put a generated list in a hand-maintained file
and would have to be regenerated every time wine's dependencies shift, which is
the sort of thing that goes stale silently. And the granularity would buy
nothing: nothing in this image bumps one of wine's transitive dependencies
without bumping the snapshot date, at which point every layer changes anyway.
"""

BASE_LAYERS = [
    "@bookworm//:flat",
    "//base:base_files_layer",
]
