"""Attestation discovery cache + reply_private sugar.

PeerApp caches inbound shipped Attestations generically (no handler needed), and
reply_private composes the whole producer side in one call: read the requester's
self-attestation coordinates, spin a channel admitting only that issuer, write
the reply into it, and publish a signed engagement attestation."""

from fastatp import Peer, PeerServer, build_did_document, crypto
from fastatp.authz.attestation import Attestation, build_attestation
from fastatp.authz.capability import ACTION_SUBSCRIBE, live_capabilities
from fastatp.authz.wireguard import generate_wireguard_keypair
from fastatp.lexicon import Lexicon
from fastatp.peerapp import Envelope, PeerApp

TOOL_DID = "did:web:tool.example"
CTX = "ctx-inspection-7"


class WantAd(Lexicon):
    nsid = "com.acme.wantad"
    context_id: str
    need: str


class Result(Lexicon):
    nsid = "com.acme.result"
    context_id: str
    finding: str


def _tool_app():
    key = crypto.generate_keypair("k256")
    server = PeerServer(Peer(did=TOOL_DID, signing_key=key))
    return PeerApp(server), server, key


def _requester():
    """A documentless did:key requester + its self-attestation coordinates."""
    key = crypto.generate_keypair("k256")
    did = crypto.encode_did_key(key.public_key())
    _priv, wg = generate_wireguard_keypair()
    self_att = build_attestation(
        context_id=CTX,
        ephemeral_did=did,
        wg_public_key=wg,
        tunnel_ip="10.0.0.42",
        expires_at="2027-01-01T00:00:00Z",
        host="requester.local:51820",
    )
    return did, self_att


def _envelope(record, issuer_did):
    return Envelope(
        issuer_did=issuer_did,
        record_cid=record.cid(),
        received_at="2026-07-06T12:00:00Z",
        record=record,
        collection=type(record).nsid,
        rkey="req",
    )


async def test_inbound_attestation_is_cached_generically():
    app, _server, _key = _tool_app()
    requester_did, self_att = _requester()
    # dispatched with NO registered handler for Attestation -> still cached
    await app._dispatch(_envelope(self_att, requester_did), None)

    found = app._find_attestation(CTX, issuer_did=requester_did)
    assert found is not None
    assert found.ephemeral_did == requester_did
    # an expired attestation is not returned
    expired = build_attestation(
        context_id="ctx-old",
        ephemeral_did=requester_did,
        wg_public_key=self_att.wg_public_key,
        tunnel_ip="10.0.0.42",
        expires_at="2000-01-01T00:00:00Z",
        host=None,
    )
    app._cache_attestation(requester_did, expired)
    assert app._find_attestation("ctx-old") is None


async def test_reply_private_composes_the_producer_side():
    app, server, key = _tool_app()
    requester_did, self_att = _requester()
    # the requester's self-attestation has arrived (board/firehose); cache it
    await app._dispatch(_envelope(self_att, requester_did), None)

    # the tool's handler answers privately in one call
    ctx = _envelope(WantAd(context_id=CTX, need="inspect"), requester_did)
    channel = await app.reply_private(ctx, Result(context_id=CTX, finding="all clear"), ttl=120)

    # the channel admits ONLY the requester, by the key from its self-attestation
    caps = live_capabilities(channel.handle.repo)
    assert requester_did in caps
    assert ACTION_SUBSCRIBE in caps[requester_did][1].actions
    assert caps[requester_did][1].wg_public_key == self_att.wg_public_key
    assert caps[requester_did][1].wg_tunnel_ip == "10.0.0.42"

    # the reply lives in the channel repo, not the tool's public repo
    reply_rkey = channel.handle.repo.list_records(Result.nsid)[0][0]
    assert channel.handle.repo.get_record(Result.nsid, reply_rkey)["finding"] == "all clear"
    assert server.repo.list_records(Result.nsid) == []

    # a signed engagement attestation for CTX is on the tool's public repo,
    # pointing at the channel, verifiable against the tool's DID
    att_rkey = server.repo.list_records(Attestation.nsid)[0][0]
    proof = server.repo.prove(Attestation.nsid, att_rkey)
    doc = build_did_document(did=TOOL_DID, signing_key=key.public_key())

    async def resolver(did):
        return doc if did == TOOL_DID else None

    engagement = await Peer(resolver=resolver).verify(proof, expected_signer=TOOL_DID)
    assert isinstance(engagement, Attestation)
    assert engagement.context_id == CTX
    assert engagement.ephemeral_did == channel.did


async def test_reply_private_without_self_attestation_is_a_clear_error():
    app, _server, _key = _tool_app()
    requester_did, _self_att = _requester()  # NOT cached
    ctx = _envelope(WantAd(context_id=CTX, need="inspect"), requester_did)
    reply = Result(context_id=CTX, finding="x")
    try:
        await app.reply_private(ctx, reply, self_attestation_timeout=0)
        raise AssertionError("expected a ValueError")
    except ValueError as e:
        assert "self-attestation" in str(e)


async def test_reply_private_allocates_distinct_channel_ips():
    app, _server, _key = _tool_app()
    r1, att1 = _requester()
    r2 = crypto.generate_keypair("k256")
    r2_did = crypto.encode_did_key(r2.public_key())
    _p, r2_wg = generate_wireguard_keypair()
    att2 = build_attestation(
        context_id="ctx-2",
        ephemeral_did=r2_did,
        wg_public_key=r2_wg,
        tunnel_ip="10.0.0.43",
        expires_at="2027-01-01T00:00:00Z",
        host=None,
    )
    await app._dispatch(_envelope(att1, r1), None)
    await app._dispatch(_envelope(att2, r2_did), None)

    ctx1 = _envelope(WantAd(context_id=CTX, need="a"), r1)
    ctx2 = _envelope(WantAd(context_id="ctx-2", need="b"), r2_did)
    ch1 = await app.reply_private(ctx1, Result(context_id=CTX, finding="1"))
    ch2 = await app.reply_private(ctx2, Result(context_id="ctx-2", finding="2"))
    assert ch1.tunnel_ip != ch2.tunnel_ip
    assert ch1.did != ch2.did
