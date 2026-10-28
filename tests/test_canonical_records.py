"""TID vectors, did:key resolution, rotation-key enforcement."""

import time

import pytest

from fastatp import Peer, build_did_document, crypto
from fastatp.did.document import (
    canonical_document_bytes,
    rotation_public_keys,
    verify_document_rotation,
)
from fastatp.did.resolver import did_key_resolver
from fastatp.exceptions import DIDDocumentInvalid, DIDNotFound, SignatureInvalid
from fastatp.lex.types import validate_tid
from fastatp.lexicon import Lexicon
from fastatp.tid import TidClock, decode_tid, encode_tid, generate_tid
from tests.conftest import read_vector_lines

# --- TID -----------------------------------------------------------------------


def test_tid_vectors_valid(interop_dir):
    for tid in read_vector_lines("tid_syntax_valid.txt"):
        timestamp_us, clock_id = decode_tid(tid)
        assert encode_tid(timestamp_us, clock_id) == tid  # lossless round-trip


def test_tid_vectors_invalid(interop_dir):
    for tid in read_vector_lines("tid_syntax_invalid.txt"):
        with pytest.raises(ValueError):
            decode_tid(tid)


def test_tid_generation():
    tid = generate_tid()
    validate_tid(tid)
    timestamp_us, _ = decode_tid(tid)
    assert abs(timestamp_us / 1e6 - time.time()) < 5

    clock = TidClock(clock_id=5)
    batch = [clock.now() for _ in range(500)]
    assert batch == sorted(batch)  # sortable
    assert len(set(batch)) == 500  # unique even within one microsecond
    assert all(decode_tid(t)[1] == 5 for t in batch)

    with pytest.raises(ValueError):
        encode_tid(1 << 53, 0)
    with pytest.raises(ValueError):
        encode_tid(0, 1024)


# --- did:key resolution -------------------------------------------------------


async def test_did_key_resolution_vectors(interop_dir):
    import json

    for name, curve in (("w3c_didkey_K256.json", "k256"), ("w3c_didkey_P256.json", "p256")):
        for fx in json.loads((interop_dir / "crypto" / name).read_text()):
            if "privateKeyBytesHex" in fx:
                raw = bytes.fromhex(fx["privateKeyBytesHex"])
            else:
                from fastatp.lex.types import base58btc_decode

                raw = base58btc_decode(fx["privateKeyBytesBase58"])
            priv = crypto.load_private_key_bytes(raw, curve)
            doc = await did_key_resolver(fx["publicDidKey"])
            assert doc.id == fx["publicDidKey"]
            method = doc.get_verification_method()
            assert crypto.encode_multikey(method.public_key()) == crypto.encode_multikey(
                priv.public_key()
            )


async def test_did_key_resolver_rejects():
    with pytest.raises(DIDNotFound):
        await did_key_resolver("did:web:example.com")
    with pytest.raises(DIDNotFound):
        await did_key_resolver("did:key:zNotAKey")


# --- rotation-key enforcement ----------------------------------------------------


class Note(Lexicon):
    nsid = "com.example.test.gate2note"
    text: str


async def test_rotation_key_cannot_sign_records():
    """A document whose (only) verification method is also listed in
    rotationKeys must be refused for record verification, even though the
    signature itself would verify."""
    from fastatp import Repo

    rotation = crypto.generate_keypair("k256")
    did = "did:web:rotate.example"
    rotation_did_key = crypto.encode_did_key(rotation.public_key())

    misconfigured = build_did_document(
        did=did, signing_key=rotation.public_key(), rotation_keys=[rotation_did_key]
    )

    async def resolver(d):
        return misconfigured if d == did else None

    repo = Repo(did, rotation)
    repo.put_record(Note.nsid, "evil", Note(text="evil"))
    proof = repo.prove(Note.nsid, "evil")
    with pytest.raises(SignatureInvalid, match="rotation"):
        await Peer(resolver=resolver).verify(proof)

    # the same key NOT listed as a rotation key verifies fine
    honest = build_did_document(did=did, signing_key=rotation.public_key())

    async def honest_resolver(d):
        return honest if d == did else None

    assert await Peer(resolver=honest_resolver).verify(proof)


def test_document_rotation_primitive():
    signing_old = crypto.generate_keypair("k256")
    signing_new = crypto.generate_keypair("p256")
    rotation = crypto.generate_keypair("k256")
    did = "did:web:rotate.example"
    rotation_entry = crypto.encode_did_key(rotation.public_key())

    old = build_did_document(
        did=did, signing_key=signing_old.public_key(), rotation_keys=[rotation_entry]
    )
    new = build_did_document(
        did=did, signing_key=signing_new.public_key(), rotation_keys=[rotation_entry]
    )

    assert len(rotation_public_keys(old)) == 1

    sig = crypto.sign_message(rotation, canonical_document_bytes(new))
    assert verify_document_rotation(old, new, sig)

    # signed by a non-rotation key -> refused
    bad_sig = crypto.sign_message(signing_old, canonical_document_bytes(new))
    assert not verify_document_rotation(old, new, bad_sig)

    # id mismatch -> refused
    other = build_did_document(did="did:web:other.example", signing_key=signing_new.public_key())
    assert not verify_document_rotation(old, other, sig)

    # malformed rotation key entries surface as invalid documents
    broken = build_did_document(did=did, signing_key=signing_old.public_key())
    broken.rotation_keys.append("did:key:zBogus")
    with pytest.raises(DIDDocumentInvalid):
        rotation_public_keys(broken)
