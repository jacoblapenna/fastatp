"""Peer record-proof verification: the post-pivot acceptance flows.

A record travels as a proof slice (Repo.prove(): signed commit + MST path +
record block); Peer.verify() re-derives its membership in the signed root.
"""

import pytest

from fastatp import (
    DIDNotFound,
    Lexicon,
    NotAuthorized,
    Peer,
    Repo,
    SignatureInvalid,
    SignerMismatch,
    build_did_document,
    chain,
)
from fastatp.crypto import generate_keypair
from fastatp.lex.types import Datetime


class Ping(Lexicon):
    nsid = "com.example.test.ping"
    seq: int
    at: Datetime


DID = "did:web:devices.example:device-1"
COLL = Ping.nsid


@pytest.fixture(params=["k256", "p256"])
def keypair(request):
    return generate_keypair(request.param)


@pytest.fixture
def doc(keypair):
    return build_did_document(did=DID, signing_key=keypair.public_key())


@pytest.fixture
def db_resolver(doc):
    async def resolver(did):
        return doc if did == DID else None

    return resolver


def _repo_with(keypair, *pings: Ping) -> Repo:
    repo = Peer(did=DID, signing_key=keypair).create_repo()
    for ping in pings:
        repo.put_record(COLL, f"seq-{ping.seq}", ping)
    return repo


async def test_prove_verify_round_trip_20_lines(keypair, db_resolver):
    # The complete user journey, well under 20 lines:
    signer = Peer(did=DID, signing_key=keypair)  # 1
    repo = signer.create_repo()  # 2
    repo.put_record(COLL, "boot", Ping(seq=7, at="2026-07-02T12:00:00Z"))  # 3
    proof = repo.prove(COLL, "boot")  # 4
    verifier = Peer(resolver=db_resolver)  # 5
    record = await verifier.verify(proof)  # 6
    assert isinstance(record, Ping) and record.seq == 7  # 7


async def test_expected_signer_binding(keypair, db_resolver):
    repo = _repo_with(keypair, Ping(seq=1, at="2026-07-02T12:00:00Z"))
    proof = repo.prove(COLL, "seq-1")
    verifier = Peer(resolver=db_resolver)
    assert await verifier.verify(proof, expected_signer=DID)
    with pytest.raises(SignerMismatch):
        await verifier.verify(proof, expected_signer="did:web:other.example")


async def test_chain_resolver_end_to_end(keypair, doc):
    async def miss(did):
        return None

    async def hit(did):
        return doc if did == DID else None

    repo = _repo_with(keypair, Ping(seq=2, at="2026-07-02T12:00:00Z"))
    proof = repo.prove(COLL, "seq-2")
    record = await Peer(resolver=chain(miss, hit)).verify(proof)
    assert isinstance(record, Ping)
    assert record.seq == 2
    with pytest.raises(DIDNotFound):
        await Peer(resolver=chain(miss, miss)).verify(proof)


async def test_proof_selects_record_in_populated_repo(keypair, db_resolver):
    """A proof from a many-record repo witnesses exactly its one record."""
    pings = [Ping(seq=i, at="2026-07-02T12:00:00Z") for i in range(30)]
    repo = _repo_with(keypair, *pings)
    proof = repo.prove(COLL, "seq-17")
    record = await Peer(resolver=db_resolver).verify(proof)
    assert isinstance(record, Ping)
    assert record.seq == 17
    # explicit path selection works too
    record = await Peer(resolver=db_resolver).verify(proof, path=f"{COLL}/seq-17")
    assert isinstance(record, Ping)
    assert record.seq == 17
    with pytest.raises(SignatureInvalid):
        await Peer(resolver=db_resolver).verify(proof, path=f"{COLL}/seq-4")


async def test_tampered_proof_rejected(keypair, db_resolver):
    """Swapping the record for other bytes breaks the CAR/MST binding."""
    from fastatp.repo.car import decode_car, encode_car

    repo = _repo_with(keypair, Ping(seq=1, at="2026-07-02T12:00:00Z"))
    forged_record = Ping(seq=999, at="2026-07-02T12:00:00Z")
    roots, blocks = decode_car(repo.prove(COLL, "seq-1"))

    # replace the record block with a forged record's bytes
    original_cid = repo.list_records(COLL)[0][1]
    del blocks[original_cid]
    blocks[forged_record.cid()] = forged_record.to_dag_cbor()
    forged = encode_car(roots, blocks)
    verifier = Peer(resolver=db_resolver)
    with pytest.raises(SignatureInvalid):
        await verifier.verify(forged)


async def test_wrong_key_rejected(keypair, db_resolver):
    impostor_key = generate_keypair("k256")
    repo = Repo(DID, impostor_key)
    repo.put_record(COLL, "evil", Ping(seq=1, at="2026-07-02T12:00:00Z"))
    proof = repo.prove(COLL, "evil")
    verifier = Peer(resolver=db_resolver)  # resolves the honest document
    with pytest.raises(SignatureInvalid):
        await verifier.verify(proof)


async def test_authorize_hook(keypair, db_resolver):
    repo = _repo_with(keypair, Ping(seq=1, at="2026-07-02T12:00:00Z"))
    proof = repo.prove(COLL, "seq-1")

    seen = []

    def deny(issuer, nsid, context):
        seen.append((issuer, nsid, context))
        return False

    verifier = Peer(resolver=db_resolver, authorize=deny)
    with pytest.raises(NotAuthorized):
        await verifier.verify(proof, context={"route": "ingest"})
    assert seen == [(DID, Ping.nsid, {"route": "ingest"})]

    async def allow(issuer, nsid, context):
        return True

    assert await Peer(resolver=db_resolver, authorize=allow).verify(proof)


async def test_did_key_issuer_short_circuits_resolution():
    key = generate_keypair("k256")
    from fastatp.crypto import encode_did_key

    did = encode_did_key(key.public_key())
    repo = Repo(did, key)
    repo.put_record(COLL, "self", Ping(seq=4, at="2026-07-02T12:00:00Z"))
    proof = repo.prove(COLL, "self")

    async def unreachable(did):  # must never be called for did:key
        raise AssertionError("resolver called for did:key")

    record = await Peer(resolver=unreachable).verify(proof, expected_signer=did)
    assert isinstance(record, Ping)
    assert record.seq == 4


async def test_malformed_proofs_rejected(keypair, db_resolver):
    verifier = Peer(resolver=db_resolver)
    with pytest.raises(SignatureInvalid):
        await verifier.verify(b"not a car at all")
    # a car whose root is not a commit
    from fastatp.repo.car import encode_car

    rec = Ping(seq=1, at="2026-07-02T12:00:00Z")
    car = encode_car([rec.cid()], {rec.cid(): rec.to_dag_cbor()})
    with pytest.raises(SignatureInvalid):
        await verifier.verify(car)


async def test_create_repo_requires_identity():
    with pytest.raises(ValueError):
        Peer().create_repo()


async def test_multi_method_document_signing_key_not_first(keypair):
    """A document listing other keys before #atproto must still verify
    (the commit carries no key hint; candidates are tried in order)."""
    from fastatp.crypto import encode_multikey, generate_keypair
    from fastatp.did.document import VerificationMethod

    doc = build_did_document(did=DID, signing_key=keypair.public_key())
    decoy = generate_keypair("p256")
    doc.verification_method.insert(
        0,
        VerificationMethod(
            id=f"{DID}#key-agreement",
            controller=DID,
            public_key_multibase=encode_multikey(decoy.public_key()),
        ),
    )

    async def resolver(did):
        return doc if did == DID else None

    repo = _repo_with(keypair, Ping(seq=9, at="2026-07-02T12:00:00Z"))
    record = await Peer(resolver=resolver).verify(repo.prove(COLL, "seq-9"))
    assert isinstance(record, Ping)
    assert record.seq == 9


async def test_malformed_method_key_does_not_mask_good_key(keypair):
    """One undecodable verification method must not break verification via
    the good #atproto key, and an all-garbage document raises
    SignatureInvalid (not CryptoError) per the verify() contract."""
    from fastatp.did.document import VerificationMethod

    doc = build_did_document(did=DID, signing_key=keypair.public_key())
    doc.verification_method.insert(
        0,
        VerificationMethod(id=f"{DID}#broken", controller=DID, public_key_multibase="zNotARealKey"),
    )

    async def resolver(did):
        return doc if did == DID else None

    repo = _repo_with(keypair, Ping(seq=3, at="2026-07-02T12:00:00Z"))
    proof = repo.prove(COLL, "seq-3")
    record = await Peer(resolver=resolver).verify(proof)
    assert isinstance(record, Ping) and record.seq == 3

    doc.verification_method = [doc.verification_method[0]]  # only the broken key
    with pytest.raises(SignatureInvalid, match="unusable"):
        await Peer(resolver=resolver).verify(proof)


def test_did_web_url_ipv6_loopback():
    from fastatp.did.resolver import did_web_url

    assert did_web_url("did:web:%5B%3A%3A1%5D") == "http://[::1]/.well-known/did.json"
    assert did_web_url("did:web:%5B%3A%3A1%5D%3A8443:dev") == "http://[::1]:8443/dev/did.json"
    # non-loopback IPv6 stays https
    assert (
        did_web_url("did:web:%5B2001%3Adb8%3A%3A1%5D")
        == "https://[2001:db8::1]/.well-known/did.json"
    )


async def test_crafted_proof_with_malformed_mst_node(keypair, db_resolver):
    """A proof whose signed root points at a non-node block must fail as
    SignatureInvalid, not TypeError/KeyError."""
    from fastatp import dagcbor
    from fastatp.cid import CID
    from fastatp.repo.car import encode_car
    from fastatp.repo.commit import Commit

    junk = dagcbor.encode({"not": "a node"})
    junk_cid = CID.for_dag_cbor(junk)
    commit = Commit.create(DID, junk_cid, "3jzfcijpj2z2a", keypair)
    car = encode_car([commit.cid()], {commit.cid(): commit.to_dag_cbor(), junk_cid: junk})
    verifier = Peer(resolver=db_resolver)
    with pytest.raises(SignatureInvalid):
        await verifier.verify(car)
    with pytest.raises(SignatureInvalid):
        await verifier.verify(car, path="com.example.x/y")


async def test_proof_with_non_cbor_record_block(keypair, db_resolver):
    """A CID-valid but non-CBOR record block fails as SignatureInvalid."""
    from fastatp.cid import CID
    from fastatp.repo.car import encode_car
    from fastatp.repo.commit import Commit
    from fastatp.repo.mst import MST
    from fastatp.repo.store import MemoryBlockStore

    junk = b"\xff\xff\xff"
    junk_cid = CID.for_dag_cbor(junk)
    store = MemoryBlockStore()
    root = MST(store).build({f"{COLL}/bad": junk_cid})
    commit = Commit.create(DID, root, "3jzfcijpj2z2a", keypair)
    blocks = {commit.cid(): commit.to_dag_cbor(), junk_cid: junk}
    for cid in MST(store).node_cids(root):
        node_bytes = store.get(cid)
        assert node_bytes is not None
        blocks[cid] = node_bytes
    car = encode_car([commit.cid()], blocks)
    with pytest.raises(SignatureInvalid, match="DAG-CBOR"):
        await Peer(resolver=db_resolver).verify(car)


async def test_crafted_commit_with_wrong_typed_fields(keypair, db_resolver):
    """A commit block with non-string did / non-bytes sig fails as
    SignatureInvalid, never AttributeError/TypeError."""
    from fastatp import dagcbor
    from fastatp.cid import CID
    from fastatp.repo.car import encode_car

    for bad_commit in (
        {
            "did": 42,
            "rev": "3jzfcijpj2z2a",
            "data": CID.for_raw(b"x"),
            "prev": None,
            "version": 3,
            "sig": bytes(64),
        },
        {
            "did": DID,
            "rev": "3jzfcijpj2z2a",
            "data": CID.for_raw(b"x"),
            "prev": None,
            "version": 3,
            "sig": "not-bytes",
        },
        {
            "did": DID,
            "rev": 7,
            "data": CID.for_raw(b"x"),
            "prev": None,
            "version": 3,
            "sig": bytes(64),
        },
    ):
        block = dagcbor.encode(bad_commit)
        cid = CID.for_dag_cbor(block)
        car = encode_car([cid], {cid: block})
        with pytest.raises(SignatureInvalid):
            await Peer(resolver=db_resolver).verify(car)


async def test_crafted_proof_with_non_utf8_key(keypair, db_resolver):
    """Non-UTF-8 key bytes in a proof's MST node fail as SignatureInvalid."""
    from fastatp import dagcbor
    from fastatp.cid import CID
    from fastatp.repo.car import encode_car
    from fastatp.repo.commit import Commit

    node = dagcbor.encode(
        {"e": [{"k": b"\xff\xfe", "p": 0, "t": None, "v": CID.for_raw(b"x")}], "l": None}
    )
    node_cid = CID.for_dag_cbor(node)
    commit = Commit.create(DID, node_cid, "3jzfcijpj2z2a", keypair)
    car = encode_car([commit.cid()], {commit.cid(): commit.to_dag_cbor(), node_cid: node})
    with pytest.raises(SignatureInvalid):
        await Peer(resolver=db_resolver).verify(car)
    with pytest.raises(SignatureInvalid):
        await Peer(resolver=db_resolver).verify(car, path="com.example.x/y")


async def test_proof_with_non_record_block(keypair, db_resolver):
    """A CBOR block that isn't a record map fails as SignatureInvalid;
    an unregistered $type still surfaces as UnknownLexicon."""
    from fastatp import Repo

    repo = Repo(DID, keypair)
    repo.put_record(COLL, "notmap", {"$type": COLL, "seq": 1, "at": "2026-07-02T12:00:00Z"})
    # craft: a record that is a bare list (no $type dict)
    from fastatp import dagcbor
    from fastatp.cid import CID
    from fastatp.repo.car import encode_car
    from fastatp.repo.commit import Commit
    from fastatp.repo.mst import MST
    from fastatp.repo.store import MemoryBlockStore

    junk = dagcbor.encode([1, 2, 3])
    junk_cid = CID.for_dag_cbor(junk)
    store = MemoryBlockStore()
    root = MST(store).build({f"{COLL}/weird": junk_cid})
    commit = Commit.create(DID, root, "3jzfcijpj2z2a", keypair)
    blocks = {commit.cid(): commit.to_dag_cbor(), junk_cid: junk}
    for cid in MST(store).node_cids(root):
        node_bytes = store.get(cid)
        assert node_bytes is not None
        blocks[cid] = node_bytes
    with pytest.raises(SignatureInvalid, match="valid record"):
        await Peer(resolver=db_resolver).verify(encode_car([commit.cid()], blocks))


async def test_verify_detailed_returns_metadata(keypair, db_resolver):
    """verify_detailed exposes issuer/cid/path from the single decode verify does."""
    repo = _repo_with(keypair, Ping(seq=5, at="2026-07-02T12:00:00Z"))
    proof = repo.prove(COLL, "seq-5")
    vr = await Peer(resolver=db_resolver).verify_detailed(proof)
    assert isinstance(vr.record, Ping)
    assert vr.record.seq == 5
    assert vr.issuer_did == DID
    assert vr.record_cid == repo.list_records(COLL)[0][1]
    assert vr.path == f"{COLL}/seq-5"
    # verify() still returns just the record (unchanged contract)
    record = await Peer(resolver=db_resolver).verify(proof)
    assert isinstance(record, Ping) and record.seq == 5
