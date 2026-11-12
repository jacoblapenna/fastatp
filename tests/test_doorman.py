"""The doorman — per-repo read gating.

A gated repo admits a reader only when the connection resolves to a DID that
holds a live ``subscribe`` capability in that repo's own records. Public repos
(the primary firehose, a board) are open. Both failure modes — no tunnel
identity, and a resolved-but-unadmitted caller — fail closed.
"""

import asyncio
import base64
import json
import logging
from collections.abc import Awaitable
from typing import cast

import httpx
import pytest
import websockets

from fastatp import Peer, PeerServer, crypto
from fastatp.authz.capability import ACTION_SEND, ACTION_SUBSCRIBE, Capability
from fastatp.authz.wireguard import overlay_address
from fastatp.exceptions import RepoError
from fastatp.repo.repo import Repo
from fastatp.repo.sync import decode_frame
from fastatp.server.peer_server import FIREHOSE_PATH, GET_REPO_PATH, TunnelIdentityResolver
from fastatp.tid import generate_tid

PRIMARY_DID = "did:web:hub.example"
ALICE = "did:key:zAlice"
BOB = "did:key:zBob"
WG_KEY = base64.b64encode(bytes(range(32))).decode()  # a valid 32-byte WG key


def _channel_repo() -> Repo:
    key = crypto.generate_keypair("k256")
    return Repo(crypto.encode_did_key(key.public_key()), key)


def _admit(handle, subject, *, actions=(ACTION_SUBSCRIBE,), wg_key=None):
    """Write a live capability admitting ``subject`` into the handle's repo,
    optionally by WireGuard key (whose overlay address is derived)."""
    cap = Capability(subject=subject, actions=list(actions), wg_public_key=wg_key)
    handle.put_record(Capability.nsid, generate_tid(), cap)


def _arriving_from(key: str):
    """A source-address seam presenting the overlay address ``key`` derives,
    standing in for the kernel's cryptokey routing on a real tunnel."""
    return lambda conn: overlay_address(key)


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
        assert rb.status_code == 404
        assert rb.json()["error"] == "RepoNotFound"

        # firehose: B refuses with a RepoNotFound error frame (a gated repo a
        # stranger cannot read does not exist for them); A delivers its
        # commit (Alice, a live subscribe-holder, is admitted)
        with pytest.raises(RepoError, match="RepoNotFound"):
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
        assert (await _get(port, a.did)).status_code == 404  # gated: refused
        assert (await _get(port)).status_code == 200  # primary: public
        with pytest.raises(RepoError, match="RepoNotFound"):
            await _first_frame(port, a.did)


async def test_send_only_capability_does_not_confer_read(serve_app):
    """A 'send' grant (push intake) must not open the firehose."""
    srv = PeerServer(_peer(), identity_resolver=lambda conn: ALICE)
    a = srv.register(_channel_repo())
    _admit(a, ALICE, actions=(ACTION_SEND,))  # send, not subscribe

    async with serve_app(srv.app) as port:
        assert (await _get(port, a.did)).status_code == 404


async def test_tunnel_identity_resolver_maps_source_address(serve_app):
    """The tunnel resolver ties the source overlay address to Alice via A's
    signed capability: the address is derived from the granted key, never
    read from a record."""
    srv = PeerServer(_peer())
    srv.identity_resolver = TunnelIdentityResolver(srv, source_address=_arriving_from(WG_KEY))
    a = srv.register(_channel_repo())
    b = srv.register(_channel_repo())
    _admit(a, ALICE, wg_key=WG_KEY)  # Alice's key -> her address -> Alice, admitted to A

    async with serve_app(srv.app) as port:
        assert (await _get(port, a.did)).status_code == 200  # Alice admitted to A
        assert (await _get(port, b.did)).status_code == 404  # Alice not in B


async def test_a_did_web_grantee_admitted_through_the_reconciler_is_named_at_the_door(serve_app):
    """The helper writes the same record the tutorial writes by hand: the
    grantee's key, read from its DID document, is in the capability, so the
    doorman derives the address the kernel admitted and names the caller.
    (Before 0.3 the helper left the key out, and a did:web grantee was
    admitted by the kernel and refused at the door.)"""
    from fastatp import build_did_document
    from fastatp.authz import MemoryWireGuardInterface, WireGuardReconciler, build_wireguard_service

    bob = "did:web:bob.example"
    bob_doc = build_did_document(
        did=bob,
        signing_key=crypto.generate_keypair("k256").public_key(),
        extra_services=[build_wireguard_service(WG_KEY)],
    )

    async def resolver(did: str):
        return bob_doc if did == bob else None

    srv = PeerServer(_peer())
    srv.identity_resolver = TunnelIdentityResolver(srv, source_address=_arriving_from(WG_KEY))
    channel = srv.register(_channel_repo())
    interface = MemoryWireGuardInterface()
    await WireGuardReconciler(channel, interface, resolver).grant(bob)
    assert WG_KEY in interface.peers()  # the kernel admits bob's key...

    async with serve_app(srv.app) as port:
        assert (await _get(port, channel.did)).status_code == 200  # ...and the door names him


async def test_proxy_warning_on_unidentifiable_loopback_read(serve_app, caplog):
    """A gated read that can't be tied to a tunnel identity, from loopback, logs
    a loud proxy-misconfiguration diagnosis (once)."""
    srv = PeerServer(_peer())  # default resolver: the real client address, loopback
    a = srv.register(_channel_repo())
    _admit(a, ALICE, wg_key=WG_KEY)  # Alice's derived address is on the overlay, not loopback

    async with serve_app(srv.app) as port:
        with caplog.at_level(logging.WARNING, logger="fastatp.peerserver"):
            assert (await _get(port, a.did)).status_code == 404
            # a second refused read must not re-warn for the same source
            assert (await _get(port, a.did)).status_code == 404

    warnings = [r for r in caplog.records if "behind a proxy" in r.getMessage()]
    assert len(warnings) == 1
    assert "loopback source" in warnings[0].getMessage()


async def test_a_refused_gated_repo_is_indistinguishable_from_an_absent_one(serve_app):
    """Every single-repo sync route and the firehose answer a stranger the
    same way for a gated repo they may not read as for a DID this server
    does not host: RepoNotFound, same status, same body. Existence is never
    leaked by the status code."""
    from fastatp.server.peer_server import (
        GET_BLOB_PATH,
        GET_LATEST_COMMIT_PATH,
        GET_RECORD_PATH,
        LIST_BLOBS_PATH,
    )

    srv = PeerServer(_peer(), identity_resolver=lambda conn: ALICE)
    gated = srv.register(_channel_repo())
    _admit(gated, BOB)  # Alice is not admitted
    absent = "did:web:nobody.example"
    routes = [
        (GET_REPO_PATH, {}),
        (GET_LATEST_COMMIT_PATH, {}),
        (GET_RECORD_PATH, {"collection": "com.example.note", "rkey": "n"}),
        (GET_BLOB_PATH, {"cid": "bafkreigh2akiscaildcqabsyg3dfr6chu3fgpregiymsck7e7aqa4s52zy"}),
        (LIST_BLOBS_PATH, {}),
    ]
    async with serve_app(srv.app) as port, httpx.AsyncClient() as client:
        for path, extra in routes:
            answers = []
            for did in (gated.did, absent):
                r = await client.get(f"http://127.0.0.1:{port}{path}", params={"did": did, **extra})
                # the whole payload, with the only thing that may differ — the
                # DID the caller asked about — normalized away
                body = json.loads(r.text.replace(json.dumps(did)[1:-1], "<did>"))
                answers.append((r.status_code, body))
            assert answers[0] == answers[1], path
            assert answers[0][1]["error"] == "RepoNotFound", path
        for did in (gated.did, absent):
            with pytest.raises(RepoError, match="RepoNotFound"):
                await _first_frame(port, did)
