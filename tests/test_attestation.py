"""The Attestation lexicon + WG keypair/endpoint helpers.

The shipped dev.fastatp.auth.attestation record — signed pointer to a
WireGuard-reachable repo — round-trips, exports a valid lexicon doc, verifies
via its commit signature against the poster's DID, and parses back to WG
coordinates. Keypair generation yields valid WG keys."""

import base64

import pytest

from fastatp import Peer, crypto
from fastatp.authz.attestation import Attestation, build_attestation
from fastatp.authz.wireguard import (
    WireGuardPeerInfo,
    build_wireguard_uri,
    generate_wireguard_keypair,
    parse_wireguard_uri,
    validate_wireguard_key,
)
from fastatp.exceptions import EnforcementError

CTX = "ctx-abc123"
EPH_DID = "did:key:zEphemeralChannel"


def _att():
    _priv, pub = generate_wireguard_keypair()
    return build_attestation(
        context_id=CTX,
        ephemeral_did=EPH_DID,
        wg_public_key=pub,
        tunnel_ip="10.9.0.7",
        expires_at="2026-07-06T13:00:00Z",
        host="tool.example:51820",
    )


def test_generate_wireguard_keypair_is_valid():
    priv, pub = generate_wireguard_keypair()
    # both are standard base64 of 32-byte X25519 keys
    assert len(base64.b64decode(priv)) == 32
    assert len(base64.b64decode(pub)) == 32
    validate_wireguard_key(pub)  # does not raise
    _priv2, pub2 = generate_wireguard_keypair()
    assert pub != pub2  # fresh each time


def test_endpoint_uri_round_trip():
    _priv, pub = generate_wireguard_keypair()
    uri = build_wireguard_uri(pub, "10.9.0.7", "host.example:51820")
    info = parse_wireguard_uri(uri)
    assert info == WireGuardPeerInfo(
        public_key=pub, tunnel_ip="10.9.0.7", endpoint="host.example:51820"
    )


def test_parse_wireguard_uri_rejects_non_wg():
    with pytest.raises(EnforcementError, match="wg:// URI"):
        parse_wireguard_uri("https://nope.example")


def test_attestation_peer_info_and_expiry():
    from datetime import datetime, timezone

    att = _att()  # expires_at = 2026-07-06T13:00:00Z
    info = att.peer_info()
    assert info.public_key == att.wg_public_key
    assert info.tunnel_ip == "10.9.0.7"
    assert info.endpoint == "tool.example:51820"
    before = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    after = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    assert att.is_expired(now=before) is False
    assert att.is_expired(now=after) is True
    # unparseable expiry fails closed
    assert att.model_copy(update={"expires_at": "not-a-date"}).is_expired(now=before) is True


def test_attestation_peer_info_rejects_key_mismatch():
    att = _att()
    # tamper: top-level key no longer matches the endpoint's embedded key
    _priv, other = generate_wireguard_keypair()
    tampered = att.model_copy(update={"wg_public_key": other})
    with pytest.raises(EnforcementError, match="disagrees with endpoint"):
        tampered.peer_info()


def test_attestation_exports_valid_lexicon_doc():
    doc = Attestation.to_lexicon_json()
    assert doc["lexicon"] == 1
    assert doc["id"] == "dev.fastatp.auth.attestation"
    props = doc["defs"]["main"]["record"]["properties"]
    assert set(props) >= {"context_id", "ephemeral_did", "wg_public_key", "endpoint", "expires_at"}


async def test_attestation_signed_and_verified_via_commit():
    """An attestation is trusted because the poster's stable DID signs the
    commit it lives in — verify() recovers it against that DID."""
    key = crypto.generate_keypair("k256")
    did = crypto.encode_did_key(key.public_key())
    poster = Peer(did=did, signing_key=key)
    repo = poster.create_repo()
    att = _att()
    repo.put_record(Attestation.nsid, "self", att)
    proof = repo.prove(Attestation.nsid, "self")

    verifier = Peer()
    recovered = await verifier.verify(proof, expected_signer=did)
    assert isinstance(recovered, Attestation)
    assert recovered.context_id == CTX
    assert recovered.peer_info().tunnel_ip == "10.9.0.7"
