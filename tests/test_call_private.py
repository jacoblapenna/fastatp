"""follow_attested + call(private=True) — consumer round-trip.

A documentless requester and a private tool complete a private exchange from the
consumer's normal call site: the requester pushes its request + self-attestation
to the tool's push intake, the tool answers with reply_private, and the
requester discovers the engagement on the tool's public firehose, follows the
ephemeral channel, and gets the reply. The channel doorman isolation is proven:
a second subscriber to the channel is refused.

Loopback identity is simulated with a ?as=<did> resolver (cryptokey routing)."""

import asyncio
from collections.abc import Awaitable
from typing import cast

import pytest
import websockets

from fastatp import Peer, PeerServer, build_did_document, crypto
from fastatp.authz.attestation import build_attestation
from fastatp.authz.wireguard import generate_wireguard_keypair
from fastatp.exceptions import RepoError
from fastatp.lexicon import Lexicon
from fastatp.peerapp import PUSH_INTAKE_PATH, PeerApp
from fastatp.repo.sync import decode_frame
from fastatp.server.peer_server import FIREHOSE_PATH

TOOL_DID = "did:web:tool.example"
CTX = "ctx-99"


class WantAd(Lexicon):
    nsid = "com.acme.wantad2"
    context_id: str
    need: str


class Result(Lexicon):
    nsid = "com.acme.result2"
    context_id: str
    finding: str


def _as_resolver(conn):
    return conn.query_params.get("as")


async def test_call_private_round_trip(serve_app):
    # --- the private tool: served, push-intake on, ?as= identity, WantAd -> reply_private
    tool_key = crypto.generate_keypair("k256")
    tool_peer = Peer(did=TOOL_DID, signing_key=tool_key)
    tool_server = PeerServer(tool_peer, identity_resolver=_as_resolver)
    tool = PeerApp(tool_server)
    tool.enable_push_intake()

    @tool.on(WantAd)
    async def handle(ad, ctx):
        result = Result(context_id=ad.context_id, finding="all clear")
        await tool.reply_private(ctx, result, ttl=120)

    # --- the requester: documentless did:key, no hosted infra
    req_key = crypto.generate_keypair("k256")
    req_did = crypto.encode_did_key(req_key.public_key())
    _priv, req_wg = generate_wireguard_keypair()
    tool_doc = build_did_document(did=TOOL_DID, signing_key=tool_key.public_key())

    async def resolver(did):
        return tool_doc if did == TOOL_DID else None

    requester = PeerApp(PeerServer(Peer(did=req_did, signing_key=req_key)), resolver=resolver)
    self_att = build_attestation(
        context_id=CTX,
        ephemeral_did=req_did,
        wg_public_key=req_wg,
        tunnel_ip="10.0.0.99",
        expires_at="2027-01-01T00:00:00Z",
        host=None,
    )

    async with serve_app(tool_server.app) as port:
        base = f"http://127.0.0.1:{port}"
        ws = f"ws://127.0.0.1:{port}{FIREHOSE_PATH}"
        try:
            reply = await requester.call(
                TOOL_DID,
                WantAd(context_id=CTX, need="inspect"),
                reply_type=Result,
                private=True,
                self_attestation=self_att,
                push_url=f"{base}{PUSH_INTAKE_PATH}",
                firehose_url=ws,  # watch the tool's public firehose for the engagement
                # the channel lives on the same server; connect as the requester
                channel_firehose_url=f"{ws}?as={req_did}",
                timeout=10,
            )
            assert isinstance(reply, Result)
            assert reply.context_id == CTX
            assert reply.finding == "all clear"

            # isolation: the engagement attestation was public, but a SECOND
            # subscriber to the ephemeral channel is refused by the doorman
            engagement = requester._find_attestation(CTX, issuer_did=TOOL_DID)
            assert engagement is not None
            intruder = f"{ws}?did={engagement.ephemeral_did}&cursor=0&as=did:key:zSnoop"
            with pytest.raises(RepoError, match="NotAuthorized"):
                async with websockets.connect(intruder) as sock:
                    # the server only sends binary frames on this socket
                    decode_frame(await asyncio.wait_for(cast("Awaitable[bytes]", sock.recv()), 5.0))
        finally:
            await requester.aclose()
            await tool.aclose()


async def test_follow_attested_refuses_expired():
    peer = Peer(did="did:web:r.example", signing_key=crypto.generate_keypair("k256"))
    requester = PeerApp(PeerServer(peer))
    _priv, wg = generate_wireguard_keypair()
    expired = build_attestation(
        context_id="ctx-x",
        ephemeral_did="did:key:zGone",
        wg_public_key=wg,
        tunnel_ip="10.9.0.1",
        expires_at="2000-01-01T00:00:00Z",
        host="x:1",
    )
    with pytest.raises(ValueError, match="expired"):
        await requester.follow_attested(expired)
    await requester.aclose()
