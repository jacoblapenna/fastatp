"""DIDDocument model and build_did_document().

Documents are atproto-shaped (Multikey verification methods, optional
AtprotoPersonalDataServer service). FastATP never writes documents anywhere:
callers store and serve them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fastatp.crypto import PublicKey, decode_multikey, encode_did_key, encode_multikey
from fastatp.exceptions import CryptoError, DIDDocumentInvalid
from fastatp.lex.types import base58btc_decode, validate_did

DID_CONTEXT = "https://www.w3.org/ns/did/v1"
MULTIKEY_CONTEXT = "https://w3id.org/security/multikey/v1"


# legacy 2019 verification-method types: bare base58btc multibase of an
# UNCOMPRESSED point, no multicodec prefix. The did spec sanctions READING
# them during the transition period; FastATP never writes them (decode
# pattern from MarshalX/atproto@d2e4214 atproto_crypto/did.py).
_LEGACY_2019_CURVES = {
    "EcdsaSecp256k1VerificationKey2019": "k256",
    "EcdsaSecp256r1VerificationKey2019": "p256",
}


def _decode_legacy_2019(multibase: str, curve: str) -> PublicKey:
    from cryptography.hazmat.primitives.asymmetric import ec

    if not multibase.startswith("z"):
        raise CryptoError("legacy verification key must be base58btc ('z' prefix)")
    try:
        point = base58btc_decode(multibase[1:])
        curve_obj = ec.SECP256K1() if curve == "k256" else ec.SECP256R1()
        return ec.EllipticCurvePublicKey.from_encoded_point(curve_obj, point)
    except (ValueError, TypeError) as e:
        raise CryptoError(f"invalid legacy 2019 verification key: {e}") from None


class VerificationMethod(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    type: str = "Multikey"
    controller: str
    public_key_multibase: str = Field(alias="publicKeyMultibase")

    def public_key(self) -> PublicKey:
        legacy_curve = _LEGACY_2019_CURVES.get(self.type)
        if legacy_curve is not None:
            return _decode_legacy_2019(self.public_key_multibase, legacy_curve)
        return decode_multikey(self.public_key_multibase)


class Service(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    type: str
    service_endpoint: str = Field(alias="serviceEndpoint")


class DIDDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    context: list[str] = Field(
        alias="@context", default_factory=lambda: [DID_CONTEXT, MULTIKEY_CONTEXT]
    )
    id: str
    also_known_as: list[str] = Field(alias="alsoKnownAs", default_factory=list)
    verification_method: list[VerificationMethod] = Field(
        alias="verificationMethod", default_factory=list
    )
    rotation_keys: list[str] = Field(alias="rotationKeys", default_factory=list)
    service: list[Service] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_id(self) -> DIDDocument:
        try:
            validate_did(self.id)
        except ValueError as e:
            raise ValueError(f"DID document id: {e}") from None
        return self

    def get_verification_method(self, kid: str | None = None) -> VerificationMethod:
        """Find the verification method matching ``kid`` (or the single default).

        ``kid`` may be a full URI (``did:...#frag``) or a bare fragment
        (``#frag`` / ``frag``); both match against the method ids.
        """
        methods = self.verification_method
        if not methods:
            raise DIDDocumentInvalid(f"{self.id}: no verificationMethod entries")
        if kid is None:
            return methods[0]
        for m in methods:
            if m.id == kid:
                return m
            frag = m.id.split("#", 1)[-1]
            if kid.lstrip("#") == frag and (kid.startswith("#") or "#" not in kid):
                return m
        raise DIDDocumentInvalid(f"{self.id}: no verification method matching {kid!r}")

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)

    @classmethod
    def from_json_dict(cls, data: Any) -> DIDDocument:
        try:
            return cls.model_validate(data)
        except Exception as e:
            raise DIDDocumentInvalid(str(e)) from None


def build_did_document(
    did: str,
    signing_key: PublicKey,
    *,
    fragment: str = "atproto",
    also_known_as: list[str] | None = None,
    rotation_keys: list[str] | None = None,
    pds_endpoint: str | None = None,
    extra_services: list[Service] | None = None,
) -> DIDDocument:
    """Construct a valid atproto-shaped DID document.

    The caller stores and serves the result; FastATP only builds it.
    ``rotationKeys`` / ``alsoKnownAs`` default to empty (v0.1 placeholders,
    forward-compatible fields).
    """
    validate_did(did)
    services: list[Service] = []
    if pds_endpoint is not None:
        services.append(
            Service(
                id="#atproto_pds",
                type="AtprotoPersonalDataServer",
                service_endpoint=pds_endpoint,
            )
        )
    if extra_services:
        services.extend(extra_services)
    return DIDDocument(
        id=did,
        also_known_as=list(also_known_as or []),
        verification_method=[
            VerificationMethod(
                id=f"{did}#{fragment}",
                controller=did,
                public_key_multibase=encode_multikey(signing_key),
            )
        ],
        rotation_keys=list(rotation_keys or []),
        service=services,
    )


def rotation_public_keys(doc: DIDDocument) -> list[PublicKey]:
    """Parse a document's rotationKeys (did:key strings) into public keys."""
    from fastatp.crypto import decode_did_key

    keys = []
    for entry in doc.rotation_keys:
        try:
            keys.append(decode_did_key(entry))
        except Exception as e:
            raise DIDDocumentInvalid(f"{doc.id}: bad rotation key {entry!r}: {e}") from None
    return keys


def canonical_document_bytes(doc: DIDDocument) -> bytes:
    """Canonical DAG-CBOR bytes of a DID document (rotation-signing payload)."""
    from fastatp import dagcbor, datamodel

    return dagcbor.encode(datamodel.from_json(doc.to_json_dict()))


def verify_document_rotation(old_doc: DIDDocument, new_doc: DIDDocument, signature: bytes) -> bool:
    """Check that a replacement document was authorized by the old document.

    The signature is compact low-S ECDSA over the new document's canonical
    DAG-CBOR bytes, and must verify against one of the OLD document's
    rotationKeys. FastATP never writes documents; this is the primitive a
    provisioning service uses before accepting an update.

    Returns False for a signature that does not verify; raises
    ``DIDDocumentInvalid`` if the old document's rotationKeys contain a
    malformed entry — a misconfigured document is an error to surface, not
    a quiet False.
    """
    from fastatp.crypto import verify_signature

    if old_doc.id != new_doc.id:
        return False
    payload = canonical_document_bytes(new_doc)
    return any(verify_signature(key, payload, signature) for key in rotation_public_keys(old_doc))


def signing_key_candidates(doc: DIDDocument) -> list[PublicKey]:
    """Candidate record-signing keys for a document, in preference order.

    The ``#atproto``-fragment method (the atproto signing-key convention)
    comes first; other verification methods follow so multi-method documents
    still verify. Keys listed in ``rotationKeys`` are excluded — rotation
    keys manage the identity itself and must never sign records.
    """
    methods = sorted(
        doc.verification_method,
        key=lambda m: m.id.split("#", 1)[-1] != "atproto",
    )
    keys = []
    for method in methods:
        try:
            key = method.public_key()
        except CryptoError:
            # an unsupported or malformed method must not mask usable ones
            continue
        # compare by CANONICAL did:key of the decoded key, not by string:
        # a rotation key exposed through a legacy 2019 method (uncompressed,
        # unprefixed encoding) must still be excluded — rotation keys manage
        # the identity and must never sign records
        if encode_did_key(key) in doc.rotation_keys:
            continue
        keys.append(key)
    if not keys:
        raise DIDDocumentInvalid(
            f"{doc.id}: document has no usable signing key "
            "(no decodable verification methods, or all are rotation keys)"
        )
    return keys
