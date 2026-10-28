"""Multi-repo hosting — one server, many repos, routed by DID.

Covers the registry + DID routing + per-repo firehose isolation + handle-only
writes. Read gating (the doorman) is a separate concern (test_doorman)."""

import asyncio

import httpx
import pytest
import websockets

from fastatp import Peer, PeerServer, RepoMirror, build_did_document, crypto
from fastatp.exceptions import RepoError
from fastatp.lex.types import Datetime
from fastatp.lexicon import Lexicon
from fastatp.repo.repo import Repo
from fastatp.repo.sync import decode_frame
from fastatp.server.peer_server import FIREHOSE_PATH, GET_REPO_PATH

PRIMARY_DID = "did:web:hub.example"


class Note(Lexicon):
    nsid = "com.example.note"
    text: str
    at: Datetime


def _note(t):
    return Note(text=t, at="2026-07-06T12:00:00Z")


def _did_key_repo() -> Repo:
    key = crypto.generate_keypair("k256")
    did = crypto.encode_did_key(key.public_key())
    return Repo(did, key)


@pytest.fixture
async def running(serve_app):
    """A PeerServer hosting a primary (did:web) repo, served under uvicorn."""
    key = crypto.generate_keypair("k256")
    peer = Peer(did=PRIMARY_DID, signing_key=key)
    srv = PeerServer(peer, backfill_window=16)
    doc = build_did_document(did=PRIMARY_DID, signing_key=key.public_key())

    async def resolver(did):
        return doc if did == PRIMARY_DID else None

    async with serve_app(srv.app) as port:
        yield srv, port, resolver


async def _recv(ws, timeout=5.0):
    return decode_frame(await asyncio.wait_for(ws.recv(), timeout))


async def test_getrepo_routes_by_did(running):
    srv, port, _resolver = running
    channel = _did_key_repo()
    handle = srv.register(channel, public=True)
    handle.put_record(Note.nsid, "a", _note("in-channel"))

    base = f"http://127.0.0.1:{port}{GET_REPO_PATH}"
    async with httpx.AsyncClient() as client:
        # no did -> primary
        r_primary = await client.get(base)
        assert r_primary.status_code == 200
        assert r_primary.headers["x-fastatp-rev"] == srv.repo.rev
        # ?did=channel -> the registered repo (distinct rev)
        r_channel = await client.get(base, params={"did": channel.did})
        assert r_channel.status_code == 200
        assert r_channel.headers["x-fastatp-rev"] == channel.rev
        # unknown did -> 404 RepoNotFound
        r_missing = await client.get(base, params={"did": "did:web:nope.example"})
        assert r_missing.status_code == 404
        assert r_missing.json()["error"] == "RepoNotFound"


async def test_per_repo_firehose_isolation_and_independent_seq(running):
    """A follower of repo A never sees repo B's frames, and each repo's seq
    counts independently from 1."""
    srv, port, resolver = running
    channel = _did_key_repo()
    handle = srv.register(channel, public=True)

    ws_base = f"ws://127.0.0.1:{port}{FIREHOSE_PATH}"
    primary_mirror = RepoMirror(PRIMARY_DID, resolver=resolver)

    async def channel_resolver(did):
        return None  # did:key resolves from the DID itself in RepoMirror

    ch_mirror = RepoMirror(channel.did, resolver=channel_resolver)

    async with (
        websockets.connect(ws_base) as ws_primary,
        websockets.connect(f"{ws_base}?did={channel.did}") as ws_channel,
    ):
        await asyncio.sleep(0.05)  # both subscriptions registered
        srv.put_record(Note.nsid, "p0", _note("primary-0"))
        handle.put_record(Note.nsid, "c0", _note("channel-0"))
        srv.put_record(Note.nsid, "p1", _note("primary-1"))

        # primary follower sees ONLY the two primary commits, seq 1,2
        _h1, b1 = await _recv(ws_primary)
        _h2, b2 = await _recv(ws_primary)
        # seq 1,2 are each repo's #identity/#account announcements
        assert (b1["seq"], b2["seq"]) == (3, 4)
        recs = await primary_mirror.apply_commit(b1)
        assert recs[0]["record"]["text"] == "primary-0"
        await primary_mirror.apply_commit(b2)

        # channel follower sees ONLY its one commit, at its own seq 1
        _hc, bc = await _recv(ws_channel)
        assert bc["seq"] == 3
        assert bc["repo"] == channel.did
        crecs = await ch_mirror.apply_commit(bc)
        assert crecs[0]["record"]["text"] == "channel-0"

        # no cross-bleed: channel follower has nothing else queued
        with pytest.raises(asyncio.TimeoutError):
            await _recv(ws_channel, timeout=0.2)

    assert srv.seq == 4  # primary (2 announcements + 2 commits)
    assert handle.seq == 3  # channel, independent (2 announcements + 1 commit)


async def test_handle_only_writes_land_in_own_repo(running):
    srv, _port, _resolver = running
    channel = _did_key_repo()
    handle = srv.register(channel)
    handle.put_record(Note.nsid, "x", _note("channel-only"))

    # the record is in the channel repo, signed under the channel DID
    assert handle.repo.get_record(Note.nsid, "x")["text"] == "channel-only"
    assert handle.repo.commit.did == channel.did
    # the primary repo never saw it — no cross-repo write path exists
    assert srv.repo.get_record(Note.nsid, "x") is None
    # there is deliberately no string-target write API on the server
    assert not hasattr(srv, "put_record_to")


async def test_duplicate_register_and_primary_guard(running):
    srv, _port, _resolver = running
    channel = _did_key_repo()
    srv.register(channel)
    with pytest.raises(ValueError, match="already hosting"):
        srv.register(channel)
    with pytest.raises(ValueError, match="primary"):
        srv.unregister(srv.repo.did)
    assert set(srv.hosts()) == {srv.repo.did, channel.did}


async def test_unregister_cuts_subscribers(running):
    srv, port, _resolver = running
    channel = _did_key_repo()
    handle = srv.register(channel, public=True)
    ws_base = f"ws://127.0.0.1:{port}{FIREHOSE_PATH}"
    async with websockets.connect(f"{ws_base}?did={channel.did}") as ws:
        await asyncio.sleep(0.05)
        handle.put_record(Note.nsid, "a", _note("live"))
        _, body = await _recv(ws)
        assert body["seq"] == 3
        srv.unregister(channel.did)
        # teardown announces the hosting change (#account active=false), then
        # the poisoned subscriber gets a ConsumerTooSlow error frame
        # (decode_frame surfaces error frames as RepoError) and the socket closes
        header, body = await _recv(ws)
        assert header["t"] == "#account"
        assert body["active"] is False and body["status"] == "deactivated"
        with pytest.raises(RepoError, match="ConsumerTooSlow"):
            await _recv(ws)
    # the repo is no longer routable
    async with httpx.AsyncClient() as client:
        r = await client.get(f"http://127.0.0.1:{port}{GET_REPO_PATH}", params={"did": channel.did})
        assert r.status_code == 404


async def test_subscribe_unknown_repo_is_refused(running):
    _srv, port, _resolver = running
    ws_base = f"ws://127.0.0.1:{port}{FIREHOSE_PATH}"
    async with websockets.connect(f"{ws_base}?did=did:web:ghost.example") as ws:
        with pytest.raises(RepoError, match="RepoNotFound"):
            await _recv(ws)
