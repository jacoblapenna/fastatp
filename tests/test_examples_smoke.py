"""Smoke runs for the examples no other test exercises.

Examples are how people learn the library, so they must not rot silently.
These checks only assert that each example runs to completion (exit 0); what
an example demonstrates is its own business, not a test expectation.

Already covered elsewhere: board_rendezvous (test_board_rendezvous),
hosted_peer (test_hosted_peer_e2e), the bluesky_peer pipeline
(test_bluesky_peer_pipeline) and sensorthings_interop
(test_sensorthings_interop).

Nothing here touches the network or needs root: every example below is
self-contained by design.
"""

import shutil
import subprocess
import sys

import pytest

from tests.conftest import REPO_ROOT

EXAMPLES = REPO_ROOT / "examples"

# self-contained scripts: `python examples/<name>.py` with no arguments
STANDALONE = ["two_peers.py", "workorder.py", "wireguard_authz.py"]


@pytest.mark.parametrize("script", STANDALONE)
def test_standalone_example_runs(script, tmp_path):
    # run from an empty directory so an example can't lean on the repo's cwd
    proc = subprocess.run(
        [sys.executable, str(EXAMPLES / script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"{script} exited {proc.returncode}:\n{proc.stderr[-2000:]}"


def test_signup_recipe_is_valid_javascript():
    """signup.example.js is a recipe, not a runnable module: it needs a real
    OAuth sign-in at the user's own host, so it can't run here. Node's syntax
    check at least keeps it from rotting into invalid JavaScript."""
    node = shutil.which("node")
    # fail, never skip: the check is cheap and node is on every CI runner
    assert node, "node not found on PATH (needed to syntax-check the JS recipe)"
    proc = subprocess.run(
        [node, "--check", str(EXAMPLES / "bluesky_signup" / "signup.example.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
