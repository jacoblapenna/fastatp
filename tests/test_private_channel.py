"""open_private_channel + attest (producer mechanism).

An ephemeral did:key channel admits exactly its one issuer (the doorman lets
that peer in and refuses everyone else), the reply lives only in the channel
repo, and a signed attestation — under the tool's stable DID — points at it.
The channel expires per its TTL.

Doorman identity on loopback is simulated by an injected resolver that reads a
``?as=<did>`` query param (standing in for WireGuard cryptokey routing, which
gives each peer a distinct source tunnel IP that loopback cannot)."""

import asyncio
from collections.abc import Awaitable
from typing import cast

import pytest
import websockets

from fastatp import Peer, PeerServer, crypto
from fastatp.authz.attestation import Attestation
from fastatp.authz.capability import ACTION_SUBSCRIBE, live_capabilities
from fastatp.authz.wireguard import generate_wireguard_keypair
from fastatp.exceptions import RepoError
from fastatp.peerapp import PeerApp
from fastatp.repo.sync import decode_frame
from fastatp.server.peer_server import FIREHOSE_PATH

TOOL_DID = "did:web:tool.example"


def _as_resolver(conn):
    # simulate cryptokey routing: this connection's identity is whatever DID it
    # claims via ?as= (in real WG this is the unspoofable source tunnel IP)
    return conn.query_params.get("as")


def _tool_app():
    key = crypto.generate_keypair("k256")
    server = PeerServer(Peer(did=TOOL_DID, signing_key=key), identity_resolver=_as_resolver)
    return PeerApp(server), server, key


def _issuer():
    key = crypto.generate_keypair("k256")
    did = crypto.encode_did_key(key.public_key())
    _priv, wg = generate_wireguard_keypair()
    return did, wg


async def _open(app, issuer_did, issuer_wg, *, ttl=60):
    return await app.open_private_channel(
        issuer_did,
        channel_tunnel_ip="10.99.0.1",
        issuer_wg_public_key=issuer_wg,
        issuer_tunnel_ip="10.0.0.5",
        endpoint_host="tool.example:51820",
        ttl=ttl,
    )


async def test_channel_admits_only_its_issuer(serve_app):
    app, server, _key = _tool_app()
    issuer_did, issuer_wg = _issuer()
    channel = await _open(app, issuer_did, issuer_wg)

    # a live subscribe capability for the issuer lives in the CHANNEL repo
    caps = live_capabilities(channel.handle.repo)
    assert issuer_did in caps
    assert ACTION_SUBSCRIBE in caps[issuer_did][1].actions
    assert channel.did in server.hosts()  # gated repo is hosted

    channel.put_record("com.x.reply", "r", {"$type": "com.x.reply", "text": "secret"})

    async with serve_app(server.app) as port:
        base = f"ws://127.0.0.1:{port}{FIREHOSE_PATH}?did={channel.did}&cursor=0"
        # the issuer is admitted and reads the private reply (skipping the
        # #identity/#account announcement frames)
        async with websockets.connect(f"{base}&as={issuer_did}") as ws:
            for _ in range(8):
                # the server only sends binary frames on this socket
                header, body = decode_frame(
                    await asyncio.wait_for(cast("Awaitable[bytes]", ws.recv()), 5.0)
                )
                if header["t"] == "#commit":
                    break
            assert header["t"] == "#commit"
            assert body["repo"] == channel.did
        # anyone else is refused by the doorman
        with pytest.raises(RepoError, match="NotAuthorized"):
            async with websockets.connect(f"{base}&as=did:key:zIntruder") as ws2:
                decode_frame(await asyncio.wait_for(cast("Awaitable[bytes]", ws2.recv()), 5.0))


async def test_channel_repr_never_leaks_wg_private_key():
    app, _server, _key = _tool_app()
    issuer_did, issuer_wg = _issuer()
    channel = await _open(app, issuer_did, issuer_wg)
    text = repr(channel)
    assert channel.wg_private_key not in text  # the secret is suppressed
    assert channel.wg_public_key in text  # the public coordinates are fine


async def test_did_key_issuer_requires_coordinates():
    app, _server, _key = _tool_app()
    issuer_did, _wg = _issuer()
    with pytest.raises(ValueError, match="admitted by public key"):
        await app.open_private_channel(issuer_did, channel_tunnel_ip="10.99.0.1", ttl=60)


async def test_attest_posts_signed_attestation_under_stable_did():
    from fastatp import build_did_document

    app, server, key = _tool_app()
    issuer_did, issuer_wg = _issuer()
    channel = await _open(app, issuer_did, issuer_wg)

    att = await app.attest(channel, "ctx-42")
    assert isinstance(att, Attestation)
    assert att.context_id == "ctx-42"
    assert att.ephemeral_did == channel.did
    assert att.peer_info().tunnel_ip == "10.99.0.1"

    # the attestation is in the tool's public repo, signed by the tool's DID
    records = server.repo.list_records(Attestation.nsid)
    assert len(records) == 1
    rkey, _cid = records[0]
    proof = server.repo.prove(Attestation.nsid, rkey)

    doc = build_did_document(did=TOOL_DID, signing_key=key.public_key())

    async def resolver(did):
        return doc if did == TOOL_DID else None

    recovered = await Peer(resolver=resolver).verify(proof, expected_signer=TOOL_DID)
    assert isinstance(recovered, Attestation)
    assert recovered.context_id == "ctx-42"


async def test_channel_expires_and_becomes_unroutable():
    from datetime import datetime, timedelta, timezone

    app, server, _key = _tool_app()
    issuer_did, issuer_wg = _issuer()
    channel = await _open(app, issuer_did, issuer_wg, ttl=3600)
    assert channel.did in server.hosts()

    channel.handle._host.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert server.reap_expired() == [channel.did]
    assert server._resolve_host(channel.did) is None
