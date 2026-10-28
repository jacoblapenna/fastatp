"""External oracle: Bluesky's `goat` verifies our firehose.

`goat firehose --verify-basic --verify-mst` runs the network's own event
validation — including inductive/inversion MST verification — against a live
local PeerServer. This is the same code path a relay exercises.

Dev-only: skips cleanly when the Go-built `goat` binary is absent (plain-pip
installs stay green — the same rule as the Node harness).
Signature verification (`--verify-sig`) is deliberately not enabled here: it
requires public DID resolution, which a loopback did:web cannot offer; the
signature path is covered by RepoMirror tests against the same frames.
"""

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from fastatp import Peer, PeerServer, crypto
from fastatp.repo.repo import WriteOp

GOAT = shutil.which("goat") or str(Path.home() / "go" / "bin" / "goat")

if not Path(GOAT).exists():
    pytest.skip("goat (Go) binary not present", allow_module_level=True)

DID = "did:web:goat-oracle.example"
NSID = "com.example.goat.item"


def _note(i: int) -> dict[str, Any]:
    return {"$type": NSID, "n": i, "text": f"item {i}"}


def _goat_stderr_problems(stderr: str) -> list[str]:
    """goat logs verification failures at WARN/ERROR ("failed to invert
    commit MST", signature failures, parse errors); a clean run has none."""
    return [
        line
        for line in stderr.splitlines()
        if '"level":"WARN"' in line or '"level":"ERROR"' in line or "failed to invert" in line
    ]


async def test_goat_verifies_full_stream(serve_app):
    srv = PeerServer(
        Peer(did=DID, signing_key=crypto.generate_keypair("k256")), backfill_window=4096
    )
    async with serve_app(srv.app) as port:
        proc = await asyncio.create_subprocess_exec(
            GOAT,
            "firehose",
            "--relay-host",
            f"ws://127.0.0.1:{port}",
            "--cursor",
            "0",
            "--verify-basic",
            "--verify-mst",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.sleep(1.0)  # goat connected (cursor 0 replays anyway)
            # a workload with every op shape, deep enough that deletes/updates
            # have MST neighbors in untouched nodes
            for i in range(60):
                srv.put_record(NSID, f"k{i:03d}", _note(i))
            for i in (0, 13, 27, 41, 59):
                srv.delete_record(NSID, f"k{i:03d}")
            for i in (1, 30, 58):
                srv.put_record(NSID, f"k{i:03d}", _note(1000 + i))
            srv.apply_writes(
                [
                    WriteOp("create", NSID, "zz-batch", _note(-1)),
                    WriteOp("update", NSID, "k002", _note(-2)),
                    WriteOp("delete", NSID, "k003"),
                ]
            )
            expected_commits = 60 + 5 + 3 + 1
            expected_events = expected_commits + 2  # + #identity + #account

            async def read_events() -> list[dict[str, Any]]:
                events: list[dict[str, Any]] = []
                assert proc.stdout is not None
                while len(events) < expected_events:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    events.append(json.loads(line))
                return events

            events = await asyncio.wait_for(read_events(), timeout=30)
        finally:
            proc.terminate()
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=10)

        stderr = err.decode(errors="replace")
        assert _goat_stderr_problems(stderr) == []
        assert len(events) == expected_events, stderr
        kinds = [e.get("type") for e in events]
        assert kinds[0] == "identity" and kinds[1] == "account"
        assert kinds.count("commit") == expected_commits
        assert events[1]["payload"]["active"] is True
        for e in events:
            assert e["payload"].get("did", e["payload"].get("repo")) == DID


async def test_goat_inverts_in_isolation_mid_stream(serve_app):
    """The strict-relay case: goat joins MID-stream (no accumulated repo
    state), so verifying each commit requires true operation inversion over
    the event's own blocks — the exact path the covering proofs exist for.
    (Without covering proofs goat logs "failed to invert commit MST ...
    partial MST" here.)"""
    srv = PeerServer(
        Peer(did=DID, signing_key=crypto.generate_keypair("k256")), backfill_window=4096
    )
    for i in range(60):
        srv.put_record(NSID, f"k{i:03d}", _note(i))
    cursor = srv.seq  # goat starts here: sees only the deletes/updates below
    async with serve_app(srv.app) as port:
        proc = await asyncio.create_subprocess_exec(
            GOAT,
            "--log-level",
            "debug",
            "firehose",
            "--relay-host",
            f"ws://127.0.0.1:{port}",
            "--cursor",
            str(cursor),
            "--verify-basic",
            "--verify-mst",
            "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.sleep(1.0)
            for i in (0, 13, 27, 41, 59):
                srv.delete_record(NSID, f"k{i:03d}")
            for i in (1, 30):
                srv.put_record(NSID, f"k{i:03d}", _note(1000 + i))

            async def wait_verified() -> str:
                assert proc.stderr is not None
                seen = ""
                while seen.count("prevData matched") < 7:
                    chunk = await proc.stderr.read(4096)
                    if not chunk:
                        break
                    seen += chunk.decode(errors="replace")
                return seen

            stderr = await asyncio.wait_for(wait_verified(), timeout=30)
        finally:
            proc.terminate()
            _out, err = await asyncio.wait_for(proc.communicate(), timeout=10)
        stderr += err.decode(errors="replace")
        assert _goat_stderr_problems(stderr) == [], stderr
        assert stderr.count("prevData matched") == 7  # every op inverted exactly
