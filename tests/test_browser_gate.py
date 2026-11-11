"""The browser admission gate.

A single-use signed nonce challenge resolves a browser pen to a DID through
the live delegation chain; the doorman's membership check is unchanged. The
required gate tests: an admitted pen subscribes; a severed pen is refused at
its next connect; a replayed nonce is refused; no identity is refused.
"""

import asyncio
import base64
import time
from collections.abc import Awaitable
from typing import cast
from urllib.parse import urlencode

import httpx
import pytest
import websockets
from starlette.requests import HTTPConnection

from fastatp import Peer, PeerServer, crypto
from fastatp.authz.capability import Capability
from fastatp.authz.delegation import Delegation
from fastatp.exceptions import RepoError
from fastatp.repo.repo import Repo
from fastatp.repo.sync import decode_frame
from fastatp.server.browser_gate import (
    CHALLENGE_LABEL,
    CHALLENGE_PATH,
    MAX_NONCE_LEN,
    BrowserGate,
    challenge_message,
    compose_caller_resolvers,
)
from fastatp.server.peer_server import FIREHOSE_PATH, GET_REPO_PATH
from fastatp.tid import generate_tid

ROOT = "did:web:user.shelf.example:aaaabbbbccccddddeeeeffffgggghhhh"


def _server_with_gate() -> tuple[PeerServer, BrowserGate]:
    key = crypto.generate_keypair("k256")
    srv = PeerServer(Peer(did="did:web:builder.example", signing_key=key))
    gate = BrowserGate(ttl=5.0)
    gate.install(srv)
    return srv, gate


def _pen() -> tuple[object, str]:
    key = crypto.generate_keypair("p256")  # browser pens are p256
    return key, crypto.encode_did_key(key.public_key())


def _user_repo(srv: PeerServer, *, subject: str = ROOT):
    """Host ROOT's gated repo, holding its own subscribe capability."""
    repo = Repo(ROOT, crypto.generate_keypair("k256"))
    handle = srv.register(repo)  # gated by default
    handle.put_record(
        Capability.nsid, generate_tid(), Capability(subject=subject, actions=["subscribe"])
    )
    return handle


def _delegate(handle, pen_did: str) -> str:
    rkey = generate_tid()
    handle.put_record(Delegation.nsid, rkey, Delegation(issuer=ROOT, subject=pen_did))
    return rkey


async def _nonce(port: int) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://127.0.0.1:{port}{CHALLENGE_PATH}")
        assert response.status_code == 200
        return response.json()["nonce"]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sign(pen_key, nonce: str, *, repo_did: str = ROOT) -> str:
    """The knock, as the bundle's ``sign_challenge(pen, nonce, repo_did)`` makes it."""
    return _b64url(crypto.sign_message(pen_key, challenge_message(repo_did, nonce)))


def _params(pen_did: str, nonce: str, sig: str, *, iss: str | None = ROOT) -> dict[str, str]:
    params = {"pen": pen_did, "nonce": nonce, "sig": sig}
    if iss is not None:
        params["iss"] = iss
    return params


async def _subscribe_first_frame(port: int, did: str, extra: dict[str, str]):
    """First #commit frame (skipping the #identity/#account announcements)."""
    query = urlencode({"did": did, "cursor": 0, **extra})
    async with websockets.connect(f"ws://127.0.0.1:{port}{FIREHOSE_PATH}?{query}") as ws:
        for _ in range(8):
            header, body = decode_frame(
                await asyncio.wait_for(cast("Awaitable[bytes]", ws.recv()), 5.0)
            )
            if header.get("t") == "#commit":
                return header, body
        raise AssertionError("no #commit frame within 8 frames")


async def _get_repo(port: int, did: str, extra: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.get(
            f"http://127.0.0.1:{port}{GET_REPO_PATH}", params={"did": did, **extra}
        )


# --- the required gate tests ----------------------------------------------------


async def test_admitted_pen_subscribes_and_severed_pen_is_refused(serve_app):
    """The full loop: delegated pen passes the challenge and receives frames;
    after its delegation record is DELETED, the same pen with a fresh, valid
    challenge is refused at its next connect — absence is revocation."""
    srv, _gate = _server_with_gate()
    handle = _user_repo(srv)
    pen_key, pen_did = _pen()
    rkey = _delegate(handle, pen_did)
    handle.put_record("com.example.note", "n", {"$type": "com.example.note", "text": "hi"})

    async with serve_app(srv.app) as port:
        nonce = await _nonce(port)
        header, body = await _subscribe_first_frame(
            port, ROOT, _params(pen_did, nonce, _sign(pen_key, nonce))
        )
        assert header["t"] == "#commit" and body["repo"] == ROOT

        handle.delete_record(Delegation.nsid, rkey)  # sever: the revocation act
        nonce = await _nonce(port)  # fresh, valid challenge — chain is what fails
        with pytest.raises(RepoError, match="NotAuthorized"):
            await _subscribe_first_frame(port, ROOT, _params(pen_did, nonce, _sign(pen_key, nonce)))


async def test_replayed_nonce_is_refused(serve_app):
    srv, _gate = _server_with_gate()
    handle = _user_repo(srv)
    pen_key, pen_did = _pen()
    _delegate(handle, pen_did)

    async with serve_app(srv.app) as port:
        nonce = await _nonce(port)
        sig = _sign(pen_key, nonce)
        assert (await _get_repo(port, ROOT, _params(pen_did, nonce, sig))).status_code == 200
        replay = await _get_repo(port, ROOT, _params(pen_did, nonce, sig))
        assert replay.status_code == 403
        assert replay.json()["error"] == "NotAuthorized"


async def test_no_identity_is_refused_and_bad_signature_burns_the_nonce(serve_app):
    srv, _gate = _server_with_gate()
    _user_repo(srv)
    pen_key, pen_did = _pen()
    other_key, _ = _pen()

    async with serve_app(srv.app) as port:
        # no challenge params at all → the doorman sees no identity
        assert (await _get_repo(port, ROOT, {})).status_code == 403
        # signature by the wrong key → refused, and the nonce is consumed
        nonce = await _nonce(port)
        forged = _params(pen_did, nonce, _sign(other_key, nonce))
        assert (await _get_repo(port, ROOT, forged)).status_code == 403
        honest = _params(pen_did, nonce, _sign(pen_key, nonce))
        assert (await _get_repo(port, ROOT, honest)).status_code == 403  # burned


async def test_undelegated_pen_is_refused_even_with_valid_challenge(serve_app):
    srv, _gate = _server_with_gate()
    _user_repo(srv)  # no delegation for this pen
    pen_key, pen_did = _pen()

    async with serve_app(srv.app) as port:
        nonce = await _nonce(port)
        response = await _get_repo(port, ROOT, _params(pen_did, nonce, _sign(pen_key, nonce)))
        assert response.status_code == 403


async def test_documentless_pen_speaks_for_itself(serve_app):
    """With ``iss`` omitted the pen IS the caller: a did:key holding its own
    capability needs no delegation chain (by-key grants compose)."""
    srv, _gate = _server_with_gate()
    pen_key, pen_did = _pen()
    _user_repo(srv, subject=pen_did)  # capability granted directly to the pen

    async with serve_app(srv.app) as port:
        nonce = await _nonce(port)
        params = _params(pen_did, nonce, _sign(pen_key, nonce), iss=None)
        assert (await _get_repo(port, ROOT, params)).status_code == 200


async def test_challenge_route_is_opt_in(serve_app):
    """A PeerServer without an installed gate serves no challenge route and
    keeps refusing browser-shaped presentations (nothing resolves them)."""
    key = crypto.generate_keypair("k256")
    srv = PeerServer(Peer(did="did:web:builder.example", signing_key=key))

    async with serve_app(srv.app) as port, httpx.AsyncClient() as client:
        assert (await client.get(f"http://127.0.0.1:{port}{CHALLENGE_PATH}")).status_code == 404


async def test_signature_is_bound_to_the_requested_repo(serve_app):
    """A knock names the repo it wants. The bare nonce (the 0.2 message) and a
    signature made for another repo's DID both fail against this repo."""
    srv, _gate = _server_with_gate()
    handle = _user_repo(srv)
    pen_key, pen_did = _pen()
    _delegate(handle, pen_did)

    async with serve_app(srv.app) as port:
        nonce = await _nonce(port)
        bare = _b64url(crypto.sign_message(pen_key, nonce.encode("ascii")))
        assert (await _get_repo(port, ROOT, _params(pen_did, nonce, bare))).status_code == 403
        nonce = await _nonce(port)
        elsewhere = _sign(pen_key, nonce, repo_did="did:web:other.example:x")
        assert (await _get_repo(port, ROOT, _params(pen_did, nonce, elsewhere))).status_code == 403
        nonce = await _nonce(port)
        honest = _params(pen_did, nonce, _sign(pen_key, nonce))
        assert (await _get_repo(port, ROOT, honest)).status_code == 200


# --- unit: the challenge message, nonce lifecycle and composition -------------------


def test_challenge_message_matches_the_rust_vector():
    # pinned in rust/crates/fastatp-core/src/challenge.rs; both sides must agree
    assert (
        challenge_message("did:web:shelf.example:abc", "n0nce_-A")
        == b"fastatp-challenge-v1\ndid:web:shelf.example:abc\nn0nce_-A"
    )
    assert CHALLENGE_LABEL == "fastatp-challenge-v1"


def test_challenge_message_can_never_look_like_dag_cbor():
    # a commit is a dag-cbor map; its first byte is 0xa0..=0xbf, never ASCII
    assert challenge_message("did:key:zDnaeABC", "nonce")[0:1].isalpha()


@pytest.mark.parametrize(
    "did", ["", "shelf.example", "did:", "did:web:", "did:Web:x", "did:web:x:", "did:web:x%"]
)
def test_challenge_message_refuses_bad_dids(did):
    with pytest.raises(ValueError, match="DID"):
        challenge_message(did, "nonce")
    with pytest.raises(ValueError, match="DID"):
        challenge_message("did:web:" + "a" * 2048, "nonce")


@pytest.mark.parametrize(
    "nonce", ["", "has space", "has\nnewline", "padded==", "plus+slash/", "did:web:x"]
)
def test_challenge_message_refuses_bad_nonces(nonce):
    with pytest.raises(ValueError, match="nonce"):
        challenge_message("did:web:x", nonce)
    assert challenge_message("did:web:x", "A" * MAX_NONCE_LEN)
    with pytest.raises(ValueError, match="nonce"):
        challenge_message("did:web:x", "A" * (MAX_NONCE_LEN + 1))


def test_issued_nonces_are_valid_challenge_nonces():
    assert challenge_message("did:web:x", BrowserGate().issue())


def test_expired_nonce_is_refused():
    gate = BrowserGate(ttl=0.01)
    nonce = gate.issue()
    time.sleep(0.03)
    assert gate._consume(nonce) is False


def test_consume_is_single_use():
    gate = BrowserGate()
    nonce = gate.issue()
    assert gate._consume(nonce) is True
    assert gate._consume(nonce) is False


def test_install_is_once_per_gate():
    key = crypto.generate_keypair("k256")
    srv = PeerServer(Peer(did="did:web:builder.example", signing_key=key))
    gate = BrowserGate()
    gate.install(srv)
    with pytest.raises(ValueError, match="already installed"):
        gate.install(srv)


async def test_challenge_route_is_get_only(serve_app):
    srv, _gate = _server_with_gate()
    async with serve_app(srv.app) as port, httpx.AsyncClient() as client:
        assert (await client.post(f"http://127.0.0.1:{port}{CHALLENGE_PATH}")).status_code == 405


async def test_resolver_composition_first_identity_wins():
    conn = cast("HTTPConnection", None)  # composed resolvers never touch it here
    resolver = compose_caller_resolvers(lambda conn: None, lambda conn: "did:key:zWins")
    assert await cast("Awaitable[str | None]", resolver(conn)) == "did:key:zWins"
    silent = compose_caller_resolvers(lambda conn: None, lambda conn: None)
    assert await cast("Awaitable[str | None]", silent(conn)) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
