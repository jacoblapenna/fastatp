"""The doorman — per-repo read gating.

A gated repo admits a reader only when the connection resolves to a DID that
holds a live ``subscribe`` capability in that repo's own records. Public repos
(the primary firehose, a board) are open. Both failure modes — no tunnel
identity, and a resolved-but-unadmitted caller — fail closed.
"""

import asyncio
import base64
import logging
from collections.abc import Awaitable
from typing import cast

import httpx
import pytest
import websockets

from fastatp import Peer, PeerServer, crypto
from fastatp.authz.capability import ACTION_SEND, ACTION_SUBSCRIBE, Capability
from fastatp.exceptions import RepoError
from fastatp.repo.repo import Repo
from fastatp.repo.sync import decode_frame
from fastatp.server.peer_server import FIREHOSE_PATH, GET_REPO_PATH
from fastatp.tid import generate_tid

PRIMARY_DID = "did:web:hub.example"
ALICE = "did:key:zAlice"
BOB = "did:key:zBob"
WG_KEY = base64.b64encode(bytes(range(32))).decode()  # a valid 32-byte WG key


def _channel_repo() -> Repo:
    key = crypto.generate_keypair("k256")
    return Repo(crypto.encode_did_key(key.public_key()), key)


def _admit(handle, subject, *, actions=(ACTION_SUBSCRIBE,), tunnel_ip=None):
    """Write a live capability admitting ``subject`` into the handle's repo."""
    cap = Capability(
        subject=subject,
        actions=list(actions),
        wg_public_key=WG_KEY if tunnel_ip else None,
        wg_tunnel_ip=tunnel_ip,
    )
    handle.put_record(Capability.nsid, generate_tid(), cap)


def _peer():
    key = crypto.generate_keypair("k256")
    return Peer(did=PRIMARY_DID, signing_key=key)


async def _get(port, did=None):
    async with httpx.AsyncClient() as client:
        params = {"did": did} if did else {}
        return await client.get(f"http://127.0.0.1:{port}{GET_REPO_PATH}", params=params)


async def _first_frame(port, did, cursor=0):
    # cursor=0 replays from the backfill so a commit written before connecting
    # is delivered (a live-tail subscription would only see future commits);
    # skips the #identity/#account announcement frames
    url = f"ws://127.0.0.1:{port}{FIREHOSE_PATH}?did={did}&cursor={cursor}"
    async with websockets.connect(url) as ws:
        for _ in range(8):
            # the server only sends binary frames on this socket
            header, body = decode_frame(
                await asyncio.wait_for(cast("Awaitable[bytes]", ws.recv()), 5.0)
            )
            if header.get("t") == "#commit":
                return header, body
        raise AssertionError("no #commit frame within 8 frames")


# --- the required gate test ---------------------------------------------------


async def test_admitted_to_a_is_refused_b(serve_app):
    """Injected identity = Alice. Alice is admitted to A only. getRepo and the
    firehose both admit A and refuse B; a B-holder (Bob) would be admitted."""
    srv = PeerServer(_peer(), identity_resolver=lambda conn: ALICE)
    a = srv.register(_channel_repo())  # gated by default
    b = srv.register(_channel_repo())
    _admit(a, ALICE)
    _admit(b, BOB)  # Bob holds B, Alice does not

    async with serve_app(srv.app) as port:
        # getRepo: A allowed, B refused
        assert (await _get(port, a.did)).status_code == 200
        rb = await _get(port, b.did)
        assert rb.status_code == 403
        assert rb.json()["error"] == "NotAuthorized"

        # firehose: B refuses with a NotAuthorized error frame; A delivers its
        # commit (Alice, a live subscribe-holder, is admitted)
        with pytest.raises(RepoError, match="NotAuthorized"):
            await _first_frame(port, b.did)

        a.put_record("com.example.note", "n", {"$type": "com.example.note", "text": "hi"})
        header, body = await _first_frame(port, a.did)
        assert header["t"] == "#commit" and body["repo"] == a.did


async def test_fail_closed_when_no_tunnel_identity(serve_app):
    """A caller the resolver cannot place (None) is refused every gated repo,
    but the public primary firehose stays open."""
    srv = PeerServer(_peer(), identity_resolver=lambda conn: None)
    a = srv.register(_channel_repo())
    _admit(a, ALICE)

    async with serve_app(srv.app) as port:
        assert (await _get(port, a.did)).status_code == 403  # gated: refused
        assert (await _get(port)).status_code == 200  # primary: public
        with pytest.raises(RepoError, match="NotAuthorized"):
            await _first_frame(port, a.did)


async def test_send_only_capability_does_not_confer_read(serve_app):
    """A 'send' grant (push intake) must not open the firehose."""
    srv = PeerServer(_peer(), identity_resolver=lambda conn: ALICE)
    a = srv.register(_channel_repo())
    _admit(a, ALICE, actions=(ACTION_SEND,))  # send, not subscribe

    async with serve_app(srv.app) as port:
        assert (await _get(port, a.did)).status_code == 403


async def test_tunnel_identity_resolver_maps_source_ip(serve_app):
    """The default resolver ties the loopback source IP to Alice via A's signed
    capability (wg_tunnel_ip), with no injected resolver."""
    srv = PeerServer(_peer())  # default TunnelIdentityResolver
    a = srv.register(_channel_repo())
    b = srv.register(_channel_repo())
    _admit(a, ALICE, tunnel_ip="127.0.0.1")  # loopback -> Alice, admitted to A

    async with serve_app(srv.app) as port:
        assert (await _get(port, a.did)).status_code == 200  # Alice admitted to A
        assert (await _get(port, b.did)).status_code == 403  # Alice not in B


async def test_proxy_warning_on_unidentifiable_loopback_read(serve_app, caplog):
    """A gated read that can't be tied to a tunnel identity, from loopback, logs
    a loud proxy-misconfiguration diagnosis (once)."""
    srv = PeerServer(_peer())  # default resolver; no cap maps loopback -> anyone
    a = srv.register(_channel_repo())
    _admit(a, ALICE, tunnel_ip="10.9.0.2")  # Alice is on the tunnel, not loopback

    async with serve_app(srv.app) as port:
        with caplog.at_level(logging.WARNING, logger="fastatp.peerserver"):
            assert (await _get(port, a.did)).status_code == 403
            # a second refused read must not re-warn for the same source
            assert (await _get(port, a.did)).status_code == 403

    warnings = [r for r in caplog.records if "behind a proxy" in r.getMessage()]
    assert len(warnings) == 1
    assert "loopback source" in warnings[0].getMessage()
