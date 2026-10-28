"""Blobs — the repo's optional media capability.

Blob CIDs are raw codec (0x55) + sha-256; the blob object round-trips both
encodings; blobs ride OUTSIDE the MST and repo CARs; a record embedding a
blob commits, emits (inversion-verified), exports, imports, and the blob
serves over getBlob/listBlobs.
"""

import asyncio
import base64
import json
from typing import Any

import httpx
import pytest

from fastatp import Peer, PeerServer, build_did_document, crypto, dagcbor, datamodel
from fastatp.cid import CID
from fastatp.exceptions import RepoError
from fastatp.lex.types import Blob
from fastatp.repo.blobs import MemoryBlobStore, SqliteBlobStore
from fastatp.repo.repo import Repo
from fastatp.repo.sync import RepoMirror, commit_frame_body, verify_commit_inversion
from fastatp.server.peer_server import GET_BLOB_PATH, LIST_BLOBS_PATH

DID = "did:web:blobs.example"
NSID = "com.example.media.post"


def _repo(blobs=None) -> Repo:
    return Repo(DID, crypto.generate_keypair("k256"), blobs=blobs)


# --- 15a: the stores ------------------------------------------------------------


@pytest.mark.parametrize("make_store", [MemoryBlobStore, lambda: SqliteBlobStore(":memory:")])
def test_blob_store_contract(make_store):
    store = make_store()
    data = b"jpeg bytes here"
    cid = CID.for_raw(data)
    assert store.get(cid) is None and store.info(cid) is None
    assert cid not in store and len(store) == 0
    store.put(cid, data, mime_type="image/jpeg", rev="3aaa")
    assert store.get(cid) == data
    info = store.info(cid)
    assert info.size == len(data) and info.mime_type == "image/jpeg" and info.rev == "3aaa"
    assert cid in store and len(store) == 1
    assert [b.cid for b in store.infos()] == [cid]
    assert store.total_bytes() == len(data)
    store.delete(cid)
    assert store.get(cid) is None and len(store) == 0


def test_sqlite_blob_store_survives_reopen(tmp_path):
    path = tmp_path / "blobs.sqlite"
    data = b"durable media"
    cid = CID.for_raw(data)
    store = SqliteBlobStore(path)
    store.put(cid, data, mime_type="image/png", rev="3bbb")
    store.close()
    reopened = SqliteBlobStore(path)
    assert reopened.get(cid) == data
    assert reopened.info(cid).mime_type == "image/png"
    reopened.close()


def test_blob_cids_are_raw_codec():
    data = b"raw not dag-cbor"
    cid = CID.for_raw(data)
    assert bytes(cid)[:2] == bytes([0x01, 0x55])  # CIDv1, raw codec
    assert cid != CID.for_dag_cbor(data)


# --- 15b: the blob shape on the wire --------------------------------------------


def test_blob_object_round_trips_both_encodings(interop_dir):
    """The interop data-model fixture containing a blob is the byte oracle;
    the palette Blob and add_blob() must produce that exact shape."""
    fixtures = json.loads((interop_dir / "data-model" / "data-model-fixtures.json").read_text())
    fx = next(f for f in fixtures if '"blob"' in json.dumps(f["json"]))
    obj = datamodel.from_json(fx["json"])
    encoded = dagcbor.encode(obj)
    assert base64.b64encode(encoded).decode().rstrip("=") == fx["cbor_base64"].rstrip("=")
    # and back out to JSON identically
    assert datamodel.to_json(dagcbor.decode(encoded)) == fx["json"]

    # the palette type emits the same wire shape
    blob_json = fx["json"]["c"]
    blob = Blob.model_validate(blob_json)
    assert blob.model_dump(by_alias=True) == blob_json


def test_add_blob_produces_conforming_reference():
    repo = _repo(blobs=MemoryBlobStore())
    data = b"picture!"
    blob = repo.add_blob(data, "image/jpeg")
    blob_json = blob.model_dump(by_alias=True)
    assert blob_json["$type"] == "blob"
    assert blob_json["size"] == len(data)
    assert blob_json["mimeType"] == "image/jpeg"
    assert blob_json["ref"] == {"$link": CID.for_raw(data).encode()}
    # embeddable: the record validates and the ref decodes as a blob ref
    record = {"$type": NSID, "text": "with media", "image": blob_json}
    obj = datamodel.from_json(record)
    assert datamodel.blob_refs(obj) == {CID.for_raw(data)}


def test_media_free_repo_pays_nothing():
    repo = _repo()  # no blob store wired
    assert repo.blobs is None
    assert repo.get_blob(CID.for_raw(b"x")) is None
    assert repo.list_blobs() == [] and repo.gc_blobs() == 0
    with pytest.raises(RepoError, match="no blob store"):
        repo.add_blob(b"x", "image/png")


# --- blob GC (explicit) ---------------------------------------------------------


def test_gc_blobs_reclaims_unreferenced():
    repo = _repo(blobs=MemoryBlobStore())
    keep = repo.add_blob(b"keep me", "image/png")
    drop = repo.add_blob(b"drop me", "image/png")
    repo.put_record(NSID, "keeper", {"$type": NSID, "image": keep.model_dump(by_alias=True)})
    repo.put_record(NSID, "dropper", {"$type": NSID, "image": drop.model_dump(by_alias=True)})
    assert repo.gc_blobs() == 0  # both referenced
    repo.delete_record(NSID, "dropper")
    assert repo.blob_refs() == {CID.for_raw(b"keep me")}
    assert repo.gc_blobs() == 1
    assert repo.get_blob(CID.for_raw(b"drop me")) is None
    assert repo.get_blob(CID.for_raw(b"keep me")) == b"keep me"


# --- the gate: full lifecycle ----------------------------------------------------


def test_blob_record_commits_emits_exports_imports_and_serves(serve_app, tmp_path):
    async def run() -> None:
        key = crypto.generate_keypair("k256")
        doc = build_did_document(did=DID, signing_key=key.public_key())

        async def resolver(did: str) -> Any:
            return doc if did == DID else None

        blob_store = SqliteBlobStore(tmp_path / "blobs.sqlite")
        repo = Repo(DID, key, blobs=blob_store)
        srv = PeerServer(Peer(did=DID, signing_key=key), repo=repo)

        image = b"\xff\xd8\xff fake-jpeg body \xff\xd9"
        relay_cursor = srv.repo.rev  # a relay synced to the head BEFORE the blob
        blob = srv.repo.add_blob(image, "image/jpeg")
        blob_cid = CID.for_raw(image)

        # commits + emits: the event passes the relay inversion check and a
        # verifying mirror; the blob bytes are NOT in the event blocks
        event = srv.put_record(
            NSID, "post1", {"$type": NSID, "text": "hi", "image": blob.model_dump(by_alias=True)}
        )
        body = commit_frame_body(DID, srv.seq, event)
        verify_commit_inversion(body)
        assert blob_cid not in event.blocks  # rides outside the MST/diff
        mirror = RepoMirror(DID, resolver=resolver)
        records = await mirror.apply_commit(body)
        assert records[0]["record"]["image"]["ref"]["$link"] == blob_cid.encode()

        # exports/imports: the CAR round-trips the record (not the blob bytes)
        car = srv.repo.export_car()
        replica = Repo(DID, key)
        replica.import_car(car)
        got = replica.get_record(NSID, "post1")
        assert got is not None and got["image"]["ref"]["$link"] == blob_cid.encode()
        assert blob_cid not in replica.store.cids()

        # serves: getBlob returns the original bytes + content type;
        # listBlobs enumerates, honors since/limit/cursor
        async with serve_app(srv.app) as port:
            base = f"http://127.0.0.1:{port}"
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{base}{GET_BLOB_PATH}", params={"did": DID, "cid": blob_cid.encode()}
                )
                assert r.status_code == 200
                assert r.content == image
                assert r.headers["content-type"].startswith("image/jpeg")

                r = await client.get(f"{base}{LIST_BLOBS_PATH}", params={"did": DID})
                assert r.status_code == 200
                assert r.json() == {"cids": [blob_cid.encode()]}
                # the relay-backfill case: a relay cursored at the pre-blob
                # head rev has NOT seen the referencing commit and must get
                # the blob (the stamp equals the cursor -> >= matches)
                r = await client.get(
                    f"{base}{LIST_BLOBS_PATH}", params={"did": DID, "since": relay_cursor}
                )
                assert r.json() == {"cids": [blob_cid.encode()]}
                # since= at the current (post-commit) head rev filters it out
                r = await client.get(
                    f"{base}{LIST_BLOBS_PATH}", params={"did": DID, "since": srv.repo.rev}
                )
                assert r.json() == {"cids": []}

                missing = await client.get(
                    f"{base}{GET_BLOB_PATH}",
                    params={"did": DID, "cid": CID.for_raw(b"nope").encode()},
                )
                assert missing.status_code == 404
                assert missing.json()["error"] == "BlobNotFound"
                bad = await client.get(f"{base}{GET_BLOB_PATH}", params={"did": DID})
                assert bad.status_code == 400

    asyncio.run(run())


def test_list_blobs_pagination(serve_app):
    async def run() -> None:
        key = crypto.generate_keypair("k256")
        repo = Repo(DID, key, blobs=MemoryBlobStore())
        srv = PeerServer(Peer(did=DID, signing_key=key), repo=repo)
        cids = sorted(
            srv.repo.add_blob(f"blob {i}".encode(), "text/plain").ref.ref for i in range(5)
        )
        async with serve_app(srv.app) as port:
            base = f"http://127.0.0.1:{port}"
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{base}{LIST_BLOBS_PATH}", params={"did": DID, "limit": 2})
                page1 = r.json()
                assert page1["cids"] == cids[:2] and page1["cursor"] == cids[1]
                r = await client.get(
                    f"{base}{LIST_BLOBS_PATH}",
                    params={"did": DID, "limit": 2, "cursor": page1["cursor"]},
                )
                page2 = r.json()
                assert page2["cids"] == cids[2:4] and page2["cursor"] == cids[3]
                r = await client.get(
                    f"{base}{LIST_BLOBS_PATH}",
                    params={"did": DID, "limit": 2, "cursor": page2["cursor"]},
                )
                assert r.json() == {"cids": cids[4:]}

    asyncio.run(run())
