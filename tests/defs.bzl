"""Macro for black-box testing a container image by running it."""

load("@rules_python//python:defs.bzl", "py_test")

def srcds_container_test(name, src, image, repo_tag, deps = [], **kwargs):
    """Runs a Python test against a live container started from `image`.

    The test is handed the image through the environment rather than as an
    argument so that the harness in //tests:srcds can start as many containers
    as a test case needs.

    Args:
        name: Name of the test target.
        src: The Python test source file.
        image: An `oci_load` target that imports the image under test.
        repo_tag: The tag `image` applies, i.e. one of its `repo_tags`.
        deps: Extra Python dependencies of `src`.
        **kwargs: Passed through to the underlying `py_test`.
    """
    py_test(
        name = name,
        srcs = [src],
        main = src,
        data = [image],
        deps = deps + ["//tests:srcds"],
        env = {
            "SRCDS_IMAGE_LOADER": "$(rlocationpath {})".format(image),
            "SRCDS_REPO_TAG": repo_tag,
        },
        tags = kwargs.pop("tags", []) + [
            # Talks to the host's container daemon and pulls nothing, but the
            # sandbox would hide the daemon socket and the test needs to reach
            # the container's published port.
            "no-sandbox",
            "requires-network",
            # Starting a 3 GB game server is not something to do in parallel
            # with dozens of other actions.
            "exclusive",
        ],
        **kwargs
    )
