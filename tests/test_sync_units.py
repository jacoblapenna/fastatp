"""Unit coverage for firehose framing, mirror error paths, and MST lookup."""

from typing import cast

import pytest
from starlette.websockets import WebSocket

from fastatp import Peer, PeerServer, RepoMirror, build_did_document, crypto
from fastatp.cid import CID
from fastatp.exceptions import DIDNotFound, RepoError
from fastatp.lexicon import Lexicon
from fastatp.repo.mst import MST
from fastatp.repo.store import MemoryBlockStore
from fastatp.repo.sync import (
    commit_frame_body,
    decode_frame,
    encode_error_frame,
    encode_frame,
    sync_frame_body,
)

DID = "did:web:syncunit.example"
KEY = crypto.generate_keypair("k256")
DOC = build_did_document(did=DID, signing_key=KEY.public_key())


class Note(Lexicon):
    nsid = "com.example.test.syncNote"
    text: str


async def _resolver(did):
    return DOC if did == DID else None


def _server() -> PeerServer:
    return PeerServer(Peer(did=DID, signing_key=KEY))


def test_frame_round_trip_and_errors():
    frame = encode_frame("#info", {"name": "OutdatedCursor"})
    header, body = decode_frame(frame)
    assert header == {"op": 1, "t": "#info"}
    assert body["name"] == "OutdatedCursor"
    with pytest.raises(RepoError, match="Boom"):
        decode_frame(encode_error_frame("Boom", "details"))
    with pytest.raises(RepoError):
        decode_frame(frame + b"\x01")  # trailing bytes
    with pytest.raises(RepoError):
        decode_frame(b"\x81\x01" + b"\x81\x01")  # non-map header/body


async def test_handle_frame_dispatch():
    srv = _server()
    mirror = RepoMirror(DID, resolver=_resolver)
    event = srv.put_record(Note.nsid, "one", Note(text="hello"))
    # checkpoint first, then a commit, then an ignorable info frame
    sync = encode_frame(
        "#sync", sync_frame_body(DID, srv.seq, srv.repo.rev, srv.repo.export_car(), event.time)
    )
    assert await mirror.handle_frame(sync) == []
    assert mirror.rev == srv.repo.rev

    event2 = srv.put_record(Note.nsid, "two", Note(text="again"))
    commit_frame = encode_frame("#commit", commit_frame_body(DID, srv.seq, event2))
    records = await mirror.handle_frame(commit_frame)
    assert records[0]["record"]["text"] == "again"
    assert await mirror.handle_frame(encode_frame("#info", {"name": "x"})) == []
    rec_two = mirror.get_record(Note.nsid, "two")
    assert rec_two is not None
    assert rec_two["text"] == "again"
    assert mirror.get_record(Note.nsid, "missing") is None


async def test_mirror_gap_and_did_checks():
    srv = _server()
    mirror = RepoMirror(DID, resolver=_resolver)
    e1 = srv.put_record(Note.nsid, "a", Note(text="1"))
    await mirror.apply_commit(commit_frame_body(DID, 1, e1))
    _e2 = srv.put_record(Note.nsid, "b", Note(text="2"))
    e3 = srv.put_record(Note.nsid, "c", Note(text="3"))
    # skipping e2 is a gap: since doesn't match local rev
    with pytest.raises(RepoError, match="gap"):
        await mirror.apply_commit(commit_frame_body(DID, 3, e3))

    # wrong-DID mirror rejects the commit outright
    other = RepoMirror("did:web:someone-else.example", resolver=_resolver)
    with pytest.raises(RepoError):
        await other.apply_commit(commit_frame_body(DID, 1, e1))

    # no resolver configured
    bare = RepoMirror(DID)
    with pytest.raises(DIDNotFound):
        await bare.signing_keys()

    async def none_resolver(did):
        return None

    with pytest.raises(DIDNotFound):
        await RepoMirror(DID, resolver=none_resolver).signing_keys()


async def test_mirror_did_key_producer():
    key = crypto.generate_keypair("p256")
    did = crypto.encode_did_key(key.public_key())
    srv = PeerServer(Peer(did=did, signing_key=key))
    mirror = RepoMirror(did)  # did:key needs no resolver
    event = srv.put_record(Note.nsid, "k", Note(text="selfcert"))
    records = await mirror.apply_commit(commit_frame_body(did, 1, event))
    assert records[0]["record"]["text"] == "selfcert"


async def test_mirror_rev_mismatch_rejected():
    srv = _server()
    mirror = RepoMirror(DID, resolver=_resolver)
    event = srv.put_record(Note.nsid, "a", Note(text="1"))
    body = commit_frame_body(DID, 1, event)
    body["rev"] = "3jzfcijpj2z2a"
    with pytest.raises(RepoError, match="rev"):
        await mirror.apply_commit(body)


def test_mst_point_lookup():
    store = MemoryBlockStore()
    mst = MST(store)
    value = CID.for_raw(b"v")
    keys = [f"com.example.coll/rec{i:03d}" for i in range(60)]
    root = mst.build(dict.fromkeys(keys, value))
    for key in keys:
        assert mst.get(root, key) == value
    assert mst.get(root, "com.example.coll/absent") is None
    assert mst.get(root, "aaa.example/000") is None
    assert mst.get(root, "zzz.example/zzz") is None


async def test_mirror_multi_method_and_rotation_semantics():
    """Mirror-side twin of the Peer key-selection rules: multi-method
    documents verify, rotation-only documents are rejected, and key
    rotation propagates because candidates are re-resolved per event."""
    from fastatp.did.document import VerificationMethod

    signing = crypto.generate_keypair("k256")
    did = "did:web:mirror-rotate.example"
    doc = build_did_document(did=did, signing_key=signing.public_key())
    decoy = crypto.generate_keypair("p256")
    doc.verification_method.insert(
        0,
        VerificationMethod(
            id=f"{did}#key-agreement",
            controller=did,
            public_key_multibase=crypto.encode_multikey(decoy.public_key()),
        ),
    )
    docs = {"current": doc}

    async def resolver(d):
        return docs["current"] if d == did else None

    srv = PeerServer(Peer(did=did, signing_key=signing))
    mirror = RepoMirror(did, resolver=resolver)
    e1 = srv.put_record(Note.nsid, "a", Note(text="1"))
    assert await mirror.apply_commit(commit_frame_body(did, 1, e1))

    # rotation: the document now lists a NEW signing key; commits from the
    # new key verify without any mirror state reset (no pinned cache)
    new_key = crypto.generate_keypair("k256")
    docs["current"] = build_did_document(did=did, signing_key=new_key.public_key())
    srv2 = PeerServer(Peer(did=did, signing_key=new_key))
    e2 = srv2.put_record(Note.nsid, "a", Note(text="rotated"))
    fresh = RepoMirror(did, resolver=resolver)
    assert await fresh.apply_commit(commit_frame_body(did, 1, e2))

    # a document whose only method is rotation-listed is rejected
    rot = crypto.generate_keypair("k256")
    docs["current"] = build_did_document(
        did=did,
        signing_key=rot.public_key(),
        rotation_keys=[crypto.encode_did_key(rot.public_key())],
    )
    srv3 = PeerServer(Peer(did=did, signing_key=rot))
    e3 = srv3.put_record(Note.nsid, "a", Note(text="evil"))
    with pytest.raises(RepoError, match="rotation"):
        await RepoMirror(did, resolver=resolver).apply_commit(commit_frame_body(did, 1, e3))


async def test_malformed_frame_bodies_raise_repo_error():
    """Missing frame/op fields surface as RepoError, never bare KeyError."""
    srv = _server()
    mirror = RepoMirror(DID, resolver=_resolver)
    with pytest.raises(RepoError, match="blocks"):
        await mirror.apply_sync({"seq": 1})
    with pytest.raises(RepoError, match="blocks"):
        await mirror.apply_commit({"seq": 1})
    event = srv.put_record(Note.nsid, "kf", Note(text="x"))
    body = commit_frame_body(DID, 1, event)
    del body["commit"]
    with pytest.raises(RepoError, match="commit"):
        await mirror.apply_commit(body)
    body = commit_frame_body(DID, 1, event)
    body["ops"] = [{"action": "create"}]  # missing path
    with pytest.raises(RepoError, match="path"):
        await mirror.apply_commit(body)


async def test_failed_commit_leaves_store_untouched():
    """A commit whose ops don't reproduce the signed root must not grow the
    mirror's block store (no junk from repeated bad events)."""
    srv = _server()
    mirror = RepoMirror(DID, resolver=_resolver)
    e1 = srv.put_record(Note.nsid, "a", Note(text="1"))
    await mirror.apply_commit(commit_frame_body(DID, 1, e1))
    size_before = len(mirror.store)

    e2 = srv.put_record(Note.nsid, "b", Note(text="2"))
    body = commit_frame_body(DID, 2, e2)
    # lie about the op path: entries map diverges from the signed root
    body["ops"][0]["path"] = f"{Note.nsid}/liar"
    for _ in range(5):
        with pytest.raises(RepoError, match="root"):
            await mirror.apply_commit(body)
    assert len(mirror.store) == size_before
    assert mirror.rev == e1.rev  # state unchanged


async def test_invalid_op_path_and_bad_block_raise_repo_error():
    """Malformed op paths and non-CBOR record blocks stay inside the
    RepoError contract instead of leaking ValueError/DataModelError."""
    from fastatp.cid import CID
    from fastatp.repo.car import encode_car

    srv = _server()
    mirror = RepoMirror(DID, resolver=_resolver)
    event = srv.put_record(Note.nsid, "p", Note(text="x"))

    body = commit_frame_body(DID, 1, event)
    body["ops"] = [dict(body["ops"][0], path="not-a-valid-mst-key")]
    with pytest.raises(RepoError, match="invalid path"):
        await mirror.apply_commit(body)

    # a record block whose bytes are not valid DAG-CBOR
    junk = b"\xff\xff\xff"
    junk_cid = CID.for_dag_cbor(junk)
    body = commit_frame_body(DID, 1, event)
    roots, blocks = __import__("fastatp.repo.car", fromlist=["decode_car"]).decode_car(
        body["blocks"]
    )
    blocks[junk_cid] = junk
    body["blocks"] = encode_car(roots, blocks)
    body["ops"] = [dict(body["ops"][0], cid=junk_cid)]
    with pytest.raises(RepoError, match="not valid DAG-CBOR"):
        await mirror.apply_commit(body)


async def test_mirror_store_prunes_superseded_blocks():
    """Sustained update churn must not grow the mirror's block store."""
    srv = _server()
    mirror = RepoMirror(DID, resolver=_resolver)
    sizes = []
    for i, seq in zip(range(30), range(1, 31), strict=True):
        event = srv.put_record(Note.nsid, "hot", Note(text=f"v{i}"))
        await mirror.apply_commit(commit_frame_body(DID, seq, event))
        sizes.append(len(mirror.store))
    assert max(sizes) <= sizes[0] + 1  # steady, not linear in updates
    rec_hot = mirror.get_record(Note.nsid, "hot")
    assert rec_hot is not None
    assert rec_hot["text"] == "v29"


async def test_malformed_frame_bytes_raise_repo_error():
    mirror = RepoMirror(DID, resolver=_resolver)
    with pytest.raises(RepoError, match="malformed frame"):
        await mirror.handle_frame(b"\xff\xff\xff")


async def test_crafted_sync_frame_stays_repo_error():
    """Non-canonical commit/MST bytes inside a #sync CAR surface as
    RepoError (the boundary guard), never DataModelError."""
    from fastatp.cid import CID
    from fastatp.repo.car import encode_car

    junk_commit = b"\x18\x17"  # non-minimal int: valid hash, invalid DAG-CBOR
    cid = CID.for_dag_cbor(junk_commit)
    car = encode_car([cid], {cid: junk_commit})
    mirror = RepoMirror(DID, resolver=_resolver)
    with pytest.raises(RepoError):
        await mirror.apply_sync({"seq": 1, "blocks": car})
    with pytest.raises(RepoError):
        await mirror.apply_commit({"seq": 1, "blocks": car, "commit": cid})


async def test_slow_consumer_evicted_with_bounded_queue():
    """A subscriber whose queue overflows is evicted and poisoned instead of
    growing server memory without bound."""
    import asyncio

    srv = _server()
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    q.put_nowait((1, b"x"))
    q.put_nowait((2, b"y"))
    srv._queues.add(q)
    srv.put_record(Note.nsid, "overflow", Note(text="z"))
    assert q not in srv._queues  # evicted
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert items[-1][0] == -1  # poison sentinel for ConsumerTooSlow


async def test_checkpoint_seq_captured_before_send():
    """A commit emitted concurrently with the checkpoint send must still be
    delivered: _catch_up returns the seq the frame was built at."""
    srv = _server()
    for i in range(4):
        srv.put_record(Note.nsid, f"r{i}", Note(text=str(i)))

    sent = []

    class FakeWS:
        async def send_bytes(self, data):
            sent.append(data)
            if len(sent) == 2:  # during the #sync frame send, a commit lands
                srv.put_record(Note.nsid, "concurrent", Note(text="raced"))

        async def close(self):
            pass

    # cursor=1 with backfill_window default 512 holds all events; force the
    # outdated path by shrinking the window view
    srv._primary._backfill = type(srv._backfill)(list(srv._backfill)[-1:], maxlen=1)
    # FakeWS is a minimal test double satisfying the send_bytes/close surface
    last_sent = await srv._catch_up(cast("WebSocket", FakeWS()), cursor=1)
    # the concurrent commit has seq > last_sent, so the main loop won't skip it
    assert last_sent is not None
    assert last_sent < srv.seq
