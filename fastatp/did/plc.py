"""did:plc resolution: directory fetch (Tier 1) and audit-log verification (Tier 2).

FastATP never hosts or mints did:plc identities — the method requires a
central directory server, which is exactly the trusted third party the
philosophy forbids on core paths. This module exists so a peer can *verify*
records authored by did:plc identities (e.g. atproto/Bluesky-world users):
resolve-only, an edge adapter a verifier opts into via ``chain()``.

Trust model (be honest about it):

- ``verify=True`` (default): fetch the audit log and verify the
  self-certifying operation chain — the genesis operation must hash to the
  DID itself, every operation must be signed by a rotation key of its
  predecessor, and nullified forks must obey the recovery rules. The document
  is rendered from the *verified* chain head; the directory's own rendering
  is never trusted for keys. A compromised directory cannot forge history
  (that would require a SHA-256 preimage); it can only *withhold* the newest
  operations (freshness/availability), which no client-side check can detect.
- ``verify=False``: trust the directory's served document — what most
  atproto clients do today. One HTTP fetch, full trust in the directory
  and the TLS path to it.

Spec: ``reference/did-method-plc/spec/v0.1/did-plc.md``; validation rules
mirror the TypeScript reference (``packages/lib/src/data.ts``) and are
exercised against the vendored conformance vectors in ``tests/vectors/plc/``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

import httpx

from fastatp.cid import CID, _base32_encode
from fastatp.crypto import decode_did_key, verify_signature
from fastatp.dagcbor import encode as dagcbor_encode
from fastatp.did.document import DIDDocument, Service, VerificationMethod
from fastatp.exceptions import (
    CryptoError,
    DIDDocumentInvalid,
    DIDNotFound,
    DIDResolutionTimeout,
)

PLC_DIRECTORY = "https://plc.directory"

_DID_PLC_RE = re.compile(r"^did:plc:[a-z2-7]{24}$")
# NB: fullmatch, and no ``$`` anchor — ``$`` would match before a trailing
# newline, letting a sig with an embedded "\n" through the alphabet check
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]+\Z")
_RECOVERY_WINDOW = timedelta(hours=72)


def _require_did_plc(did: str) -> str:
    """Validate did:plc syntax; identifiers are normalized to lower-case."""
    did = did.lower()
    if not _DID_PLC_RE.match(did):
        raise DIDNotFound(f"not a valid did:plc: {did!r}")
    return did


def _decode_sig(sig: str) -> bytes:
    """Strict unpadded base64url decode (RFC 4648 §5, zero padding bits).

    Non-canonical encodings MUST be rejected: otherwise the ``sig`` string
    (and therefore the operation's CID) could be malleated without
    invalidating the signature. Rejects padding characters, non-alphabet
    bytes (including whitespace), and non-zero trailing padding bits — the
    re-encode round-trip catches all of the above in one comparison.
    """
    if not _B64URL_RE.fullmatch(sig):
        raise DIDDocumentInvalid("sig is not canonical unpadded base64url")
    raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
    if base64.urlsafe_b64encode(raw).decode().rstrip("=") != sig:
        raise DIDDocumentInvalid("sig has non-zero base64url padding bits")
    return raw


# --- operations -----------------------------------------------------------------


def _ensure_at_prefix(value: str) -> str:
    if value.startswith("at://"):
        return value
    return "at://" + value.removeprefix("http://").removeprefix("https://")


def _ensure_http_prefix(value: str) -> str:
    if value.startswith(("http://", "https://")):
        return value
    return f"https://{value}"


def _normalize_op(op: dict[str, Any]) -> dict[str, Any]:
    """Normalize a legacy ``create`` genesis op to ``plc_operation`` form.

    Hashing and signing always use the ORIGINAL serialized form; only the
    interpreted fields (rotation keys, document data) go through this.
    """
    if op.get("type") == "plc_operation":
        return op
    if op.get("type") != "create":
        raise DIDDocumentInvalid(f"unknown operation type {op.get('type')!r}")
    try:
        return {
            "type": "plc_operation",
            "verificationMethods": {"atproto": op["signingKey"]},
            "rotationKeys": [op["recoveryKey"], op["signingKey"]],
            "alsoKnownAs": [_ensure_at_prefix(op["handle"])],
            "services": {
                "atproto_pds": {
                    "type": "AtprotoPersonalDataServer",
                    "endpoint": _ensure_http_prefix(op["service"]),
                }
            },
            "prev": op["prev"],
            "sig": op["sig"],
        }
    except (KeyError, TypeError, AttributeError):
        # fail closed on malformed structure, per the documented contract
        raise DIDDocumentInvalid("malformed legacy create operation") from None


def _unsigned_bytes(op: dict[str, Any]) -> bytes:
    """Canonical DAG-CBOR of the operation with the ``sig`` field removed."""
    return dagcbor_encode({k: v for k, v in op.items() if k != "sig"})


def derive_did(genesis_op: dict[str, Any]) -> str:
    """The DID a genesis operation self-certifies:
    ``did:plc:`` + base32(sha256(dag-cbor(op)))[:24]."""
    digest = hashlib.sha256(dagcbor_encode(genesis_op)).digest()
    return "did:plc:" + _base32_encode(digest)[:24]


def _verify_op_sig(allowed_did_keys: list[str], op: dict[str, Any]) -> str:
    """Verify ``op.sig`` against a rotation-key list; return the signer.

    Returns the FIRST listed did:key that verifies (list position is
    authority order — ties on duplicate keys resolve to the highest
    authority, matching the reference implementation's ``indexOf``).
    """
    sig = op.get("sig")
    if not isinstance(sig, str):
        raise DIDDocumentInvalid("operation has no sig field")
    sig_bytes = _decode_sig(sig)
    payload = _unsigned_bytes(op)
    for did_key in allowed_did_keys:
        try:
            key = decode_did_key(did_key)
        except CryptoError:
            continue  # a key we cannot decode cannot have produced the sig
        if verify_signature(key, payload, sig_bytes):
            return did_key
    raise DIDDocumentInvalid("operation signature does not verify against any allowed rotation key")


def _parse_created_at(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise DIDDocumentInvalid(f"bad createdAt timestamp {value!r}") from None


# --- audit-log verification (Tier 2) ----------------------------------------------


class _Entry:
    __slots__ = ("cid", "created_at", "nullified", "op")

    def __init__(self, op: dict[str, Any], cid: str, created_at: datetime) -> None:
        self.op = op
        self.cid = cid
        self.created_at = created_at
        self.nullified = False


def verify_audit_log(did: str, log: list[dict[str, Any]]) -> DIDDocument | None:
    """Verify a full PLC audit log and render the document from its head.

    Replays the directory's own acceptance rules over every entry —
    including nullified forks — and fails closed (``DIDDocumentInvalid``)
    on any inconsistency:

    - the genesis operation must hash to ``did`` (self-certification);
    - every entry's served ``cid`` must match the recomputed CID;
    - every operation must be signed by a rotation key of the operation its
      ``prev`` points to (or its own keys, for genesis) — low-S compact
      ECDSA over the sig-less DAG-CBOR, strict base64url;
    - a nullifying (recovery) fork must be signed by a strictly
      higher-authority key than the nullified branch's signer, within the
      72h recovery window (exactly 72h is allowed), with a monotonically
      increasing timestamp;
    - the served ``nullified`` flags must match the replay's outcome;
    - a tombstone can only ever sit at the head of the active chain.

    Returns ``None`` for a tombstoned (deactivated) DID.
    """
    did = _require_did_plc(did)
    if not log:
        raise DIDDocumentInvalid(f"{did}: empty audit log")

    entries: list[_Entry] = []
    for i, raw in enumerate(log):
        try:
            op = raw["operation"]
            claimed_cid = raw["cid"]
            created_at = _parse_created_at(raw["createdAt"])
        except (KeyError, TypeError):
            raise DIDDocumentInvalid(f"{did}: malformed audit log entry {i}") from None
        if not isinstance(op, dict):
            raise DIDDocumentInvalid(f"{did}: entry {i} operation is not an object")
        if raw.get("did") != did:
            raise DIDDocumentInvalid(f"{did}: entry {i} is for {raw.get('did')!r}")
        cid = CID.for_dag_cbor(dagcbor_encode(op)).encode()
        if claimed_cid != cid:
            raise DIDDocumentInvalid(
                f"{did}: entry {i} served cid {claimed_cid!r} != computed {cid!r}"
            )

        active = [e for e in entries if not e.nullified]
        if i == 0:
            # genesis: signed by its OWN rotation keys, prev null, hashes to the DID
            if op.get("type") == "plc_tombstone":
                raise DIDDocumentInvalid(f"{did}: genesis operation is a tombstone")
            if op.get("prev") is not None:
                raise DIDDocumentInvalid(f"{did}: genesis operation has non-null prev")
            _verify_op_sig(_normalize_op(op)["rotationKeys"], op)
            expected = derive_did(op)
            if expected != did:
                raise DIDDocumentInvalid(
                    f"{did}: genesis operation hashes to {expected!r} (forged identity)"
                )
        else:
            prev = op.get("prev")
            if not isinstance(prev, str):
                raise DIDDocumentInvalid(f"{did}: entry {i} has no prev pointer")
            idx = next((j for j, e in enumerate(active) if e.cid == prev), None)
            if idx is None:
                raise DIDDocumentInvalid(
                    f"{did}: entry {i} prev {prev!r} is not an active operation "
                    "(misordered, nullified, or unknown)"
                )
            last = active[idx]
            to_nullify = active[idx + 1 :]
            if last.op.get("type") == "plc_tombstone":
                raise DIDDocumentInvalid(f"{did}: entry {i} extends a tombstone")
            last_keys = _normalize_op(last.op)["rotationKeys"]
            if not to_nullify:
                _verify_op_sig(last_keys, op)
            else:
                # recovery fork: must be signed by a STRICTLY higher-authority
                # key than the one that signed the branch being nullified
                disputed = _verify_op_sig(last_keys, to_nullify[0].op)
                stronger = last_keys[: last_keys.index(disputed)]
                _verify_op_sig(stronger, op)
                if created_at <= active[-1].created_at:
                    raise DIDDocumentInvalid(
                        f"{did}: recovery entry {i} timestamp is not monotonic"
                    )
                if created_at - to_nullify[0].created_at > _RECOVERY_WINDOW:
                    raise DIDDocumentInvalid(f"{did}: recovery entry {i} is outside the 72h window")
                for e in to_nullify:
                    e.nullified = True
        entries.append(_Entry(op, cid, created_at))

    # the directory's nullified annotations must agree with the replay
    for i, (raw, entry) in enumerate(zip(log, entries, strict=True)):
        if bool(raw.get("nullified")) != entry.nullified:
            raise DIDDocumentInvalid(f"{did}: entry {i} nullified flag disagrees with chain replay")

    head = [e for e in entries if not e.nullified][-1]
    if head.op.get("type") == "plc_tombstone":
        return None  # deactivated
    return _document_from_op(did, _normalize_op(head.op))


def _document_from_op(did: str, op: dict[str, Any]) -> DIDDocument:
    """Render the verified chain head as an atproto-shaped DID document.

    Mirrors the spec's "Presentation as DID Document": ``rotationKeys`` are
    PLC-internal and never appear in the document; verification methods keep
    their map key as the fragment.
    """
    methods = []
    for fragment, did_key in dict(op.get("verificationMethods") or {}).items():
        if not isinstance(did_key, str) or not did_key.startswith("did:key:z"):
            raise DIDDocumentInvalid(f"{did}: verification method {fragment!r} is not a did:key")
        methods.append(
            VerificationMethod(
                id=f"{did}#{fragment}",
                controller=did,
                public_key_multibase=did_key[len("did:key:") :],
            )
        )
    try:
        services = [
            Service(id=f"#{sid}", type=svc["type"], service_endpoint=svc["endpoint"])
            for sid, svc in dict(op.get("services") or {}).items()
        ]
        return DIDDocument(
            id=did,
            also_known_as=list(op.get("alsoKnownAs") or []),
            verification_method=methods,
            service=services,
        )
    except (KeyError, TypeError, ValueError):
        # fail closed on malformed structure, per the documented contract
        raise DIDDocumentInvalid(f"{did}: malformed document data in chain head") from None


# --- resolver (Tier 1 + Tier 2) ----------------------------------------------------


class PLCResolver:
    """``DIDResolver`` for did:plc via a PLC directory. Resolve-only.

    With ``verify=True`` (default) each resolution fetches
    ``{directory}/{did}/log/audit`` and runs :func:`verify_audit_log`, so the
    document comes from the self-certified chain, not the directory's word.
    With ``verify=False`` it fetches the directory's rendered document and
    trusts it (the ecosystem-typical posture).

    Results are cached in a small LRU for ``cache_ttl`` seconds (the PLC
    directory sets no meaningful cache headers, so the TTL is ours) with the
    same ``invalidate()`` seam as ``WebResolver`` — a firehose ``#identity``
    event should invalidate rather than wait out the TTL.
    """

    def __init__(
        self,
        *,
        directory: str = PLC_DIRECTORY,
        verify: bool = True,
        timeout: float = 10.0,
        cache_size: int = 1024,
        cache_ttl: float = 300.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._directory = directory.rstrip("/")
        self._verify = verify
        self._timeout = timeout
        self._cache_size = cache_size
        self._cache_ttl = cache_ttl
        self._cache: OrderedDict[str, tuple[float, DIDDocument]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[DIDDocument]] = {}
        self._client = client

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(follow_redirects=False)
        return self._client

    def invalidate(self, did: str) -> None:
        """Drop a cached document so the next resolve refetches."""
        self._cache.pop(did.lower(), None)

    async def __call__(self, did: str) -> DIDDocument:
        did = _require_did_plc(did)
        now = time.monotonic()
        cached = self._cache.get(did)
        if cached is not None:
            expires_at, doc = cached
            if now < expires_at:
                self._cache.move_to_end(did)
                return doc
            del self._cache[did]

        task = self._inflight.get(did)
        if task is None:
            task = asyncio.create_task(self._resolve(did))
            self._inflight[did] = task
            task.add_done_callback(lambda _t: self._inflight.pop(did, None))
        return await asyncio.shield(task)

    async def _resolve(self, did: str) -> DIDDocument:
        if self._verify:
            log = await self._fetch_json(did, f"{self._directory}/{did}/log/audit")
            if not isinstance(log, list):
                raise DIDDocumentInvalid(f"{did}: audit log is not a JSON array")
            doc = verify_audit_log(did, log)
            if doc is None:
                raise DIDNotFound(f"{did}: tombstoned (deactivated)")
        else:
            data = await self._fetch_json(did, f"{self._directory}/{did}")
            doc = DIDDocument.from_json_dict(data)
            if doc.id != did:
                raise DIDDocumentInvalid(f"{did}: document id mismatch ({doc.id!r})")
        if self._cache_ttl > 0:
            self._cache[did] = (time.monotonic() + self._cache_ttl, doc)
            self._cache.move_to_end(did)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return doc

    async def _fetch_json(self, did: str, url: str) -> Any:
        try:
            response = await self._get_client().get(url, timeout=self._timeout)
        except httpx.TimeoutException as e:
            raise DIDResolutionTimeout(f"{did}: {e}") from None
        except httpx.HTTPError as e:
            raise DIDNotFound(f"{did}: {e}") from None
        if response.status_code == 404:
            raise DIDNotFound(f"{did}: 404 from {url}")
        if response.status_code == 410:
            raise DIDNotFound(f"{did}: tombstoned (410 from {url})")
        if response.status_code != 200:
            raise DIDNotFound(f"{did}: HTTP {response.status_code} from {url}")
        try:
            return response.json()
        except ValueError as e:
            raise DIDDocumentInvalid(f"{did}: response is not JSON: {e}") from None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
