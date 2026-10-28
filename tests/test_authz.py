"""v0.4: capability records + WireGuard enforcement + the reconciler."""

import base64
from datetime import datetime, timedelta, timezone

import pytest

from fastatp import DIDDocument, Peer, PeerServer, RepoMirror, build_did_document, crypto
from fastatp.authz import (
    Capability,
    MemoryWireGuardInterface,
    Revocation,
    WgToolInterface,
    WireGuardReconciler,
    build_wireguard_service,
    live_capabilities,
    parse_wireguard_service,
)
from fastatp.exceptions import AuthzError, EnforcementError
from fastatp.lex.schema import LexiconDoc

GRANTOR = "did:web:grantor.example"
ALICE = "did:web:alice.example"
BOB = "did:web:bob.example"


def _wg_key(seed: int) -> str:
    return base64.b64encode(bytes([seed]) * 32).decode()


ALICE_KEY = _wg_key(1)
BOB_KEY = _wg_key(2)

DOCS: dict[str, DIDDocument] = {}


def _register(did: str, wg_key: str, tunnel_ip: str, endpoint: str | None = None):
    key = crypto.generate_keypair("k256")
    DOCS[did] = build_did_document(
        did=did,
        signing_key=key.public_key(),
        extra_services=[build_wireguard_service(wg_key, tunnel_ip, endpoint)],
    )


_register(ALICE, ALICE_KEY, "10.100.0.2", "203.0.113.5:51820")
_register(BOB, BOB_KEY, "fd00::2")


async def resolver(did: str):
    return DOCS.get(did)


@pytest.fixture
def grantor():
    key = crypto.generate_keypair("k256")
    server = PeerServer(Peer(did=GRANTOR, signing_key=key))
    interface = MemoryWireGuardInterface()
    reconciler = WireGuardReconciler(server, interface, resolver)
    return server, interface, reconciler


# --- lexicons -----------------------------------------------------------------


def test_capability_lexicons_export_valid_docs():
    for cls in (Capability, Revocation):
        LexiconDoc.from_json_dict(cls.to_lexicon_json())


def test_capability_expiry_semantics():
    past = "2020-01-01T00:00:00Z"
    future = "2999-01-01T00:00:00Z"
    assert Capability(subject=ALICE, actions=["subscribe"]).is_expired() is False
    assert Capability(subject=ALICE, actions=["subscribe"], expires_at=past).is_expired()
    assert not Capability(subject=ALICE, actions=["subscribe"], expires_at=future).is_expired()


# --- service entry --------------------------------------------------------------


def test_wireguard_service_round_trip():
    info = parse_wireguard_service(DOCS[ALICE])
    assert info.public_key == ALICE_KEY
    assert info.tunnel_ip == "10.100.0.2"
    assert info.endpoint == "203.0.113.5:51820"
    assert info.allowed_ips == "10.100.0.2/32"
    # IPv6 grantee, no endpoint (roaming)
    info6 = parse_wireguard_service(DOCS[BOB])
    assert info6.endpoint is None
    assert info6.allowed_ips == "fd00::2/128"


def test_wireguard_service_validation():
    with pytest.raises(EnforcementError):
        build_wireguard_service("not-a-key", "10.0.0.1")
    with pytest.raises(ValueError):
        build_wireguard_service(ALICE_KEY, "not-an-ip")
    doc = build_did_document(
        did="did:web:nowg.example",
        signing_key=crypto.generate_keypair("k256").public_key(),
    )
    with pytest.raises(EnforcementError, match="no WireGuardFirehose"):
        parse_wireguard_service(doc)


# --- grant / revoke ----------------------------------------------------------------


async def test_grant_signs_capability_and_opens_tunnel(grantor):
    server, interface, reconciler = grantor
    rkey, _cap = await reconciler.grant(ALICE, note="field gateway")
    # authorization plane: signed record in the repo (firehosed via the server)
    stored = server.repo.get_record(Capability.nsid, rkey)
    assert stored["subject"] == ALICE and stored["actions"] == ["subscribe"]
    # enforcement plane: pubkey present with the grantee's tunnel ip
    assert interface.peers() == {ALICE_KEY: "10.100.0.2/32"}


async def test_grant_unresolvable_did_is_strict(grantor):
    _server, interface, reconciler = grantor
    with pytest.raises(AuthzError):
        await reconciler.grant("did:web:unknown.example")
    assert interface.peers() == {}  # nothing committed, nothing opened


async def test_revoke_kills_record_then_tunnel(grantor):
    server, interface, reconciler = grantor
    rkey, _cap = await reconciler.grant(ALICE)
    revocation = await reconciler.revoke(ALICE, reason="offboarded")
    # record plane: capability gone, revocation audit trail present
    assert server.repo.get_record(Capability.nsid, rkey) is None
    revs = server.repo.list_records(Revocation.nsid)
    assert len(revs) == 1
    audit = server.repo.get_record(Revocation.nsid, revs[0][0])
    assert audit["subject"] == ALICE and audit["reason"] == "offboarded"
    assert audit["capability_rkey"] == rkey
    # enforcement plane: pubkey absent = unreachable
    assert interface.peers() == {}
    assert revocation.reason == "offboarded"
    # revoking again is a no-op
    assert await reconciler.revoke(ALICE) is None


async def test_supersession_in_one_commit(grantor):
    server, interface, reconciler = grantor
    rkey1, _ = await reconciler.grant(ALICE)
    rkey2, _ = await reconciler.grant(ALICE, actions=("subscribe", "send"))
    assert rkey1 != rkey2
    live = live_capabilities(server.repo)
    assert set(live) == {ALICE} and live[ALICE][0] == rkey2
    # the superseded grant left a revocation with reason=superseded
    reasons = [
        server.repo.get_record(Revocation.nsid, rk)["reason"]
        for rk, _ in server.repo.list_records(Revocation.nsid)
    ]
    assert reasons == ["superseded"]
    assert interface.peers() == {ALICE_KEY: "10.100.0.2/32"}


# --- reconcile -----------------------------------------------------------------------


async def test_reconcile_heals_drift_both_directions(grantor):
    _server, interface, reconciler = grantor
    await reconciler.grant(ALICE)
    await reconciler.grant(BOB)
    # drift: alice's peer vanished (crash after record commit); a foreign
    # peer appeared that no capability explains
    interface.remove_peer(ALICE_KEY)
    foreign = _wg_key(9)
    interface.add_peer(foreign, "10.99.0.9/32")
    report = await reconciler.reconcile()
    assert report.changed
    assert report.added == [ALICE]
    assert report.removed == [foreign]
    assert sorted(report.kept) == [BOB]
    assert set(interface.peers()) == {ALICE_KEY, BOB_KEY}


async def test_reconcile_respects_pinned_pubkeys():
    key = crypto.generate_keypair("k256")
    server = PeerServer(Peer(did=GRANTOR, signing_key=key))
    interface = MemoryWireGuardInterface()
    operator_key = _wg_key(7)
    interface.add_peer(operator_key, "10.0.0.7/32")
    reconciler = WireGuardReconciler(server, interface, resolver, pinned_pubkeys={operator_key})
    report = await reconciler.reconcile()
    assert report.removed == []
    assert operator_key in interface.peers()


async def test_expiry_is_a_lifecycle_revocation(grantor):
    server, interface, reconciler = grantor
    soon = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    await reconciler.grant(ALICE, expires_at=soon)
    assert interface.peers()  # tunnel open while live
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    report = await reconciler.reconcile(now=later)
    assert report.expired == [ALICE]
    assert interface.peers() == {}  # sweep removed the peer
    assert server.repo.record_count(Capability.nsid) == 0
    reasons = [
        server.repo.get_record(Revocation.nsid, rk)["reason"]
        for rk, _ in server.repo.list_records(Revocation.nsid)
    ]
    assert reasons == ["expired"]


async def test_reconcile_keeps_peers_when_resolution_fails(grantor):
    _server, interface, reconciler = grantor
    await reconciler.grant(ALICE)
    # the grantee's document becomes unreachable (DNS blip): reconcile must
    # NOT drop the live tunnel, and must report the failure
    saved = DOCS.pop(ALICE)
    try:
        report = await reconciler.reconcile()
        assert ALICE in report.failed
        assert ALICE_KEY in interface.peers()  # connectivity preserved
    finally:
        DOCS[ALICE] = saved


# --- audit trail over the firehose ------------------------------------------------


async def test_capability_lifecycle_is_firehosed(grantor):
    from fastatp.repo.sync import commit_frame_body

    server, _interface, reconciler = grantor
    doc = build_did_document(did=GRANTOR, signing_key=server.peer.signing_key.public_key())

    async def grantor_resolver(did):
        return doc if did == GRANTOR else None

    mirror = RepoMirror(GRANTOR, resolver=grantor_resolver)
    events = []

    original_emit = server._emit

    def capture(event):
        events.append(event)
        return original_emit(event)

    server._emit = capture
    await reconciler.grant(ALICE)
    await reconciler.revoke(ALICE, reason="rotated out")
    for seq, event in enumerate(events, start=1):
        await mirror.apply_commit(commit_frame_body(GRANTOR, seq, event))
    # a subscriber independently verified the whole capability lifecycle
    assert mirror.entries.keys() >= set()
    assert not [k for k in mirror.entries if k.startswith(Capability.nsid)]
    rev_paths = [k for k in mirror.entries if k.startswith(Revocation.nsid)]
    assert len(rev_paths) == 1
    rev_record = mirror.get_record(*rev_paths[0].split("/", 1))
    assert rev_record is not None
    assert rev_record["reason"] == "rotated out"


# --- WgToolInterface command construction (no root, injected runner) ---------------


def test_wg_tool_interface_commands():
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv[:2] == ["wg", "show"]:
            return f"{ALICE_KEY}\t10.100.0.2/32\n{BOB_KEY}\tfd00::2/128 10.1.0.0/24\n"
        return ""

    wg = WgToolInterface("wg-fastatp", run_command=runner)
    wg.add_peer(ALICE_KEY, "10.100.0.2/32")
    wg.remove_peer(ALICE_KEY)
    peers = wg.peers()
    assert calls[0] == [
        "wg",
        "set",
        "wg-fastatp",
        "peer",
        ALICE_KEY,
        "allowed-ips",
        "10.100.0.2/32",
    ]
    assert calls[1] == ["wg", "set", "wg-fastatp", "peer", ALICE_KEY, "remove"]
    assert peers == {ALICE_KEY: "10.100.0.2/32", BOB_KEY: "fd00::2/128,10.1.0.0/24"}
    with pytest.raises(EnforcementError):
        wg.add_peer("garbage", "10.0.0.1/32")


async def test_grant_commit_failure_leaves_interface_untouched(grantor):
    """If the record commit fails, no WG peer is opened (record-first)."""
    server, interface, reconciler = grantor

    def boom(_writes):
        raise RuntimeError("commit failed")

    server.apply_writes = boom
    with pytest.raises(RuntimeError):
        await reconciler.grant(ALICE)
    assert interface.peers() == {}


async def test_reconcile_aborts_on_expiry_sweep_failure(grantor):
    """If the expiry sweep commit fails, reconcile aborts before the diff so
    still-present expired records are never re-opened as tunnels."""
    server, interface, reconciler = grantor
    soon = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await reconciler.grant(ALICE, expires_at=soon)  # already expired
    # tunnel is open from the grant; the sweep would normally close it
    assert ALICE_KEY in interface.peers()

    real_apply = server.apply_writes

    def fail_sweep(writes):
        if any(w.collection == Revocation.nsid for w in writes):
            raise RuntimeError("sweep commit failed")
        return real_apply(writes)

    server.apply_writes = fail_sweep
    with pytest.raises(RuntimeError):
        await reconciler.reconcile()
    # aborted: the expired peer was NOT re-added/kept via the diff, and the
    # capability record still exists (sweep never committed)
    assert server.repo.record_count(Capability.nsid) == 1


def test_allowed_ips_comparison_is_order_insensitive():
    from fastatp.authz.reconciler import _ips_key

    assert _ips_key("10.0.0.1/32,10.0.0.2/32") == _ips_key("10.0.0.2/32 10.0.0.1/32")
    assert _ips_key(None) == frozenset()


def test_wg_tool_add_peer_normalizes_multi_ip():
    calls = []

    def _rec(a: list[str]) -> str:
        calls.append(a)
        return ""

    wg = WgToolInterface("wg-fastatp", run_command=_rec)
    wg.add_peer(ALICE_KEY, "10.0.0.1/32 10.0.0.2/32")  # space form in
    assert calls[0][-1] == "10.0.0.1/32,10.0.0.2/32"  # comma form to wg set


# --- grant-by-public-key for documentless did:key subjects -----------

CAROL_KEY = _wg_key(3)


def _did_key_subject() -> str:
    # a plausible did:key (shape only; grant-by-key never resolves it)
    return "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK"


async def test_capability_carries_wg_info_on_by_key_grant(grantor):
    server, interface, reconciler = grantor
    did = _did_key_subject()
    rkey, cap = await reconciler.grant(did, wg_public_key=CAROL_KEY, tunnel_ip="10.100.0.3")
    # authorization plane: the capability is self-contained (embeds the WG id)
    assert cap.wg_public_key == CAROL_KEY and cap.wg_tunnel_ip == "10.100.0.3"
    stored = server.repo.get_record(Capability.nsid, rkey)
    assert stored["wg_public_key"] == CAROL_KEY
    # enforcement plane: peer added by key, no resolution needed
    assert interface.peers() == {CAROL_KEY: "10.100.0.3/32"}
    # and it still exports as a valid lexicon: 1 doc with the new fields
    LexiconDoc.from_json_dict(Capability.to_lexicon_json())


async def test_by_key_grant_needs_tunnel_ip(grantor):
    _server, interface, reconciler = grantor
    with pytest.raises(AuthzError, match="tunnel_ip"):
        await reconciler.grant(_did_key_subject(), wg_public_key=CAROL_KEY)
    with pytest.raises(AuthzError, match="tunnel_ip"):
        await reconciler.grant(_did_key_subject(), wg_public_key=CAROL_KEY, tunnel_ip="not-an-ip")
    assert interface.peers() == {}  # nothing committed, nothing opened


async def test_did_key_subject_is_reconcilable_without_resolution(grantor):
    """The whole point of embedding: reconcile() re-derives a did:key peer from
    the capability, never resolving (which would fail), so drift heals."""
    _server, interface, reconciler = grantor
    did = _did_key_subject()
    await reconciler.grant(did, wg_public_key=CAROL_KEY, tunnel_ip="10.100.0.3")

    # drift: the peer vanished (crash after the record committed)
    interface.remove_peer(CAROL_KEY)
    report = await reconciler.reconcile()
    assert report.added == [did]  # re-derived from the capability, not resolved
    assert CAROL_KEY in interface.peers()
    # nothing reported failed — a did:key subject reconciles cleanly
    assert report.failed == {}


async def test_by_key_revoke_removes_the_right_peer(grantor):
    _server, interface, reconciler = grantor
    did = _did_key_subject()
    await reconciler.grant(did, wg_public_key=CAROL_KEY, tunnel_ip="10.100.0.3")
    assert CAROL_KEY in interface.peers()
    await reconciler.revoke(did, reason="done")
    # removed directly from the capability's embedded key — no resolution attempt
    assert CAROL_KEY not in interface.peers()


async def test_by_key_expiry_is_a_lifecycle_revocation(grantor):
    server, interface, reconciler = grantor
    did = _did_key_subject()
    soon = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await reconciler.grant(did, wg_public_key=CAROL_KEY, tunnel_ip="10.100.0.3", expires_at=soon)
    report = await reconciler.reconcile()
    assert report.expired == [did]
    assert interface.peers() == {}  # expired did:key peer swept without resolution
    assert server.repo.record_count(Capability.nsid) == 0


async def test_did_web_grant_unchanged_leaves_wg_fields_null(grantor):
    """The by-DID path is untouched: capability WG fields stay null so did:web
    key rotation still propagates via re-resolution."""
    _server, interface, reconciler = grantor
    _rkey, cap = await reconciler.grant(ALICE)  # resolves the did:web document
    assert cap.wg_public_key is None and cap.wg_tunnel_ip is None
    assert interface.peers() == {ALICE_KEY: "10.100.0.2/32"}
    # reconcile still resolves alice (embedded info is null) — unchanged behavior
    interface.remove_peer(ALICE_KEY)
    report = await reconciler.reconcile()
    assert report.added == [ALICE] and ALICE_KEY in interface.peers()


async def test_by_key_grant_rejects_did_web(grantor):
    """The by-public-key path is did:key-only: a did:web grantee must resolve
    (so key rotation propagates) and cannot be pinned to a static key."""
    _server, interface, reconciler = grantor
    with pytest.raises(AuthzError, match="did:key"):
        await reconciler.grant(ALICE, wg_public_key=CAROL_KEY, tunnel_ip="10.100.0.3")
    assert interface.peers() == {}  # nothing committed, nothing opened
