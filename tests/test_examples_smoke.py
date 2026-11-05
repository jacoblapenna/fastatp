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

import http.server
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

import httpx
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


# ---- the operator runbook: bluesky_peer + hygiene, end to end, offline ----

DOMAIN = "alice.example.com"
PDS = "https://peer.example.com"


def _run_example(path, *args, cwd):
    # plain `python <script>` (not -I): the bluesky_peer scripts import their
    # sibling records.py from the script's own directory, as a user's run does
    proc = subprocess.run(
        [sys.executable, str(EXAMPLES / path), *map(str, args)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc


def _ok(proc, what):
    assert proc.returncode == 0, f"{what} exited {proc.returncode}:\n{proc.stderr[-2000:]}"
    return proc.stdout


def _rotation_pen(bootstrap_stdout: str) -> str:
    """bootstrap_identity.py prints the rotation pen once, on the line after
    its 'ROTATION PEN' banner, so the operator can store it cold."""
    lines = bootstrap_stdout.splitlines()
    i = next(n for n, line in enumerate(lines) if "ROTATION PEN" in line)
    pen = lines[i + 1].strip()
    assert re.fullmatch(r"[0-9a-f]{64}", pen), f"unexpected pen line: {pen!r}"
    return pen


def test_operator_runbook(tmp_path):
    """Mint an identity, post, back up, restore into a fresh state dir,
    rotate the signing pen there, and post again under the new pen. A wrong
    rotation pen is refused without changing anything."""
    state, static = tmp_path / "state", tmp_path / "static"
    out = _ok(
        _run_example(
            "bluesky_peer/bootstrap_identity.py",
            "--domain",
            DOMAIN,
            "--pds",
            PDS,
            "--state",
            state,
            "--out",
            static,
            cwd=tmp_path,
        ),
        "bootstrap_identity",
    )
    pen_file = tmp_path / "pen.hex"
    pen_file.write_text(_rotation_pen(out) + "\n")
    pen_file.chmod(0o600)

    post = ("bluesky_peer/post.py", "--domain", DOMAIN)
    _ok(
        _run_example(*post, "--state", state, "--profile", "Alice", "--text", "hi", cwd=tmp_path),
        "post",
    )
    _ok(
        _run_example(
            "hygiene/backup.py", "--state", state, "--dest", tmp_path / "bk", cwd=tmp_path
        ),
        "backup",
    )
    _ok(
        _run_example(
            "hygiene/backup.py",
            "--state",
            state,
            "--dest",
            tmp_path / "bk-nosecrets",
            "--exclude-secrets",
            cwd=tmp_path,
        ),
        "backup --exclude-secrets",
    )
    assert not (tmp_path / "bk-nosecrets" / "signing.key").exists()

    restored = tmp_path / "restored"
    _ok(
        _run_example(
            "hygiene/restore.py", "--backup", tmp_path / "bk", "--state", restored, cwd=tmp_path
        ),
        "restore",
    )

    rotate = ("hygiene/rotate.py", "--state", restored, "--pds", PDS)
    wrong = tmp_path / "wrong.hex"
    wrong.write_text("11" * 32 + "\n")
    refused = _run_example(*rotate, "--out", tmp_path / "s-bad", "--pen-file", wrong, cwd=tmp_path)
    assert refused.returncode != 0, "rotate accepted a pen that is not a rotationKey"
    assert "nothing changed" in (refused.stdout + refused.stderr)

    key_before = (restored / "signing.key").read_bytes()
    _ok(
        _run_example(*rotate, "--out", tmp_path / "s2", "--pen-file", pen_file, cwd=tmp_path),
        "rotate",
    )
    assert (restored / "signing.key").read_bytes() != key_before, "signing pen did not change"
    _ok(_run_example(*post, "--state", restored, "--text", "after rotation", cwd=tmp_path), "post")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_peer_serves_the_repo(tmp_path):
    """peer.py is a long-running server: start it, read the repo head over
    the sync API, stop it."""
    state = tmp_path / "state"
    _ok(
        _run_example(
            "bluesky_peer/bootstrap_identity.py",
            "--domain",
            DOMAIN,
            "--pds",
            PDS,
            "--state",
            state,
            "--out",
            tmp_path / "static",
            cwd=tmp_path,
        ),
        "bootstrap_identity",
    )
    head = (state / "repo_head").read_text().strip()

    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(EXAMPLES / "bluesky_peer/peer.py"),
            "--domain",
            DOMAIN,
            "--state",
            str(state),
            "--port",
            str(port),
        ],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        url = f"http://127.0.0.1:{port}/xrpc/com.atproto.sync.getLatestCommit"
        deadline = time.monotonic() + 30
        while True:
            try:
                r = httpx.get(url, params={"did": f"did:web:{DOMAIN}"}, timeout=2)
                break
            except httpx.TransportError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    pytest.fail(f"peer.py never served (rc={proc.poll()}):\n{proc.stdout.read()}")
                time.sleep(0.2)
        assert r.status_code == 200, r.text
        assert r.json()["cid"] == head
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_request_crawl_posts_to_the_chosen_relay(tmp_path):
    """request_crawl.py has no default relay (the anti-registry rule): it
    contacts exactly the one you name. Name a stand-in on loopback and check
    it received the requestCrawl, so nothing reaches a real relay."""
    received = []

    class Relay(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("content-length", 0)))
            received.append((self.path, json.loads(body)))
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Relay)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        relay = f"http://127.0.0.1:{server.server_address[1]}"
        _ok(
            _run_example(
                "bluesky_peer/request_crawl.py",
                "--relay",
                relay,
                "--hostname",
                "peer.example.com",
                cwd=tmp_path,
            ),
            "request_crawl",
        )
    finally:
        server.shutdown()
        server.server_close()
    assert received == [("/xrpc/com.atproto.sync.requestCrawl", {"hostname": "peer.example.com"})]
