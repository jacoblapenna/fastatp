"""Frozen repos: a host serves a repo whose pen lives elsewhere.

Reopened with no pen, a repo serves ``getRepo`` and the firehose (public, or
gated to live capability holders) and refuses every commit. Pushes land
through ``import_car``, which signs nothing.
"""

import asyncio
from collections.abc import Awaitable
from typing import cast
from urllib.parse import urlencode

import httpx
import pytest
import websockets

from fastatp import Peer, PeerServer, Repo, crypto
from fastatp.authz.capability import Capability
from fastatp.exceptions import RepoError
from fastatp.repo.store import MemoryBlockStore
from fastatp.repo.sync import decode_frame
from fastatp.server.peer_server import FIREHOSE_PATH, GET_REPO_PATH, ResolvedCaller
from fastatp.tid import generate_tid

DID = "did:key:z6MkfrozenExampleRepoWhosePenLivesElsewhere"
NOTE = "com.example.note"


async def _penned_repo(store):
    """The pen's own copy: it commits; the host only ever imports its CARs."""
    key = crypto.generate_keypair("p256")
    repo = await Repo.create(DID, key, store=store)
    await repo.put_record(NOTE, "one", {"$type": NOTE, "text": "first"})
    return key, repo


def _frozen_copy(penned: Repo) -> Repo:
    """What the host opens: the same blocks, the stored head, no pen."""
    return Repo(DID, None, store=penned.store, head_cid=penned.commit_cid)


async def test_a_frozen_repo_serves_and_never_commits():
    key, penned = await _penned_repo(MemoryBlockStore())
    frozen = _frozen_copy(penned)
    assert frozen.frozen and not penned.frozen
    assert frozen.signer is None
    assert frozen.rev == penned.rev
    assert frozen.get_record(NOTE, "one") == {"$type": NOTE, "text": "first"}
    assert frozen.export_car() == penned.export_car()
    assert frozen.commit.verify(key.public_key())

    head = frozen.commit_cid
    with pytest.raises(RepoError, match="frozen"):
        await frozen.put_record(NOTE, "two", {"$type": NOTE, "text": "never"})
    with pytest.raises(RepoError, match="frozen"):
        await frozen.delete_record(NOTE, "one")
    with pytest.raises(RepoError, match="frozen"):
        await frozen.recommit()
    assert frozen.commit_cid == head
    assert frozen.get_record(NOTE, "two") is None


async def test_a_fresh_repo_cannot_be_frozen():
    with pytest.raises(RepoError, match="needs a pen"):
        await Repo.create(DID, None)


async def test_a_push_lands_in_a_frozen_repo_through_import_car():
    key, penned = await _penned_repo(MemoryBlockStore())
    frozen = Repo(DID, None, store=MemoryBlockStore())  # a host that has nothing yet
    await frozen.import_car(penned.export_car(), verify_key=key.public_key())
    assert frozen.rev == penned.rev

    await penned.put_record(NOTE, "two", {"$type": NOTE, "text": "pushed"})
    await frozen.import_car(penned.export_car(), verify_key=key.public_key(), newer_than=frozen.rev)
    assert frozen.rev == penned.rev
    assert frozen.get_record(NOTE, "two") == {"$type": NOTE, "text": "pushed"}
    assert frozen.frozen


# --- served through a PeerServer ----------------------------------------------------


async def _host(caller: str | None = None) -> PeerServer:
    resolved = ResolvedCaller(caller, "signature") if caller is not None else None
    return await PeerServer.create(
        Peer(did="did:web:host.example", signing_key=crypto.generate_keypair("k256")),
        identity_resolver=lambda conn: resolved,
    )


async def _get_repo(port: int, did: str) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.get(f"http://127.0.0.1:{port}{GET_REPO_PATH}", params={"did": did})


async def _first_frame(port: int, did: str, kind: str):
    query = urlencode({"did": did, "cursor": 0})
    async with websockets.connect(f"ws://127.0.0.1:{port}{FIREHOSE_PATH}?{query}") as ws:
        for _ in range(8):
            header, body = decode_frame(
                await asyncio.wait_for(cast("Awaitable[bytes]", ws.recv()), 5.0)
            )
            if header.get("t") == kind:
                return header, body
        raise AssertionError(f"no {kind} frame within 8 frames")


async def test_a_frozen_repo_is_served_public_and_refuses_writes_at_the_handle(serve_app):
    _key, penned = await _penned_repo(MemoryBlockStore())
    srv = await _host()
    handle = await srv.register(_frozen_copy(penned), public=True)

    async with serve_app(srv.app) as port:
        got = await _get_repo(port, DID)
        assert got.status_code == 200
        assert got.content == penned.export_car()
        # the firehose announces the repo as hosted and active; a consumer
        # then fetches the head with getRepo exactly as for any other repo
        _header, body = await _first_frame(port, DID, "#account")
        assert body["did"] == DID and body["active"] is True
        with pytest.raises(RepoError, match="frozen"):
            await handle.put_record(NOTE, "two", {"$type": NOTE, "text": "never"})
        assert (await _get_repo(port, DID)).content == penned.export_car()


async def test_a_gated_frozen_repo_admits_only_live_capability_holders(serve_app):
    """The capability record is the owner's: written by the pen, pushed to the
    host, and the host enforces it without ever being able to write one."""
    reader = crypto.encode_did_key(crypto.generate_keypair("p256").public_key())
    stranger = crypto.encode_did_key(crypto.generate_keypair("p256").public_key())
    key, penned = await _penned_repo(MemoryBlockStore())
    await penned.put_record(
        Capability.nsid, generate_tid(), Capability(subject=reader, actions=["subscribe"])
    )
    frozen = Repo(DID, None, store=MemoryBlockStore())
    await frozen.import_car(penned.export_car(), verify_key=key.public_key())

    admitted = await _host(reader)
    refused = await _host(stranger)
    nobody = await _host(None)
    for srv in (admitted, refused, nobody):
        await srv.register(frozen, public=False)

    async with serve_app(admitted.app) as port:
        assert (await _get_repo(port, DID)).status_code == 200
    async with serve_app(refused.app) as port:
        assert (await _get_repo(port, DID)).status_code == 404
    async with serve_app(nobody.app) as port:
        assert (await _get_repo(port, DID)).status_code == 404


async def test_a_peer_without_a_pen_can_host_a_frozen_repo():
    """A pure host: no pen anywhere on it. It still needs a repo to serve."""
    _key, penned = await _penned_repo(MemoryBlockStore())
    host = Peer(did=DID)
    with pytest.raises(ValueError, match="signing_key"):
        await host.create_repo()
    srv = PeerServer(host, _frozen_copy(penned))
    assert srv.repo.frozen


async def test_a_frozen_repo_refuses_an_unverified_push():
    """A host with no pen has no other way to know the bytes are the owner's,
    so the verification the docs ask of every push handler is enforced."""
    key, penned = await _penned_repo(MemoryBlockStore())
    frozen = Repo(DID, None, store=MemoryBlockStore())
    with pytest.raises(RepoError, match="verified pushes"):
        await frozen.import_car(penned.export_car())
    assert frozen._commit is None  # nothing landed
    await frozen.import_car(penned.export_car(), verify_key=key.public_key())
    assert frozen.rev == penned.rev
    assert penned.frozen is False
    await penned.import_car(penned.export_car())  # a penned repo may still import unverified
