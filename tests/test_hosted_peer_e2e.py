"""The hosted-peer capability, end to end.

The shipped wasm bundle (driven under Node 22 — same artifact, same WebCrypto)
runs connection zero against an example builder composed ONLY of shipped
primitives, posts pen-signed state through the example router, delegates a
second device pen, syncs through the browser gate, and is locked out at the
next connect after the delegation is deleted. Every hop verifies; no secret
ever crosses the wire.
"""

import asyncio
import binascii
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from fastatp import crypto
from fastatp.authz.capability import live_capabilities
from fastatp.authz.delegation import Delegation, live_delegations
from fastatp.repo.repo import Repo

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "fastatp" / "browser" / "static" / "fastatp_wasm_bg.wasm"
CLIENT = REPO_ROOT / "examples" / "hosted_peer" / "client.mjs"
NODE = shutil.which("node") or ""
DOMAIN = "shelf.example"

if not NODE:
    pytest.skip("node not available", allow_module_level=True)
if not STATIC.exists():
    pytest.skip("wasm bundle not built (scripts/build-wasm.sh)", allow_module_level=True)

_spec = importlib.util.spec_from_file_location(
    "hosted_peer_builder", REPO_ROOT / "examples" / "hosted_peer" / "builder.py"
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load examples/hosted_peer/builder.py")
hosted_peer_builder = importlib.util.module_from_spec(_spec)
# dataclasses resolve annotations via sys.modules[cls.__module__]
sys.modules["hosted_peer_builder"] = hosted_peer_builder
_spec.loader.exec_module(hosted_peer_builder)


async def test_hosted_peer_end_to_end(serve_app, tmp_path):
    builder = hosted_peer_builder.make_builder(DOMAIN)

    async with (
        serve_app(builder.shelf.app) as shelf_port,
        serve_app(builder.peers.app) as peer_port,
    ):
        proc = await asyncio.create_subprocess_exec(
            NODE,
            str(CLIENT),
            f"http://127.0.0.1:{peer_port}",
            f"http://127.0.0.1:{shelf_port}",
            str(tmp_path),
            DOMAIN,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        assert proc.returncode == 0, f"client failed:\n{err.decode()}\n{out.decode()}"

    report = json.loads((tmp_path / "report.json").read_text())
    phases = report["phases"]
    twin = report["twin_did"]

    # connection zero: a twin-only slug DID minted client-side, shelved (201),
    # and its genesis repo hosted gated (201) — shelf rental, never an account
    assert twin.startswith(f"did:web:{DOMAIN}:")
    assert phases["shelf-put-chain"] == 201
    assert phases["signup"] == 201
    assert twin in builder.handles

    # the pen-signed push was verified against the shelved chain head and
    # re-hosted (the router can never forge, only withhold)
    assert phases["push-1"] == 200

    # device 2, delegated by a record PRESENT in the hosted repo, passes the
    # signed-challenge gate and syncs the full repo (multi-device sync)
    assert phases["sync-device2"] == 200
    synced = Repo(twin, crypto.generate_keypair("k256"))
    synced.import_car(
        Path(report["files"]["device2_sync_car"]).read_bytes(),
        verify_key=builder.head_key(twin),
    )
    note = synced.get_record("com.example.hosted.note", "note-1")
    assert note is not None and note["text"] == "hello hosted world"
    assert report["pen2_did"] in live_delegations(synced)

    # a stranger pen with a perfectly valid challenge is refused: identity
    # comes from the delegation chain, not from holding a signature
    assert phases["sync-stranger"] == 403

    # severance: the delegation record was DELETED and the new state pushed;
    # device 2's next connect — fresh, valid challenge — is refused.
    # Absence IS revocation.
    assert phases["push-severance"] == 200
    assert phases["sync-device2-severed"] == 403
    hosted = builder.handles[twin].repo
    assert live_delegations(hosted) == {}
    assert twin in live_capabilities(hosted)  # the self-capability survives

    # no secret ever crossed the wire: every body the client sent is on disk;
    # none contains the paper-root secret (hex or raw), and the pen is
    # non-extractable so there is nothing else to leak
    secret_hex = report["rotation_secret"].encode("ascii")
    secret_raw = binascii.unhexlify(report["rotation_secret"])
    bodies = sorted((tmp_path / "bodies").glob("*.bin"))
    assert len(bodies) >= 4  # chain, signup CAR, two pushes
    for body in bodies:
        data = body.read_bytes()
        assert secret_hex not in data, f"paper secret leaked in {body.name}"
        assert secret_raw not in data, f"paper secret leaked (raw) in {body.name}"

    # the delegation lexicon the flow relied on is the shipped one
    assert Delegation.nsid == "dev.fastatp.auth.delegation"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
