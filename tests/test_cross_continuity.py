"""Continuity cross-implementation proof (in Rust).

A Rust-minted twin-only genesis + chain must verify under the Python
``ContinuityChain`` byte-exactly (the slug re-derivation is the byte-exactness
oracle) and pass the shelf intake; a Python-minted chain must verify
under the Rust side. Skips cleanly without a Rust toolchain.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from fastatp import crypto
from fastatp.did.continuity import ContinuityChain
from fastatp.did.document import DIDDocument
from fastatp.server.did_server import SHELF_INTAKE_PATH, DIDServer

REPO_ROOT = Path(__file__).resolve().parent.parent
RUST_DIR = REPO_ROOT / "rust"
CARGO = shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")

if not (Path(CARGO).exists() and RUST_DIR.exists()):
    pytest.skip("rust toolchain/workspace not present", allow_module_level=True)

DOMAIN = "shelf.example"


@pytest.fixture(scope="session")
def cross_bin() -> Path:
    env = dict(os.environ, PATH=f"{Path(CARGO).parent}:{os.environ.get('PATH', '')}")
    subprocess.run(
        [CARGO, "build", "-q", "-p", "fastatp-cross"],
        cwd=RUST_DIR,
        env=env,
        check=True,
        timeout=600,
    )
    return RUST_DIR / "target" / "debug" / "fastatp-cross"


def _run(cross_bin: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(cross_bin), *args], capture_output=True, text=True, timeout=60)


@pytest.fixture()
def rust_genesis(cross_bin, tmp_path) -> dict:
    result = _run(cross_bin, "make-genesis", str(tmp_path), DOMAIN)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["dir"] = tmp_path
    return manifest


# --- direction 1: Rust-minted chains verified by Python --------------------------


def test_rust_genesis_verifies_under_python(rust_genesis):
    chain = ContinuityChain.from_json(json.loads((rust_genesis["dir"] / "chain.json").read_text()))
    chain.verify()  # slug re-derivation = the byte-exactness oracle
    assert chain.slug == rust_genesis["slug"]
    assert chain.twin_did == rust_genesis["twin_did"]
    assert chain.genesis.also_known_as == []  # twin-only


def test_rust_rotated_chain_verifies_under_python(rust_genesis):
    chain = ContinuityChain.from_json(json.loads((rust_genesis["dir"] / "chain2.json").read_text()))
    chain.verify()
    assert len(chain.pages) == 2
    genesis_key = chain.genesis.get_verification_method().public_key_multibase
    head_key = chain.head.get_verification_method().public_key_multibase
    assert genesis_key != head_key  # page two rotated the signing key


async def test_rust_chain_passes_the_shelf_intake(rust_genesis):
    """Connection zero's identity half, cross-implementation: a chain minted
    by the Rust core is accepted by the shelf and served back."""
    store: dict[str, dict] = {}

    async def resolver(slug: str) -> DIDDocument | None:
        entry = store.get(slug)
        if entry is None:
            return None
        return DIDDocument.from_json_dict(entry["chain"][-1]["document"])

    async def chain_resolver(slug: str):
        return store.get(slug)

    server = DIDServer(
        DOMAIN,
        resolver,
        chain_resolver=chain_resolver,
        shelf_store=lambda slug, chain: store.__setitem__(slug, chain.to_json()),
    )
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{DOMAIN}") as client:
        payload = json.loads((rust_genesis["dir"] / "chain.json").read_text())
        response = await client.post(SHELF_INTAKE_PATH, json=payload)
        assert response.status_code == 201, response.text
        assert response.json()["twin"] == rust_genesis["twin_did"]

        served = await client.get(f"/{rust_genesis['slug']}/did.json")
        assert served.status_code == 200
        assert served.json()["id"] == rust_genesis["twin_did"]

        # rotation arrives as a LONGER chain — also minted by Rust
        rotated = json.loads((rust_genesis["dir"] / "chain2.json").read_text())
        assert (await client.post(SHELF_INTAKE_PATH, json=rotated)).status_code == 201


# --- direction 2: Python-minted chains verified by Rust --------------------------


def _python_chain() -> ContinuityChain:
    signing = crypto.generate_keypair("p256")
    rotation = crypto.generate_keypair("k256")
    rotation_did = crypto.encode_did_key(rotation.public_key())
    return ContinuityChain.create(DOMAIN, signing.public_key(), [rotation_did], bind_pretty=False)


def test_python_chain_verifies_under_rust(cross_bin, tmp_path):
    chain = _python_chain()
    path = tmp_path / "py-chain.json"
    path.write_text(json.dumps(chain.to_json()))

    result = _run(cross_bin, "verify-chain", str(path))
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["twin"] == chain.twin_did
    assert report["slug"] == chain.slug


def test_tampered_python_chain_is_refused_by_rust(cross_bin, tmp_path):
    chain = _python_chain()
    payload = chain.to_json()
    payload["chain"][0]["document"]["rotationKeys"] = [
        crypto.encode_did_key(crypto.generate_keypair("k256").public_key())
    ]
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload))

    result = _run(cross_bin, "verify-chain", str(path))
    assert result.returncode != 0
    assert "refused" in result.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
