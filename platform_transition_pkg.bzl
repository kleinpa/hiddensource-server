"""platform_transition_pkg_filegroup — like aspect_bazel_lib's
platform_transition_filegroup, but for a rules_pkg source.

aspect_bazel_lib's own version (@aspect_bazel_lib//lib:transitions.bzl) only
forwards DefaultInfo from its transitioned srcs, which is enough for a
filegroup of plain files but silently drops PackageFilesInfo/
PackageFilegroupInfo -- the providers pkg_tar actually reads to place a
file at anything other than its own raw path. Since @rules_sourcemod's
presets (e.g. sourcemod_ep1) are pkg_filegroups, not plain filegroups, that
loss flattens every file in them to this package's root under its raw,
unrenamed cc_binary output name once fed through the aspect_bazel_lib rule
and into pkg_tar -- no error, just silently wrong. This rule exists to
carry those providers through the same platform transition.
"""

load("@rules_pkg//pkg:providers.bzl", "PackageFilegroupInfo", "PackageFilesInfo")

def _transition_platform_impl(_, attr):
    return {"//command_line_option:platforms": str(attr.target_platform)}

_transition_platform = transition(
    implementation = _transition_platform_impl,
    inputs = [],
    outputs = ["//command_line_option:platforms"],
)

def _platform_transition_pkg_filegroup_impl(ctx):
    # A single-label attr under a transition still comes back as a
    # (length-1) list -- the transition mechanism doesn't know statically
    # that this particular transition is 1:1.
    src = ctx.attr.src[0]
    providers = [src[DefaultInfo]]
    if PackageFilesInfo in src:
        providers.append(src[PackageFilesInfo])
    if PackageFilegroupInfo in src:
        providers.append(src[PackageFilegroupInfo])
    return providers

platform_transition_pkg_filegroup = rule(
    implementation = _platform_transition_pkg_filegroup_impl,
    attrs = {
        "_allowlist_function_transition": attr.label(
            default = "@bazel_tools//tools/allowlists/function_transition_allowlist",
        ),
        "target_platform": attr.label(
            doc = "The target platform to transition src to.",
            mandatory = True,
        ),
        "src": attr.label(
            cfg = _transition_platform,
            doc = "A pkg_files/pkg_filegroup target to transition to target_platform.",
            mandatory = True,
        ),
    },
    doc = ("Transitions a single rules_pkg source (pkg_files/pkg_filegroup) " +
           "to target_platform, forwarding its PackageFilesInfo/" +
           "PackageFilegroupInfo so a downstream pkg_tar still sees where " +
           "its contents install."),
)
