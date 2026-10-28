"""Whole-repo TTL / expiry + reclamation.

An ephemeral repo (a private channel) is registered with a TTL. Past
expiry it is unroutable, its live subscribers are cut, and the host — with its
repo and block store — is released."""

import asyncio
import gc
import weakref
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from typing import cast

import httpx
import pytest
import websockets

from fastatp import Peer, PeerServer, crypto
from fastatp.exceptions import RepoError
from fastatp.repo.repo import Repo
from fastatp.repo.sync import decode_frame
from fastatp.server.peer_server import FIREHOSE_PATH, GET_REPO_PATH

PRIMARY_DID = "did:web:hub.example"


def _peer():
    return Peer(did=PRIMARY_DID, signing_key=crypto.generate_keypair("k256"))


def _channel_repo() -> Repo:
    key = crypto.generate_keypair("k256")
    return Repo(crypto.encode_did_key(key.public_key()), key)


def test_ttl_and_expires_at_are_mutually_exclusive():
    srv = PeerServer(_peer())
    with pytest.raises(ValueError, match="not both"):
        srv.register(_channel_repo(), ttl=60, expires_at=datetime.now(timezone.utc))


def test_expired_repo_is_unroutable_and_reaped_lazily():
    srv = PeerServer(_peer())
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    handle = srv.register(_channel_repo(), public=True, expires_at=past)
    did = handle.did
    assert did in srv.hosts()
    # first access past expiry reaps it and routes as RepoNotFound
    assert srv._resolve_host(did) is None
    assert did not in srv.hosts()


def test_reap_expired_returns_and_drops_due_repos():
    srv = PeerServer(_peer())
    live = srv.register(_channel_repo(), ttl=3600)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    dead = srv.register(_channel_repo(), expires_at=past)
    reaped = srv.reap_expired()
    assert reaped == [dead.did]
    assert set(srv.hosts()) == {srv.repo.did, live.did}


def test_dropped_repo_store_is_released():
    """Retiring an ephemeral repo releases its host, repo, and block store."""
    srv = PeerServer(_peer())
    handle = srv.register(_channel_repo())
    ref = weakref.ref(handle._host)
    did = handle.did
    srv.unregister(did)
    del handle
    gc.collect()
    assert ref() is None  # the host (and its repo/store) was reclaimed


async def test_expired_repo_cuts_live_subscriber(serve_app):
    srv = PeerServer(_peer())
    handle = srv.register(_channel_repo(), public=True, ttl=3600)
    async with serve_app(srv.app) as port:
        url = f"ws://127.0.0.1:{port}{FIREHOSE_PATH}?did={handle.did}"
        async with websockets.connect(url) as ws:
            await asyncio.sleep(0.05)
            note = {"$type": "com.example.note", "text": "x"}
            handle.put_record("com.example.note", "n", note)
            # the server only sends binary frames on this socket
            _, body = decode_frame(await asyncio.wait_for(cast("Awaitable[bytes]", ws.recv()), 5.0))
            assert body["seq"] == 3
            # force expiry and reap: a hosting farewell, then the live
            # subscriber is poisoned
            handle._host.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            assert srv.reap_expired() == [handle.did]
            header, body = decode_frame(
                await asyncio.wait_for(cast("Awaitable[bytes]", ws.recv()), 5.0)
            )
            assert header["t"] == "#account" and body["active"] is False
            with pytest.raises(RepoError, match="ConsumerTooSlow"):
                decode_frame(await asyncio.wait_for(cast("Awaitable[bytes]", ws.recv()), 5.0))
        # and it is no longer routable
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"http://127.0.0.1:{port}{GET_REPO_PATH}", params={"did": handle.did}
            )
            assert r.status_code == 404
