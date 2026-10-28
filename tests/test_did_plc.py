"""did:plc — audit-log conformance vectors, chain rules, and PLCResolver.

The vendored ``tests/vectors/plc/audit_log`` corpus (from did-method-plc /
go-didplc, see PROVENANCE.md) is the cross-implementation oracle: every
valid log must verify, every invalid log must be rejected. Synthesized
chains signed with our own keys exercise the recovery rules from the
signing side; tampered copies of real logs are the fail-closed negatives.
FastATP is resolve-only for did:plc — nothing here mints or hosts one.
"""

import base64
import copy
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from fastatp import crypto
from fastatp.cid import CID
from fastatp.dagcbor import encode as dagcbor_encode
from fastatp.did.plc import (
    PLC_DIRECTORY,
    PLCResolver,
    derive_did,
    verify_audit_log,
)
from fastatp.exceptions import DIDDocumentInvalid, DIDNotFound

VECTORS = Path(__file__).parent / "vectors" / "plc" / "audit_log"
VALID = sorted((VECTORS / "valid").glob("*.json"))
INVALID = sorted((VECTORS / "invalid").glob("*.json"))


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text())


# --- conformance vectors ------------------------------------------------------


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_vectors_verify(path):
    log = _load(path)
    doc = verify_audit_log(log[0]["did"], log)
    if path.stem == "log_tombstone":
        assert doc is None  # deactivated DID has no document
    else:
        assert doc is not None
        assert doc.id == log[0]["did"]


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_invalid_vectors_rejected(path):
    log = _load(path)
    with pytest.raises(DIDDocumentInvalid):
        verify_audit_log(log[0]["did"], log)


def test_valid_vector_document_contents():
    """The rendered document carries the head op's keys/services, and the
    ``#atproto`` signing key is usable by the existing document helpers."""
    log = _load(VECTORS / "valid" / "log_bnewbold_robocracy.json")
    doc = verify_audit_log(log[0]["did"], log)
    head = log[-1]["operation"]
    method = doc.get_verification_method("atproto")
    assert "did:key:" + method.public_key_multibase == head["verificationMethods"]["atproto"]
    method.public_key()  # decodes to a real key
    assert doc.service[0].service_endpoint == head["services"]["atproto_pds"]["endpoint"]
    assert doc.also_known_as == head["alsoKnownAs"]
    assert doc.rotation_keys == []  # PLC rotation keys never enter the document


def test_nullified_tombstone_does_not_deactivate():
    """A tombstone that was clobbered by a recovery fork is dead history:
    the DID stays active and the document comes from the fork head."""
    log = _load(VECTORS / "valid" / "log_nullified_tombstone.json")
    tombstones = [e for e in log if e["operation"]["type"] == "plc_tombstone"]
    assert tombstones and all(e["nullified"] for e in tombstones)
    doc = verify_audit_log(log[0]["did"], log)
    assert doc is not None  # NOT tombstoned — the recovery fork won


# --- tampered copies of real logs (fail closed) --------------------------------


@pytest.fixture
def real_log():
    return _load(VECTORS / "valid" / "log_bnewbold_robocracy.json")


def test_empty_log_rejected():
    with pytest.raises(DIDDocumentInvalid, match="empty audit log"):
        verify_audit_log("did:plc:nhxcyu4ewwhl5pqil4dotqjo", [])


def test_wrong_did_rejected(real_log):
    """A real, internally-consistent log cannot be replayed for a different
    DID — the genesis hash is the identity."""
    other = "did:plc:z72i7hdynmk6r22z27h6tvur"
    log = copy.deepcopy(real_log)
    for entry in log:
        entry["did"] = other
    with pytest.raises(DIDDocumentInvalid, match="hashes to"):
        verify_audit_log(other, log)


def test_swapped_signing_key_rejected(real_log):
    """The domain-thief move: swap the head op's signing key. The op's bytes
    change, so the served cid no longer matches (and the sig would fail)."""
    log = copy.deepcopy(real_log)
    thief = crypto.encode_did_key(crypto.generate_keypair("k256").public_key())
    log[-1]["operation"]["verificationMethods"]["atproto"] = thief
    with pytest.raises(DIDDocumentInvalid, match="cid"):
        verify_audit_log(log[0]["did"], log)


def test_recomputed_cid_and_sig_still_rejected(real_log):
    """Same attack with the cid honestly recomputed — now the signature is
    the thing that fails. The directory cannot forge, only withhold."""
    log = copy.deepcopy(real_log)
    op = log[-1]["operation"]
    op["verificationMethods"]["atproto"] = crypto.encode_did_key(
        crypto.generate_keypair("k256").public_key()
    )
    log[-1]["cid"] = CID.for_dag_cbor(dagcbor_encode(op)).encode()
    with pytest.raises(DIDDocumentInvalid, match="signature"):
        verify_audit_log(log[0]["did"], log)


def test_lying_nullified_flag_rejected(real_log):
    log = copy.deepcopy(real_log)
    log[0]["nullified"] = True
    with pytest.raises(DIDDocumentInvalid, match="nullified flag"):
        verify_audit_log(log[0]["did"], log)


def test_truncating_to_genesis_still_verifies(real_log):
    """Withholding the newest ops yields a VALID stale view — the freshness
    gap Tier 2 cannot close. Documented behavior, not a bug."""
    doc = verify_audit_log(real_log[0]["did"], real_log[:1])
    assert doc is not None


def test_malformed_structures_fail_closed():
    """Structurally-broken entries raise DIDDocumentInvalid, never a bare
    KeyError/TypeError — the documented fail-closed contract."""
    # a legacy create op missing recoveryKey (cid honestly recomputed so the
    # structure check, not the cid mismatch, is what fires)
    log = _load(VECTORS / "valid" / "log_legacy_dholms.json")
    del log[0]["operation"]["recoveryKey"]
    log[0]["cid"] = CID.for_dag_cbor(dagcbor_encode(log[0]["operation"])).encode()
    with pytest.raises(DIDDocumentInvalid, match="malformed legacy create"):
        verify_audit_log(log[0]["did"], log[:1])

    # an operation that is not an object at all
    op = "not-an-operation"
    entry = {
        "did": "did:plc:nhxcyu4ewwhl5pqil4dotqjo",
        "operation": op,
        "cid": CID.for_dag_cbor(dagcbor_encode(op)).encode(),
        "nullified": False,
        "createdAt": "2024-01-01T00:00:00.000Z",
    }
    with pytest.raises(DIDDocumentInvalid, match="not an object"):
        verify_audit_log(entry["did"], [entry])


# --- synthesized chains: recovery rules from the signing side ------------------


def _sign_op(op: dict, key) -> dict:
    unsigned = {k: v for k, v in op.items() if k != "sig"}
    sig = crypto.sign_message(key, dagcbor_encode(unsigned))
    return {**unsigned, "sig": base64.urlsafe_b64encode(sig).decode().rstrip("=")}


def _plc_op(rotation_keys, signing_key, prev, handle="alice.test"):
    return {
        "type": "plc_operation",
        "rotationKeys": [crypto.encode_did_key(k.public_key()) for k in rotation_keys],
        "verificationMethods": {"atproto": crypto.encode_did_key(signing_key.public_key())},
        "alsoKnownAs": [f"at://{handle}"],
        "services": {
            "atproto_pds": {
                "type": "AtprotoPersonalDataServer",
                "endpoint": "https://pds.test",
            }
        },
        "prev": prev,
    }


def _entry(did, op, at):
    return {
        "did": did,
        "operation": op,
        "cid": CID.for_dag_cbor(dagcbor_encode(op)).encode(),
        "nullified": False,
        "createdAt": at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }


@pytest.fixture
def keys():
    recovery = crypto.generate_keypair("p256")
    rotation = crypto.generate_keypair("k256")
    signing = crypto.generate_keypair("k256")
    return recovery, rotation, signing


def _genesis_chain(recovery, rotation, signing):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    genesis = _sign_op(_plc_op([recovery, rotation], signing, None), rotation)
    did = derive_did(genesis)
    return did, [_entry(did, genesis, t0)], t0


def test_synthesized_create_update_tombstone(keys):
    recovery, rotation, signing = keys
    did, log, t0 = _genesis_chain(recovery, rotation, signing)
    doc = verify_audit_log(did, log)
    assert doc is not None and doc.id == did

    update = _sign_op(
        _plc_op([recovery, rotation], signing, log[0]["cid"], handle="alice.example"),
        rotation,
    )
    log.append(_entry(did, update, t0 + timedelta(days=1)))
    doc = verify_audit_log(did, log)
    assert doc.also_known_as == ["at://alice.example"]

    tomb = _sign_op({"type": "plc_tombstone", "prev": log[1]["cid"]}, rotation)
    log.append(_entry(did, tomb, t0 + timedelta(days=2)))
    assert verify_audit_log(did, log) is None


def test_synthesized_recovery_fork(keys):
    """The recovery key (higher authority) clobbers a rotation-key takeover
    inside the window; the replay marks the branch nullified."""
    recovery, rotation, signing = keys
    did, log, t0 = _genesis_chain(recovery, rotation, signing)

    attacker = crypto.generate_keypair("k256")
    takeover = _sign_op(_plc_op([attacker], attacker, log[0]["cid"]), rotation)
    recovery_op = _sign_op(_plc_op([recovery], signing, log[0]["cid"]), recovery)

    bad = _entry(did, takeover, t0 + timedelta(hours=1))
    bad["nullified"] = True
    log += [bad, _entry(did, recovery_op, t0 + timedelta(hours=2))]
    doc = verify_audit_log(did, log)
    assert doc.get_verification_method("atproto")  # recovered state

    # the same fork signed by the LOWER-authority key must be rejected
    log[2] = _entry(
        did,
        _sign_op(_plc_op([recovery], signing, log[0]["cid"]), rotation),
        t0 + timedelta(hours=2),
    )
    with pytest.raises(DIDDocumentInvalid, match="signature"):
        verify_audit_log(did, log)


def test_synthesized_late_recovery_rejected(keys):
    recovery, rotation, signing = keys
    did, log, t0 = _genesis_chain(recovery, rotation, signing)
    takeover = _sign_op(_plc_op([rotation], signing, log[0]["cid"]), rotation)
    recovery_op = _sign_op(_plc_op([recovery], signing, log[0]["cid"]), recovery)
    bad = _entry(did, takeover, t0 + timedelta(hours=1))
    bad["nullified"] = True
    log += [bad, _entry(did, recovery_op, t0 + timedelta(hours=1) + timedelta(hours=73))]
    with pytest.raises(DIDDocumentInvalid, match="72h"):
        verify_audit_log(did, log)


def test_malformed_services_in_head_fail_closed(keys):
    """A validly-signed head op with broken document data (services entry
    missing its endpoint) still fails closed at rendering — no KeyError."""
    recovery, rotation, signing = keys
    op = _plc_op([recovery, rotation], signing, None)
    op["services"] = {"atproto_pds": {"type": "AtprotoPersonalDataServer"}}
    genesis = _sign_op(op, rotation)
    did = derive_did(genesis)
    entry = _entry(did, genesis, datetime(2026, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(DIDDocumentInvalid, match="malformed document data"):
        verify_audit_log(did, [entry])


# --- PLCResolver --------------------------------------------------------------


def _mock_resolver(handler, **kwargs) -> PLCResolver:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return PLCResolver(client=client, **kwargs)


@pytest.fixture
def directory(real_log):
    """A fake plc.directory serving one DID's audit log + rendered doc."""
    did = real_log[0]["did"]
    calls = {"audit": 0, "doc": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/{did}/log/audit":
            calls["audit"] += 1
            return httpx.Response(200, json=real_log)
        if request.url.path == f"/{did}":
            calls["doc"] += 1
            return httpx.Response(200, json=verify_audit_log(did, real_log).to_json_dict())
        return httpx.Response(404)

    return did, handler, calls


async def test_resolver_tier2_verifies_audit_log(directory):
    did, handler, calls = directory
    resolver = _mock_resolver(handler)  # verify=True is the default
    doc = await resolver(did)
    assert doc.id == did
    assert calls == {"audit": 1, "doc": 0}  # never trusts the rendered doc


async def test_resolver_tier1_fetches_document(directory):
    did, handler, calls = directory
    resolver = _mock_resolver(handler, verify=False)
    doc = await resolver(did)
    assert doc.id == did
    assert calls == {"audit": 0, "doc": 1}


async def test_resolver_caches_and_invalidates(directory):
    did, handler, calls = directory
    resolver = _mock_resolver(handler)
    await resolver(did)
    await resolver(did)
    assert calls["audit"] == 1  # served from cache
    resolver.invalidate(did)
    await resolver(did)
    assert calls["audit"] == 2


async def test_resolver_rejects_tampered_log(real_log):
    did = real_log[0]["did"]
    evil = copy.deepcopy(real_log)
    evil[-1]["operation"]["verificationMethods"]["atproto"] = crypto.encode_did_key(
        crypto.generate_keypair("k256").public_key()
    )
    evil[-1]["cid"] = CID.for_dag_cbor(dagcbor_encode(evil[-1]["operation"])).encode()

    resolver = _mock_resolver(lambda req: httpx.Response(200, json=evil))
    with pytest.raises(DIDDocumentInvalid):
        await resolver(did)


async def test_resolver_tombstoned_is_not_found():
    log = _load(VECTORS / "valid" / "log_tombstone.json")
    resolver = _mock_resolver(lambda req: httpx.Response(200, json=log))
    with pytest.raises(DIDNotFound, match="tombstoned"):
        await resolver(log[0]["did"])


async def test_resolver_404_and_syntax():
    resolver = _mock_resolver(lambda req: httpx.Response(404))
    with pytest.raises(DIDNotFound):
        await resolver("did:plc:nhxcyu4ewwhl5pqil4dotqjo")
    with pytest.raises(DIDNotFound, match="not a valid did:plc"):
        await resolver("did:plc:short")
    with pytest.raises(DIDNotFound, match="not a valid did:plc"):
        await resolver("did:web:example.com")


# --- live directory sampler (skips cleanly offline) -----------------------------


async def test_live_random_sample_fully_verifies():
    """Every did:plc on the live network is a free, self-certifying vector:
    sample a few at a random point in directory history and run full Tier 2
    verification. A failure here is (almost always) OUR encoding bug — pin
    the printed DID's audit log as a regression fixture."""
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    end = datetime.now(timezone.utc) - timedelta(days=7)
    at = start + (end - start) * random.random()
    cursor = at.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{PLC_DIRECTORY}/export",
                params={"count": 50, "after": cursor},
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            pytest.skip(f"plc.directory unreachable: {e}")

        dids: list[str] = []
        for line in response.text.splitlines():
            did = json.loads(line)["did"]
            if did not in dids:
                dids.append(did)
        resolver = PLCResolver(client=client)  # verify=True
        for did in dids[:3]:
            try:
                doc = await resolver(did)
                assert doc.id == did
            except DIDNotFound:
                pass  # tombstoned/deactivated is a legitimate outcome
            except DIDDocumentInvalid as e:
                pytest.fail(
                    f"live audit log failed verification for {did} "
                    f"(sampled at {cursor}): {e} — capture "
                    f"{PLC_DIRECTORY}/{did}/log/audit as a regression fixture"
                )
