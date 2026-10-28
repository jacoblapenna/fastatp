"""Firehose: live PeerServer over WebSocket, commit-order delivery,
verified consumption (RepoMirror), disconnect + cursor resume, and the
checkpoint fallback for cursors beyond the backfill window."""

import asyncio
import socket
from collections.abc import Awaitable
from typing import cast

import httpx
import pytest
import uvicorn
import websockets

from fastatp import Peer, PeerServer, RepoMirror, build_did_document, crypto
from fastatp.exceptions import RepoError
from fastatp.lex.types import Datetime
from fastatp.lexicon import Lexicon
from fastatp.repo.sync import decode_frame
from fastatp.server.peer_server import FIREHOSE_PATH, GET_REPO_PATH

DID = "did:web:producer.example"


class Reading(Lexicon):
    nsid = "com.example.telemetry.firehoseReading"
    seq: int
    at: Datetime


def _reading(i):
    return Reading(seq=i, at="2026-07-02T12:00:00Z")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def server():
    key = crypto.generate_keypair("k256")
    peer = Peer(did=DID, signing_key=key)
    srv = PeerServer(peer, backfill_window=8)
    port = _free_port()
    config = uvicorn.Config(srv.app, host="127.0.0.1", port=port, log_level="error")
    uv_server = uvicorn.Server(config)
    task = asyncio.create_task(uv_server.serve())
    while not uv_server.started:
        await asyncio.sleep(0.01)

    doc = build_did_document(did=DID, signing_key=key.public_key())

    async def resolver(did):
        return doc if did == DID else None

    yield srv, f"ws://127.0.0.1:{port}{FIREHOSE_PATH}", f"http://127.0.0.1:{port}", resolver
    uv_server.should_exit = True
    await task


async def _recv_frame(ws, timeout=5.0):
    return decode_frame(await asyncio.wait_for(ws.recv(), timeout))


async def test_commit_order_delivery_and_verification(server):
    srv, ws_url, _http, resolver = server
    mirror = RepoMirror(DID, resolver=resolver)
    async with websockets.connect(ws_url) as ws:
        await asyncio.sleep(0.05)  # subscription registered before writes
        for i in range(3):
            srv.put_record(Reading.nsid, f"r{i}", _reading(i))
        seen = []
        for _ in range(3):
            header, body = await _recv_frame(ws)
            assert header["t"] == "#commit"
            records = await mirror.apply_commit(body)
            seen.append((body["seq"], records[0]["record"]["seq"]))
    # seq 1 and 2 are the repo's #identity and #account announcements
    assert seen == [(3, 0), (4, 1), (5, 2)]
    assert mirror.rev == srv.repo.rev
    r2 = mirror.get_record(Reading.nsid, "r2")
    assert r2 is not None
    assert r2["seq"] == 2


async def test_disconnect_and_cursor_resume(server):
    srv, ws_url, _http, resolver = server
    mirror = RepoMirror(DID, resolver=resolver)

    async with websockets.connect(ws_url) as ws:
        await asyncio.sleep(0.05)
        srv.put_record(Reading.nsid, "a", _reading(1))
        _, body = await _recv_frame(ws)
        await mirror.apply_commit(body)
    # consumer is now offline; producer keeps committing
    srv.put_record(Reading.nsid, "b", _reading(2))
    srv.put_record(Reading.nsid, "c", _reading(3))

    # reconnect with the kept cursor: replay only the two missed commits
    async with websockets.connect(f"{ws_url}?cursor={mirror.seq}") as ws:
        for expected_seq in (4, 5):
            header, body = await _recv_frame(ws)
            assert header["t"] == "#commit"
            assert body["seq"] == expected_seq
            await mirror.apply_commit(body)
    rec_c = mirror.get_record(Reading.nsid, "c")
    assert rec_c is not None
    assert rec_c["seq"] == 3
    assert mirror.rev == srv.repo.rev


async def test_outdated_cursor_falls_back_to_checkpoint(server):
    srv, ws_url, http, resolver = server
    # push far beyond the backfill window (8)
    for i in range(20):
        srv.put_record(Reading.nsid, f"r{i}", _reading(i))
    # a consumer whose cursor predates the window
    mirror = RepoMirror(DID, resolver=resolver)
    async with websockets.connect(f"{ws_url}?cursor=1") as ws:
        header, body = await _recv_frame(ws)
        assert header["t"] == "#info" and body["name"] == "OutdatedCursor"
        header, body = await _recv_frame(ws)
        assert header["t"] == "#sync"
        await mirror.apply_sync(body)
        assert mirror.seq == 22  # 2 announcement frames + 20 commits
        # spec shape: the #sync carries the commit block only; the consumer
        # re-fetches the full state through getRepo
        assert mirror.resync_target is not None
        assert mirror.entries == {}
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{http}{GET_REPO_PATH}", params={"did": DID})
        await mirror.load_checkpoint(response.content)
        assert mirror.resync_target is None
        assert len(mirror.entries) == 20
        # and the stream continues live from the checkpoint
        srv.put_record(Reading.nsid, "after", _reading(99))
        header, body = await _recv_frame(ws)
        assert header["t"] == "#commit" and body["seq"] == 23
        await mirror.apply_commit(body)
    rec_after = mirror.get_record(Reading.nsid, "after")
    assert rec_after is not None
    assert rec_after["seq"] == 99
    assert len(mirror.entries) == 21
    assert mirror.rev == srv.repo.rev


async def test_future_cursor_rejected(server):
    _srv, ws_url, _http, _resolver = server
    async with websockets.connect(f"{ws_url}?cursor=999") as ws:
        with pytest.raises(RepoError, match="FutureCursor"):
            # the server only sends binary frames on this socket
            decode_frame(await asyncio.wait_for(cast("Awaitable[bytes]", ws.recv()), 5))


async def test_get_repo_checkpoint_route(server):
    srv, _ws, http, _resolver = server
    srv.put_record(Reading.nsid, "x", _reading(5))
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{http}{GET_REPO_PATH}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/vnd.ipld.car")
        assert response.headers["x-fastatp-rev"] == srv.repo.rev
        missing = await client.get(f"{http}{GET_REPO_PATH}?did=did:web:other.example")
        assert missing.status_code == 404

    from fastatp.repo.repo import Repo

    commit, entries, _ = Repo.read_car(response.content)
    assert commit.did == DID
    assert f"{Reading.nsid}/x" in entries


async def test_mirror_rejects_forged_commit(server):
    """A commit signed by the wrong key must not be accepted by consumers."""
    srv, _ws, _http, _resolver = server
    forger_key = crypto.generate_keypair("k256")
    forged_doc = build_did_document(did=DID, signing_key=forger_key.public_key())

    async def forged_resolver(did):  # resolves to the real producer's doc
        return forged_doc

    # a mirror resolving the honest document must reject events from a repo
    # signed with a different key
    honest_key_doc_resolver_mirror = RepoMirror(DID, resolver=forged_resolver)
    from fastatp.repo.sync import commit_frame_body

    event = srv.put_record(Reading.nsid, "forge-test", _reading(1))
    body = commit_frame_body(DID, srv.seq, event)
    with pytest.raises(RepoError, match="signature"):
        await honest_key_doc_resolver_mirror.apply_commit(body)
