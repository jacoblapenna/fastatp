"""v0.4: capability records + WireGuard enforcement + the reconciler."""

import base64
from datetime import UTC, datetime, timedelta

import pytest

from fastatp import DIDDocument, Peer, PeerServer, RepoMirror, build_did_document, crypto
from fastatp.authz import (
    ACTION_SUBSCRIBE,
    Capability,
    MemoryWireGuardInterface,
    Revocation,
    WgToolInterface,
    WireGuardReconciler,
    build_wireguard_service,
    live_capabilities,
    overlay_address,
    parse_wireguard_service,
)
from fastatp.exceptions import AuthzError, EnforcementError
from fastatp.lex.schema import LexiconDoc
from fastatp.tid import generate_tid

GRANTOR = "did:web:grantor.example"
ALICE = "did:web:alice.example"
BOB = "did:web:bob.example"


def _wg_key(seed: int) -> str:
    return base64.b64encode(bytes([seed]) * 32).decode()


ALICE_KEY = _wg_key(1)
BOB_KEY = _wg_key(2)

DOCS: dict[str, DIDDocument] = {}


def _register(did: str, wg_key: str, endpoint: str | None = None):
    key = crypto.generate_keypair("k256")
    DOCS[did] = build_did_document(
        did=did,
        signing_key=key.public_key(),
        extra_services=[build_wireguard_service(wg_key, endpoint)],
    )


_register(ALICE, ALICE_KEY, "203.0.113.5:51820")
_register(BOB, BOB_KEY)


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
    assert info.tunnel_ip == overlay_address(ALICE_KEY)  # derived, never carried
    assert info.endpoint == "203.0.113.5:51820"
    assert info.allowed_ips == f"{overlay_address(ALICE_KEY)}/128"
    # no endpoint (roaming)
    info6 = parse_wireguard_service(DOCS[BOB])
    assert info6.endpoint is None
    assert info6.allowed_ips == f"{overlay_address(BOB_KEY)}/128"


def test_wireguard_service_validation():
    with pytest.raises(EnforcementError):
        build_wireguard_service("not-a-key")
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
    assert interface.peers() == {ALICE_KEY: f"{overlay_address(ALICE_KEY)}/128"}


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
    assert interface.peers() == {ALICE_KEY: f"{overlay_address(ALICE_KEY)}/128"}


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
    soon = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    await reconciler.grant(ALICE, expires_at=soon)
    assert interface.peers()  # tunnel open while live
    later = datetime.now(UTC) + timedelta(hours=1)
    report = await reconciler.reconcile(now=later)
    assert report.expired == [ALICE]
    assert interface.peers() == {}  # sweep removed the peer
    assert server.repo.record_count(Capability.nsid) == 0
    reasons = [
        server.repo.get_record(Revocation.nsid, rk)["reason"]
        for rk, _ in server.repo.list_records(Revocation.nsid)
    ]
    assert reasons == ["expired"]


async def test_reconcile_never_consults_a_document(grantor):
    """The record carries the key, so reconcile has no reason to resolve: a
    grantee whose document vanished (DNS blip) is healed from the record and
    nothing is reported failed."""
    _server, interface, reconciler = grantor
    await reconciler.grant(ALICE)
    interface.remove_peer(ALICE_KEY)  # drift
    saved = DOCS.pop(ALICE)
    try:
        report = await reconciler.reconcile()
    finally:
        DOCS[ALICE] = saved
    assert report.added == [ALICE] and report.failed == {}
    assert ALICE_KEY in interface.peers()


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
    soon = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
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
    rkey, cap = await reconciler.grant(did, wg_public_key=CAROL_KEY)
    # authorization plane: the capability is self-contained (embeds the WG key)
    assert cap.wg_public_key == CAROL_KEY
    stored = server.repo.get_record(Capability.nsid, rkey)
    assert stored["wg_public_key"] == CAROL_KEY
    # enforcement plane: peer added by key, no resolution needed
    assert interface.peers() == {CAROL_KEY: f"{overlay_address(CAROL_KEY)}/128"}
    # and it still exports as a valid lexicon: 1 doc with the new fields
    LexiconDoc.from_json_dict(Capability.to_lexicon_json())


async def test_did_key_subject_is_reconcilable_without_resolution(grantor):
    """The whole point of embedding: reconcile() re-derives a did:key peer from
    the capability, never resolving (which would fail), so drift heals."""
    _server, interface, reconciler = grantor
    did = _did_key_subject()
    await reconciler.grant(did, wg_public_key=CAROL_KEY)

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
    await reconciler.grant(did, wg_public_key=CAROL_KEY)
    assert CAROL_KEY in interface.peers()
    await reconciler.revoke(did, reason="done")
    # removed directly from the capability's embedded key — no resolution attempt
    assert CAROL_KEY not in interface.peers()


async def test_by_key_expiry_is_a_lifecycle_revocation(grantor):
    server, interface, reconciler = grantor
    did = _did_key_subject()
    soon = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    await reconciler.grant(did, wg_public_key=CAROL_KEY, expires_at=soon)
    report = await reconciler.reconcile()
    assert report.expired == [did]
    assert interface.peers() == {}  # expired did:key peer swept without resolution
    assert server.repo.record_count(Capability.nsid) == 0


async def test_did_web_grant_carries_the_resolved_key(grantor):
    """A did:web grant resolves the document and writes the key it found into
    the record: the doorman reads records, not documents, so a record without
    the key would open the kernel to a caller the doorman cannot name."""
    server, interface, reconciler = grantor
    rkey, cap = await reconciler.grant(ALICE)  # resolves the did:web document
    assert cap.wg_public_key == ALICE_KEY
    assert server.repo.get_record(Capability.nsid, rkey)["wg_public_key"] == ALICE_KEY
    assert interface.peers() == {ALICE_KEY: f"{overlay_address(ALICE_KEY)}/128"}
    # drift heals from the record, not the document
    interface.remove_peer(ALICE_KEY)
    report = await reconciler.reconcile()
    assert report.added == [ALICE] and ALICE_KEY in interface.peers()


async def test_a_rotated_key_is_a_stranger_until_granted_again(grantor):
    """A did:web grantee that publishes a new WireGuard key is not followed:
    the record is what the grantor signed, so reconcile keeps enforcing the
    old key and the new one cannot handshake. Granting again is the
    grantor's act, and supersedes the record with the new key."""
    server, interface, reconciler = grantor
    old_rkey, _cap = await reconciler.grant(ALICE, note="field gateway")
    new_key = _wg_key(9)
    saved = DOCS[ALICE]
    _register(ALICE, new_key, "203.0.113.5:51820")  # alice rotates her WireGuard key
    try:
        report = await reconciler.reconcile()
        # nothing moved: the old key is still the admitted one
        assert report.kept == [ALICE] and not report.changed and report.failed == {}
        assert interface.peers() == {ALICE_KEY: f"{overlay_address(ALICE_KEY)}/128"}
        assert live_capabilities(server.repo)[ALICE][1].wg_public_key == ALICE_KEY
        # the grantor decides, by granting again
        rkey, cap = await reconciler.grant(ALICE, note="field gateway")
    finally:
        DOCS[ALICE] = saved
    assert rkey != old_rkey and cap.wg_public_key == new_key
    assert interface.peers() == {new_key: f"{overlay_address(new_key)}/128"}
    revocations = [
        server.repo.get_record(Revocation.nsid, r)
        for r, _ in server.repo.list_records(Revocation.nsid)
    ]
    assert [(r["capability_rkey"], r["reason"]) for r in revocations] == [(old_rkey, "superseded")]


async def test_a_regrant_keeps_a_key_another_grant_still_names(grantor):
    """Two DIDs on one machine share a WireGuard key. Re-granting one of them
    with a new key must not pull the shared key out from under the other."""
    _server, interface, reconciler = grantor
    device = _did_key_subject()
    await reconciler.grant(ALICE)  # alice's document says ALICE_KEY
    await reconciler.grant(device, wg_public_key=ALICE_KEY)  # a device on alice's machine
    new_key = _wg_key(9)
    saved = DOCS[ALICE]
    _register(ALICE, new_key, "203.0.113.5:51820")
    try:
        await reconciler.grant(ALICE)  # alice re-granted with her new key
    finally:
        DOCS[ALICE] = saved
    assert interface.peers() == {
        ALICE_KEY: f"{overlay_address(ALICE_KEY)}/128",  # still named by the device's grant
        new_key: f"{overlay_address(new_key)}/128",
    }


async def test_a_record_without_a_key_is_reported_not_resolved(grantor):
    """A record written by a pre-0.3 reconciler for a did:web grantee carries
    no key. Reconcile does not go and fetch one: it reports the record and
    admits nobody for it until the grantor grants again."""
    server, interface, reconciler = grantor
    old = Capability(subject=ALICE, actions=[ACTION_SUBSCRIBE])  # as 0.2 wrote it
    server.put_record(Capability.nsid, generate_tid(), old)
    report = await reconciler.reconcile()
    assert "grant again" in report.failed[ALICE]
    assert interface.peers() == {}
    assert live_capabilities(server.repo)[ALICE][1].wg_public_key is None  # untouched
    _rkey, cap = await reconciler.grant(ALICE)
    assert cap.wg_public_key == ALICE_KEY
    assert interface.peers() == {ALICE_KEY: f"{overlay_address(ALICE_KEY)}/128"}


async def test_regranting_a_keyless_record_prunes_the_peer_it_once_opened(grantor):
    """0.2 programmed the kernel from the document and wrote no key. When the
    grantor grants again after a rotation, the record cannot say which old
    peer to remove, so grant prunes every peer no live record explains, the
    way revoke does for such a record."""
    server, interface, reconciler = grantor
    stale = _wg_key(7)  # the key 0.2 resolved and programmed back then
    server.put_record(
        Capability.nsid, generate_tid(), Capability(subject=ALICE, actions=[ACTION_SUBSCRIBE])
    )
    interface.add_peer(stale, f"{overlay_address(stale)}/128")
    await reconciler.grant(ALICE)  # alice's document now says ALICE_KEY
    assert interface.peers() == {ALICE_KEY: f"{overlay_address(ALICE_KEY)}/128"}


async def test_by_key_grant_rejects_did_web(grantor):
    """The by-public-key path is did:key-only: a did:web grantee's document is
    the one source for its key, and the next reconcile would overwrite a
    hand-given key from it anyway."""
    _server, interface, reconciler = grantor
    with pytest.raises(AuthzError, match="did:key"):
        await reconciler.grant(ALICE, wg_public_key=CAROL_KEY)
    assert interface.peers() == {}  # nothing committed, nothing opened
