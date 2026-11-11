"""Owner pull: a host serves a gated repo to the pen that writes it.

The owner is not a subscriber. When a repo is registered with
``owner_pull``, the pen that signs its commits reads it back with no
capability record — the peer reaching its own storage. Off by default, the
caller still has to prove the pen, and everyone else goes through the
capability rule exactly as before.
"""

import asyncio
import base64
from collections.abc import Awaitable
from types import SimpleNamespace
from typing import cast
from urllib.parse import urlencode

import httpx
import pytest
import websockets

from fastatp import Peer, PeerServer, crypto
from fastatp.authz.capability import Capability
from fastatp.exceptions import RepoError
from fastatp.repo.repo import Repo
from fastatp.repo.sync import decode_frame
from fastatp.server.browser_gate import CHALLENGE_PATH, BrowserGate, challenge_message
from fastatp.server.peer_server import FIREHOSE_PATH, GET_REPO_PATH
from fastatp.tid import generate_tid

ROOT = "did:web:user.shelf.example:aaaabbbbccccddddeeeeffffgggghhhh"


def _pen(curve: str = "p256"):
    key = crypto.generate_keypair(curve)
    return key, crypto.encode_did_key(key.public_key())


def _server(caller: str | None) -> PeerServer:
    """A server whose doorman identity is whatever the test says it is."""
    builder_key = crypto.generate_keypair("k256")
    return PeerServer(
        Peer(did="did:web:builder.example", signing_key=builder_key),
        identity_resolver=lambda conn: caller,
    )


async def _get_repo(port: int, did: str, extra: dict[str, str] | None = None) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.get(
            f"http://127.0.0.1:{port}{GET_REPO_PATH}", params={"did": did, **(extra or {})}
        )


async def _first_commit_frame(port: int, did: str):
    query = urlencode({"did": did, "cursor": 0})
    async with websockets.connect(f"ws://127.0.0.1:{port}{FIREHOSE_PATH}?{query}") as ws:
        for _ in range(8):
            header, body = decode_frame(
                await asyncio.wait_for(cast("Awaitable[bytes]", ws.recv()), 5.0)
            )
            if header.get("t") == "#commit":
                return header, body
        raise AssertionError("no #commit frame within 8 frames")


# --- the rule ---------------------------------------------------------------------


async def test_off_by_default_the_owner_is_just_another_stranger(serve_app):
    pen_key, pen_did = _pen()
    srv = _server(caller=pen_did)
    srv.register(Repo(ROOT, pen_key))  # gated, no owner_pull, no capability

    async with serve_app(srv.app) as port:
        response = await _get_repo(port, ROOT)
        assert response.status_code == 403
        assert response.json()["error"] == "NotAuthorized"


async def test_the_pen_that_signed_the_head_reads_with_no_capability(serve_app):
    pen_key, pen_did = _pen()
    srv = _server(caller=pen_did)
    handle = srv.register(Repo(ROOT, pen_key), owner_pull=True)
    handle.put_record("com.example.note", "n", {"$type": "com.example.note", "text": "mine"})
    assert handle.owner_pull is True

    async with serve_app(srv.app) as port:
        assert (await _get_repo(port, ROOT)).status_code == 200
        header, body = await _first_commit_frame(port, ROOT)
        assert header["t"] == "#commit" and body["repo"] == ROOT


async def test_a_different_pen_is_refused_and_so_is_no_identity(serve_app):
    pen_key, _ = _pen()
    _other_key, other_did = _pen()
    refused = _server(caller=other_did)
    refused.register(Repo(ROOT, pen_key), owner_pull=True)
    nobody = _server(caller=None)
    nobody.register(Repo(ROOT, pen_key), owner_pull=True)

    async with serve_app(refused.app) as port:
        assert (await _get_repo(port, ROOT)).status_code == 403
        with pytest.raises(RepoError, match="NotAuthorized"):
            await _first_commit_frame(port, ROOT)
    async with serve_app(nobody.app) as port:
        assert (await _get_repo(port, ROOT)).status_code == 403


async def test_the_identity_itself_is_an_owner_of_its_own_repo(serve_app):
    """A caller resolved to the repo's own DID got there through a delegation
    record in this repo (a device speaking for the identity), so it reads
    with no capability. Only when owner pull is on."""
    pen_key, _ = _pen()
    on = _server(caller=ROOT)
    on.register(Repo(ROOT, pen_key), owner_pull=True)
    off = _server(caller=ROOT)
    off.register(Repo(ROOT, pen_key))

    async with serve_app(on.app) as port:
        assert (await _get_repo(port, ROOT)).status_code == 200
    async with serve_app(off.app) as port:
        assert (await _get_repo(port, ROOT)).status_code == 403


async def test_another_identity_is_never_an_owner_but_capabilities_still_work(serve_app):
    """A did:web caller that is not this repo's DID is some other identity: it
    falls through to the capability rule like any reader."""
    pen_key, _ = _pen()
    other = "did:web:other.example:x"
    srv = _server(caller=other)
    handle = srv.register(Repo(ROOT, pen_key), owner_pull=True)

    async with serve_app(srv.app) as port:
        assert (await _get_repo(port, ROOT)).status_code == 403
        handle.put_record(
            Capability.nsid, generate_tid(), Capability(subject=other, actions=["subscribe"])
        )
        assert (await _get_repo(port, ROOT)).status_code == 200


async def test_a_builder_rule_names_the_current_pen_before_it_has_signed(serve_app):
    """After a recovery the chain head names the new pen, but the hosted
    head commit still carries the old pen's signature until the new pen
    pushes. The host's rule answers from the chain, so the new pen pulls and
    the old one is out at once."""
    old_key, old_did = _pen()
    _new_key, new_did = _pen()
    current = {"pen": old_did}  # the host's chain head, mutable like a shelf

    def from_chain(repo: Repo) -> str | None:
        assert repo.did == ROOT
        return current["pen"]

    new_pen_calls = _server(caller=new_did)
    new_pen_calls.register(Repo(ROOT, old_key), owner_pull=from_chain)
    old_pen_calls = _server(caller=old_did)
    old_pen_calls.register(Repo(ROOT, old_key), owner_pull=from_chain)

    async with serve_app(new_pen_calls.app) as port:
        assert (await _get_repo(port, ROOT)).status_code == 403  # not rotated yet
        current["pen"] = new_did  # rotation lands on the shelf
        assert (await _get_repo(port, ROOT)).status_code == 200  # before any push
    async with serve_app(old_pen_calls.app) as port:
        assert (await _get_repo(port, ROOT)).status_code == 403  # the head still bears its sig


async def test_the_default_rule_keeps_a_rotated_out_pen_until_the_new_one_pushes(serve_app):
    """Documented limit of ``owner_pull=True``: the head commit is the only
    evidence, so the old pen reads until the new pen's first push. A host
    that holds the chain passes a rule instead."""
    old_key, old_did = _pen()
    new_key, _ = _pen()
    srv = _server(caller=old_did)
    handle = srv.register(Repo(ROOT, old_key), owner_pull=True)

    async with serve_app(srv.app) as port:
        assert (await _get_repo(port, ROOT)).status_code == 200
        handle.repo.signing_key = new_key  # the new pen's first commit lands
        handle.put_record("com.example.note", "n", {"$type": "com.example.note", "text": "new"})
        assert (await _get_repo(port, ROOT)).status_code == 403


def test_a_non_did_key_caller_and_a_malformed_did_key_are_not_owners():
    pen_key, pen_did = _pen()
    host = SimpleNamespace(repo=Repo(ROOT, pen_key), owner_pull=True, did=ROOT)
    assert PeerServer._is_owner(pen_did, host) is True  # type: ignore[arg-type]
    assert PeerServer._is_owner(ROOT, host) is True  # type: ignore[arg-type]
    assert PeerServer._is_owner("did:web:x", host) is False  # type: ignore[arg-type]
    assert PeerServer._is_owner("did:key:zNotAKey", host) is False  # type: ignore[arg-type]


# --- through the browser gate: the knock with no iss is the owner's ----------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


async def _nonce(port: int) -> str:
    async with httpx.AsyncClient() as client:
        return (await client.get(f"http://127.0.0.1:{port}{CHALLENGE_PATH}")).json()["nonce"]


async def test_the_browser_pen_pulls_its_own_repo_with_no_record_naming_it(serve_app):
    """A device's pen gets its repo back from the host: knock naming the repo,
    ``iss`` omitted (the pen is the caller), no capability, no delegation."""
    pen_key, pen_did = _pen()
    builder_key = crypto.generate_keypair("k256")
    srv = PeerServer(Peer(did="did:web:builder.example", signing_key=builder_key))
    gate = BrowserGate(ttl=5.0)
    gate.install(srv)
    device_repo = pen_did  # a device repo: its DID is its pen
    srv.register(Repo(device_repo, pen_key), owner_pull=True)
    stranger_key, stranger_did = _pen()

    def knock(key, as_pen: str, nonce: str) -> dict[str, str]:
        sig = _b64url(crypto.sign_message(key, challenge_message(device_repo, nonce)))
        return {"pen": as_pen, "nonce": nonce, "sig": sig}

    async with serve_app(srv.app) as port:
        nonce = await _nonce(port)
        owner = knock(pen_key, pen_did, nonce)
        assert (await _get_repo(port, device_repo, owner)).status_code == 200
        replay = await _get_repo(port, device_repo, owner)
        assert replay.status_code == 403  # the nonce was spent
        nonce = await _nonce(port)
        stranger = knock(stranger_key, stranger_did, nonce)
        assert (await _get_repo(port, device_repo, stranger)).status_code == 403
        nonce = await _nonce(port)
        forged = knock(stranger_key, pen_did, nonce)  # claims the pen, can't sign for it
        assert (await _get_repo(port, device_repo, forged)).status_code == 403


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
