"""Cross-implementation round-trips.

The Rust core and the Python package must verify each other's bytes: a
Rust-produced repo CAR and record proof verify under Python ``Peer.verify()``
/ ``Repo.import_car()``, and Python-produced artifacts verify under Rust.
Identities are ``did:key`` throughout — both sides short-circuit resolution.

Skips cleanly when no Rust toolchain is present (plain-pip installs stay
green; the toolchain is a contributor concern).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from fastatp import Peer, crypto
from fastatp.exceptions import FastATPError
from fastatp.lexicon import Lexicon
from fastatp.repo.repo import Repo

REPO_ROOT = Path(__file__).resolve().parent.parent
RUST_DIR = REPO_ROOT / "rust"
CARGO = shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")

if not (Path(CARGO).exists() and RUST_DIR.exists()):
    pytest.skip("rust toolchain/workspace not present", allow_module_level=True)


class CrossNote(Lexicon):
    nsid = "com.example.crossNote"

    text: str
    n: int


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


def _rust_artifacts(cross_bin: Path, out_dir: Path, curve: str = "k256") -> dict:
    record = json.dumps({"$type": CrossNote.nsid, "text": "from rust", "n": 7})
    result = _run(cross_bin, "make-proof", str(out_dir), curve, CrossNote.nsid, "note-1", record)
    assert result.returncode == 0, result.stderr
    return json.loads((out_dir / "manifest.json").read_text())


# --- direction 1: Rust bytes verified by Python ---------------------------------


@pytest.mark.parametrize("curve", ["k256", "p256"])
async def test_rust_proof_verifies_under_python_peer(cross_bin, tmp_path, curve):
    manifest = _rust_artifacts(cross_bin, tmp_path, curve)
    proof = (tmp_path / "proof.car").read_bytes()

    record = await Peer().verify(proof, expected_signer=manifest["did"])
    assert isinstance(record, CrossNote)
    assert record.text == "from rust" and record.n == 7


async def test_tampered_rust_proof_is_refused_by_python(cross_bin, tmp_path):
    manifest = _rust_artifacts(cross_bin, tmp_path)
    proof = bytearray((tmp_path / "proof.car").read_bytes())
    proof[-1] ^= 0x01  # flip one bit in the record block
    with pytest.raises(FastATPError):
        await Peer().verify(bytes(proof), expected_signer=manifest["did"])


def test_rust_repo_car_imports_into_python(cross_bin, tmp_path):
    manifest = _rust_artifacts(cross_bin, tmp_path)
    verify_key = crypto.decode_multikey(manifest["multikey"])

    target = Repo(manifest["did"], crypto.generate_keypair("k256"))
    target.import_car((tmp_path / "repo.car").read_bytes(), verify_key=verify_key)
    record = target.get_record(CrossNote.nsid, "note-1")
    assert record is not None and record["text"] == "from rust"
    assert target.rev == manifest["rev"]


# --- direction 2: Python bytes verified by Rust ---------------------------------


def _python_repo(curve: str = "k256") -> tuple[Repo, str, str]:
    key = crypto.generate_keypair(curve)
    did = crypto.encode_did_key(key.public_key())
    repo = Repo(did, key)
    for i in range(4):
        repo.put_record("com.example.filler", f"fill-{i}", {"$type": "com.example.filler", "n": i})
    repo.put_record(CrossNote.nsid, "note-1", CrossNote(text="from python", n=11))
    return repo, did, crypto.encode_multikey(key.public_key())


@pytest.mark.parametrize("curve", ["k256", "p256"])
def test_python_proof_verifies_under_rust(cross_bin, tmp_path, curve):
    repo, did, _multikey = _python_repo(curve)
    proof_path = tmp_path / "py-proof.car"
    proof_path.write_bytes(repo.prove(CrossNote.nsid, "note-1"))

    result = _run(cross_bin, "verify-proof", str(proof_path), did)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["did"] == did
    assert report["collection"] == CrossNote.nsid and report["rkey"] == "note-1"
    assert report["record"]["text"] == "from python" and report["record"]["n"] == 11


def test_tampered_python_proof_is_refused_by_rust(cross_bin, tmp_path):
    repo, did, _multikey = _python_repo()
    proof = bytearray(repo.prove(CrossNote.nsid, "note-1"))
    proof[-1] ^= 0x01
    proof_path = tmp_path / "tampered.car"
    proof_path.write_bytes(bytes(proof))

    result = _run(cross_bin, "verify-proof", str(proof_path), did)
    assert result.returncode != 0
    assert "refused" in result.stderr or "fastatp-cross" in result.stderr


def test_wrong_key_is_refused_by_rust(cross_bin, tmp_path):
    repo, _did, _multikey = _python_repo()
    proof_path = tmp_path / "py-proof.car"
    proof_path.write_bytes(repo.prove(CrossNote.nsid, "note-1"))
    stranger = crypto.encode_did_key(crypto.generate_keypair("k256").public_key())

    result = _run(cross_bin, "verify-proof", str(proof_path), stranger)
    assert result.returncode != 0


def test_python_repo_car_imports_into_rust(cross_bin, tmp_path):
    repo, did, _multikey = _python_repo()
    car_path = tmp_path / "py-repo.car"
    car_path.write_bytes(repo.export_car())

    result = _run(cross_bin, "import-car", str(car_path), did)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["did"] == did
    assert report["records"] == 5
    assert report["rev"] == repo.rev


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
