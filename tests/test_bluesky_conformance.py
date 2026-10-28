"""Sync-spec conformance — the firehose a strict relay accepts.

Every emitted #commit must verify by operation inversion (the sync-v1.1
relay algorithm) standalone, with nothing but the event's own bytes; the
discovery/resync routes serve relay bootstrap; #account/#identity frames
announce hosting; #sync carries the commit block only.
"""

import asyncio
from typing import Any, cast

import httpx
import websockets

from fastatp import Peer, PeerServer, build_did_document, crypto
from fastatp.cid import CID
from fastatp.repo.car import decode_car
from fastatp.repo.repo import Repo, WriteOp
from fastatp.repo.sync import (
    RepoMirror,
    account_frame_body,
    commit_frame_body,
    decode_frame,
    identity_frame_body,
    verify_commit_inversion,
)
from fastatp.server.peer_server import (
    FIREHOSE_PATH,
    GET_LATEST_COMMIT_PATH,
    GET_RECORD_PATH,
    LIST_REPOS_PATH,
)

DID = "did:web:conform.example"
NSID = "com.example.gate14.item"


def _note(i: int) -> dict[str, Any]:
    return {"$type": NSID, "n": i}


def _repo() -> Repo:
    return Repo(DID, crypto.generate_keypair("k256"))


def _assert_inverts(event, seq: int = 1) -> None:
    """The event body alone must satisfy the relay's inversion check."""
    verify_commit_inversion(commit_frame_body(DID, seq, event))


# --- every emitted commit passes inversion, standalone -------------------------


def test_single_op_commits_invert():
    repo = _repo()
    _assert_inverts(repo.put_record(NSID, "only", _note(0)))  # create
    _assert_inverts(repo.put_record(NSID, "only", _note(1)))  # update
    _assert_inverts(repo.delete_record(NSID, "only"))  # delete
    _assert_inverts(repo.recommit())  # empty ops (re-sign)


def test_deep_tree_commits_invert():
    """Creates, updates, and deletes across a many-node tree — including ops
    whose MST neighbors live in otherwise-untouched nodes (the case a
    new-nodes-only diff gets wrong)."""
    repo = _repo()
    for i in range(120):
        _assert_inverts(repo.put_record(NSID, f"k{i:03d}", _note(i)))
    # scattered deletes: each op's neighbors sit in untouched nodes
    for i in (0, 17, 39, 58, 77, 96, 119):
        _assert_inverts(repo.delete_record(NSID, f"k{i:03d}"))
    # scattered updates
    for i in (1, 40, 95):
        _assert_inverts(repo.put_record(NSID, f"k{i:03d}", _note(1000 + i)))


def test_batched_writes_invert():
    repo = _repo()
    for i in range(60):
        repo.put_record(NSID, f"k{i:03d}", _note(i))
    batch = [
        WriteOp("create", NSID, "zz-new-1", _note(-1)),
        WriteOp("create", NSID, "aa-new-2", _note(-2)),
        WriteOp("update", NSID, "k030", _note(9999)),
        WriteOp("delete", NSID, "k010"),
        WriteOp("delete", NSID, "k055"),
    ]
    _assert_inverts(repo.apply_writes(batch))


def test_tampered_events_fail_inversion():
    import pytest

    from fastatp.exceptions import RepoError

    repo = _repo()
    repo.put_record(NSID, "a", _note(0))
    event = repo.put_record(NSID, "b", _note(1))
    # lie about the op path
    body = commit_frame_body(DID, 2, event)
    body["ops"] = [dict(body["ops"][0], path=f"{NSID}/liar")]
    with pytest.raises(RepoError):
        verify_commit_inversion(body)
    # claim a different prevData
    body = commit_frame_body(DID, 2, event)
    body["prevData"] = CID.for_raw(b"not-the-previous-root")
    with pytest.raises(RepoError, match="prevData"):
        verify_commit_inversion(body)
    # drop the ops entirely: the diff no longer inverts to prevData
    body = commit_frame_body(DID, 2, event)
    body["ops"] = []
    with pytest.raises(RepoError):
        verify_commit_inversion(body)


def test_mirror_round_trip_with_inversion():
    """The consumer path (RepoMirror.apply_commit) runs the inversion check
    on every event and still tracks state exactly."""

    async def run() -> None:
        key = crypto.generate_keypair("k256")
        doc = build_did_document(did=DID, signing_key=key.public_key())

        async def resolver(did):
            return doc if did == DID else None

        repo = Repo(DID, key)
        mirror = RepoMirror(DID, resolver=resolver)
        seq = 0
        for i in range(30):
            seq += 1
            await mirror.apply_commit(
                commit_frame_body(DID, seq, repo.put_record(NSID, f"r{i:02d}", _note(i)))
            )
        for i in (3, 11, 27):
            seq += 1
            await mirror.apply_commit(
                commit_frame_body(DID, seq, repo.delete_record(NSID, f"r{i:02d}"))
            )
        assert mirror.rev == repo.rev
        assert len(mirror.entries) == 27

    asyncio.run(run())


# --- frame hygiene: #sync carries the commit block only ------------------------


def test_sync_frame_commit_block_only():
    peer = Peer(did=DID, signing_key=crypto.generate_keypair("k256"))
    srv = PeerServer(peer)
    for i in range(5):
        srv.put_record(NSID, f"s{i}", _note(i))
    header, body = decode_frame(srv._primary.sync_frame())
    assert header["t"] == "#sync"
    roots, blocks = decode_car(body["blocks"])
    assert roots == [srv.repo.commit_cid]
    assert set(blocks) == {srv.repo.commit_cid}  # the commit block, nothing else
    assert body["rev"] == srv.repo.rev


# --- frame builders -------------------------------------------------------------


def test_account_and_identity_bodies():
    t = "2026-07-15T00:00:00Z"
    assert account_frame_body(DID, 7, t, active=True) == {
        "seq": 7,
        "did": DID,
        "time": t,
        "active": True,
    }
    assert account_frame_body(DID, 8, t, active=False, status="deactivated")["status"] == (
        "deactivated"
    )
    assert identity_frame_body(DID, 9, t) == {"seq": 9, "did": DID, "time": t}
    assert identity_frame_body(DID, 9, t, handle="alice.example.com")["handle"] == (
        "alice.example.com"
    )


def test_hosted_repo_announces_before_commits(serve_app):
    """A fresh subscriber replaying from cursor 0 sees #identity and #account
    {active: true} before any #commit (relays ignore commits from accounts
    not known to be active)."""

    async def run() -> None:
        peer = Peer(did=DID, signing_key=crypto.generate_keypair("k256"))
        srv = PeerServer(peer)
        srv.put_record(NSID, "x", _note(0))
        async with serve_app(srv.app) as port:
            url = f"ws://127.0.0.1:{port}{FIREHOSE_PATH}?cursor=0"
            async with websockets.connect(url) as ws:
                frames = []
                for _ in range(3):
                    frames.append(decode_frame(await asyncio.wait_for(cast("Any", ws.recv()), 5.0)))
        types = [h["t"] for h, _ in frames]
        assert types == ["#identity", "#account", "#commit"]
        account_body = frames[1][1]
        assert account_body["active"] is True and account_body["did"] == DID
        assert [b["seq"] for _, b in frames] == [1, 2, 3]

    asyncio.run(run())


# --- discovery / resync routes ---------------------------------------------------


def test_discovery_routes(serve_app):
    async def run() -> None:
        peer = Peer(did=DID, signing_key=crypto.generate_keypair("k256"))
        srv = PeerServer(peer)
        srv.put_record(NSID, "rec1", _note(1))
        gated = Repo("did:web:gated.example", crypto.generate_keypair("k256"))
        srv.register(gated, public=False)
        async with serve_app(srv.app) as port:
            base = f"http://127.0.0.1:{port}"
            async with httpx.AsyncClient() as client:
                # listRepos: the gated repo is invisible to a stranger
                r = await client.get(f"{base}{LIST_REPOS_PATH}")
                assert r.status_code == 200
                repos = r.json()["repos"]
                assert [x["did"] for x in repos] == [DID]
                assert repos[0]["rev"] == srv.repo.rev
                assert repos[0]["head"] == srv.repo.commit_cid.encode()
                assert repos[0]["active"] is True

                # getLatestCommit
                r = await client.get(f"{base}{GET_LATEST_COMMIT_PATH}", params={"did": DID})
                assert r.status_code == 200
                assert r.json() == {"cid": srv.repo.commit_cid.encode(), "rev": srv.repo.rev}
                r = await client.get(f"{base}{GET_LATEST_COMMIT_PATH}")
                assert r.status_code == 400
                r = await client.get(
                    f"{base}{GET_LATEST_COMMIT_PATH}", params={"did": "did:web:nobody.example"}
                )
                assert r.status_code == 404
                # a gated repo is indistinguishable from an absent one? No —
                # fail-closed: strangers get 403 only if they could name it;
                # the doorman refuses the read either way
                r = await client.get(f"{base}{GET_LATEST_COMMIT_PATH}", params={"did": gated.did})
                assert r.status_code == 403

                # getRecord: the body IS Repo.prove()
                r = await client.get(
                    f"{base}{GET_RECORD_PATH}",
                    params={"did": DID, "collection": NSID, "rkey": "rec1"},
                )
                assert r.status_code == 200
                assert r.headers["content-type"].startswith("application/vnd.ipld.car")
                assert r.content == srv.repo.prove(NSID, "rec1")
                r = await client.get(
                    f"{base}{GET_RECORD_PATH}",
                    params={"did": DID, "collection": NSID, "rkey": "missing"},
                )
                assert r.status_code == 404
                assert r.json()["error"] == "RecordNotFound"
                r = await client.get(f"{base}{GET_RECORD_PATH}", params={"did": DID})
                assert r.status_code == 400

    asyncio.run(run())
