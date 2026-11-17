"""The signer seam end to end: a repo whose pen is a handle, not key bytes.

A fake hardware signer stands in for a TPM: it returns high-S DER, the
worst-behaved encoding a device gives back. Everything above it (commit,
repo, server, mirror) must not be able to tell it from an in-memory key.
"""

import asyncio

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

from fastatp import Peer, PeerServer, Repo, RepoMirror, crypto
from fastatp.did.document import build_did_document
from fastatp.exceptions import CryptoError, RepoError
from fastatp.repo.sync import commit_frame_body, encode_frame, sync_frame_body

DID = "did:web:device.example"
NOTE = "com.example.note"


class _HardwareSigner:
    """Signs with a key the test holds but the repo never sees, and hands
    back DER with ``s`` forced into the upper half, the way a device that
    never normalizes would."""

    def __init__(self, key: ec.EllipticCurvePrivateKey) -> None:
        self._key = key
        self.calls = 0

    @property
    def public_key(self):
        return self._key.public_key()

    async def sign(self, message: bytes) -> bytes:
        self.calls += 1
        r, s = decode_dss_signature(self._key.sign(message, ec.ECDSA(hashes.SHA256())))
        n = crypto.curve_order(self._key.curve)
        if s <= n // 2:
            s = n - s
        return encode_dss_signature(r, s)


class _LyingSigner(_HardwareSigner):
    """Reports one key, signs with another."""

    def __init__(self, key, claimed):
        super().__init__(key)
        self._claimed = claimed

    @property
    def public_key(self):
        return self._claimed.public_key()


@pytest.mark.parametrize("curve", ["k256", "p256"])
async def test_a_hardware_signed_repo_mirrors_like_a_software_one(curve):
    signer = _HardwareSigner(crypto.generate_keypair(curve))
    doc = build_did_document(did=DID, signing_key=signer.public_key)

    async def resolver(did):
        return doc if did == DID else None

    srv = await PeerServer.create(Peer(did=DID, signing_key=signer))
    assert signer.calls == 1  # the genesis commit
    assert srv.repo.commit.verify(signer.public_key)
    assert len(srv.repo.commit.sig) == 64  # compact low-S on the wire, never DER

    mirror = RepoMirror(DID, resolver=resolver)
    event = await srv.put_record(NOTE, "one", {"$type": NOTE, "text": "from the chip"})
    sync = encode_frame(
        "#sync", sync_frame_body(DID, srv.seq, srv.repo.rev, srv.repo.export_car(), event.time)
    )
    assert await mirror.handle_frame(sync) == []
    assert mirror.rev == srv.repo.rev

    event2 = await srv.put_record(NOTE, "two", {"$type": NOTE, "text": "again"})
    records = await mirror.handle_frame(
        encode_frame("#commit", commit_frame_body(DID, srv.seq, event2))
    )
    assert records[0]["record"]["text"] == "again"
    assert mirror.get_record(NOTE, "two")["text"] == "again"
    assert signer.calls == 3


async def test_a_peer_creates_its_repo_through_the_seam():
    signer = _HardwareSigner(crypto.generate_keypair("p256"))
    peer = Peer(did=DID, signing_key=signer)
    assert peer.signer is signer
    repo = await peer.create_repo()
    assert repo.signer is signer
    assert repo.commit.verify(signer.public_key)
    # a plain key still works everywhere, wrapped on the way in
    key = crypto.generate_keypair("k256")
    assert isinstance(Peer(did=DID, signing_key=key).signer, crypto.SoftwareSigner)
    assert isinstance((await Repo.create(DID, key)).signer, crypto.SoftwareSigner)


async def test_a_misbehaving_signer_fails_at_the_write_and_changes_nothing():
    good = _HardwareSigner(crypto.generate_keypair("p256"))
    repo = await Repo.create(DID, good)
    await repo.put_record(NOTE, "kept", {"$type": NOTE, "text": "before"})
    head, entries = repo.commit_cid, dict(repo._entries)

    repo.signer = _LyingSigner(crypto.generate_keypair("p256"), crypto.generate_keypair("p256"))
    with pytest.raises(CryptoError, match="does not verify against its own public key"):
        await repo.put_record(NOTE, "lost", {"$type": NOTE, "text": "never"})
    assert repo.commit_cid == head
    assert repo._entries == entries
    assert repo.get_record(NOTE, "lost") is None
    assert repo.store.get(head) is not None  # the old head is still the head


async def test_a_fresh_repo_has_no_commit_until_create_signs_one():
    repo = Repo(DID, crypto.generate_keypair("k256"))
    with pytest.raises(Exception, match=r"await Repo\.create"):
        _ = repo.commit
    with pytest.raises(Exception, match=r"await Repo\.create"):
        repo.export_car()


# --- one head, one writer at a time ------------------------------------------------


class _SlowSigner(_HardwareSigner):
    """Yields to the event loop while signing, the way a device does."""

    async def sign(self, message: bytes) -> bytes:
        await asyncio.sleep(0.01)
        return await super().sign(message)


async def test_concurrent_writes_on_one_repo_all_land_in_sequence():
    repo = await Repo.create(DID, _SlowSigner(crypto.generate_keypair("p256")))
    await asyncio.gather(
        *(repo.put_record(NOTE, f"n{i}", {"$type": NOTE, "text": str(i)}) for i in range(5))
    )
    assert repo.record_count(NOTE) == 5
    assert {rkey for rkey, _ in repo.list_records(NOTE)} == {f"n{i}" for i in range(5)}
    repo.export_car()  # every reachable block is present: no write GC'd another's nodes
    reachable = {repo.commit_cid} | repo._mst_nodes | set(repo._entries.values())
    assert set(repo.store.cids()) == reachable  # and nothing staged was left behind


async def test_a_failing_signer_leaves_no_staged_blocks():
    repo = await Repo.create(DID, _HardwareSigner(crypto.generate_keypair("p256")))
    await repo.put_record(NOTE, "kept", {"$type": NOTE, "text": "before"})
    before = set(repo.store.cids())

    repo.signer = _LyingSigner(crypto.generate_keypair("p256"), crypto.generate_keypair("p256"))
    with pytest.raises(CryptoError):
        await repo.put_record(NOTE, "lost", {"$type": NOTE, "text": "never"})
    assert set(repo.store.cids()) == before


async def test_a_checkpoint_landing_mid_signature_is_caught_after_it():
    key = crypto.generate_keypair("p256")
    owner = await Repo.create(DID, key)
    await owner.put_record(NOTE, "theirs", {"$type": NOTE, "text": "pushed"})
    pushed = owner.export_car()

    repo = await Repo.create(DID, key)

    class _ImportsWhileSigning(_HardwareSigner):
        async def sign(self, message: bytes) -> bytes:
            repo.import_car(pushed, verify_key=key.public_key())  # sync, no lock to take
            return await super().sign(message)

    repo.signer = _ImportsWhileSigning(key)
    before = set(repo.store.cids())  # the state before the write began
    with pytest.raises(RepoError, match="head moved"):
        await repo.put_record(NOTE, "mine", {"$type": NOTE, "text": "stale"})
    assert repo.rev == owner.rev  # the checkpoint stood
    assert repo.get_record(NOTE, "mine") is None
    reachable = {repo.commit_cid} | repo._mst_nodes | set(repo._entries.values())
    assert set(repo.store.cids()) == reachable
    assert before != set(repo.store.cids())  # the import, not the write, changed the store
