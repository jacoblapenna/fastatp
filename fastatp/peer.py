"""The symmetric Peer: commits signed records, verifies others' proofs.

Every participant — device, cloud service, agent, human session — is a peer
with a DID. Records live in the peer's repo (see ``fastatp.repo``); a record
travels as a **proof slice** (``Repo.prove()``: signed commit + MST path +
record block, as CAR bytes) that any peer can verify with nothing but a DID
resolver. The core is transport-agnostic: proof bytes ride any transport;
``Peer.verify()`` takes them back to a typed record.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastatp.cid import CID
from fastatp.crypto import PrivateKey, decode_did_key
from fastatp.did.document import DIDDocument
from fastatp.did.resolver import DIDResolver, chain, web_resolver
from fastatp.exceptions import (
    DIDNotFound,
    LexiconValidationError,
    NotAuthorized,
    RepoError,
    SignatureInvalid,
    SignerMismatch,
)
from fastatp.lexicon import Lexicon, parse_record

__all__ = ["AuthorizeHook", "Peer", "VerifiedRecord", "chain"]


@dataclass(frozen=True)
class VerifiedRecord:
    """A fully verified record plus the metadata computed during verification.

    Returned by :meth:`Peer.verify_detailed` so callers get the issuer, the
    record CID, and its ``collection/rkey`` path without re-decoding the proof.
    """

    record: Lexicon
    issuer_did: str
    record_cid: CID
    path: str


AuthorizeHook = Callable[[str, str, Any], Any]
"""``authorize(issuer_did, nsid, context) -> bool`` (sync or async).

The reserved authorization seam: run after signature and schema checks,
defaulting to allow-all. Deployments on a private network (tailnet ACLs)
never need to touch it; enterprise policy drops in without a breaking
change. The v0.4 model is signed delegation (capability) records under the
reserved ``*.auth.capability`` lexicon namespace."""

CAPABILITY_NSID_SUFFIX = "auth.capability"
"""Reserved lexicon name suffix for v0.4 signed-delegation (capability)
records, e.g. ``com.yourorg.auth.capability``."""


class Peer:
    """Symmetric participant: a DID, optionally a signing key, a resolver.

    ``did`` + ``signing_key`` let the peer own repos (``PeerServer`` hosts
    them); ``resolver`` (default: ``web_resolver``) lets it verify other
    peers' records. Either side can be omitted.
    """

    def __init__(
        self,
        did: str | None = None,
        signing_key: PrivateKey | None = None,
        *,
        resolver: DIDResolver | None = None,
        authorize: AuthorizeHook | None = None,
    ) -> None:
        self.did = did
        self.signing_key = signing_key
        self.resolver: DIDResolver = resolver if resolver is not None else web_resolver
        self.authorize = authorize

    # --- producing ------------------------------------------------------------

    def create_repo(self, store: Any = None) -> Any:
        """A signed present-state repo for this peer's DID."""
        from fastatp.repo.repo import Repo

        if self.did is None or self.signing_key is None:
            raise ValueError("a repo requires this Peer to have a did and a signing_key")
        return Repo(self.did, self.signing_key, store=store)

    # --- verification ---------------------------------------------------------

    async def verify(
        self,
        proof: bytes,
        *,
        expected_signer: str | None = None,
        path: str | None = None,
        context: Any = None,
    ) -> Lexicon:
        """Verify a record proof and return the typed Lexicon instance.

        Flow: parse the proof CAR -> commit ``did`` (issuer) -> resolve DID
        document -> verify commit signature (rotation keys rejected) ->
        re-derive the record's membership in the signed MST root -> parse the
        record by ``$type`` -> run the ``authorize()`` hook.

        A proof normally witnesses exactly one record; pass ``path``
        (``collection/rkey``) to select one from a multi-record slice.

        Raises ``VerificationError`` subclasses (SignatureInvalid,
        SignerMismatch, NotAuthorized, UnknownLexicon) for anything wrong
        with the proof or the resolved document. Resolution failures
        propagate as themselves so callers can retry: ``DIDNotFound``
        (miss) and ``DIDResolutionTimeout`` (transient network failure —
        deliberately NOT wrapped as a verification failure).
        """
        return (
            await self.verify_detailed(
                proof, expected_signer=expected_signer, path=path, context=context
            )
        ).record

    async def verify_detailed(
        self,
        proof: bytes,
        *,
        expected_signer: str | None = None,
        path: str | None = None,
        context: Any = None,
    ) -> VerifiedRecord:
        """Like :meth:`verify`, but also returns the issuer DID, the record's
        CID, and its ``collection/rkey`` path — all computed in the single CAR
        decode ``verify`` already does. Callers that need this metadata (e.g.
        push intake building an Envelope) use this to avoid a redundant decode.
        """
        from fastatp.exceptions import DataModelError
        from fastatp.repo.car import decode_car
        from fastatp.repo.commit import Commit
        from fastatp.repo.mst import MST
        from fastatp.repo.store import MemoryBlockStore

        try:
            roots, blocks = decode_car(proof)
        except (RepoError, DataModelError, ValueError) as e:
            raise SignatureInvalid(f"malformed proof: {e}") from None
        if len(roots) != 1 or roots[0] not in blocks:
            raise SignatureInvalid("proof must be rooted at its commit block")
        try:
            commit = Commit.from_dag_cbor(blocks[roots[0]])
        except (RepoError, DataModelError) as e:
            raise SignatureInvalid(f"malformed commit: {e}") from None

        issuer = commit.did
        if expected_signer is not None and issuer != expected_signer:
            raise SignerMismatch(f"expected signer {expected_signer!r}, proof signed by {issuer!r}")
        # the commit carries no key hint, so try the document's candidate
        # signing keys (#atproto-fragment method first) until one verifies
        candidate_keys = await self._resolve_keys(issuer)
        if not any(commit.verify(key) for key in candidate_keys):
            raise SignatureInvalid(f"{issuer}: commit signature does not verify")

        store = MemoryBlockStore()
        for cid, block in blocks.items():
            store.put(cid, block)
        mst = MST(store)

        resolved_path: str
        if path is not None:
            try:
                record_cid = mst.get(commit.data, path)
            except (RepoError, DataModelError):
                record_cid = None  # lookup left the proof's covered/valid path
            if record_cid is None:
                raise SignatureInvalid(f"proof does not witness a record at {path!r}")
            resolved_path = path
        else:
            # leaf values are record CIDs by construction (an MST node's CID
            # can never appear as a leaf value), so the witnessed values whose
            # blocks travelled with the proof are exactly its records
            try:
                witnessed = mst.load_partial(commit.data)
            except (RepoError, DataModelError) as e:
                raise SignatureInvalid(f"malformed MST blocks in proof: {e}") from None
            candidates = [(p, cid) for p, cid in witnessed.items() if cid in blocks]
            if len(candidates) != 1:
                raise SignatureInvalid(
                    f"proof witnesses {len(candidates)} records; pass path= to select one"
                )
            resolved_path, record_cid = candidates[0]

        record_block = store.get(record_cid)
        if record_block is None:
            raise SignatureInvalid("proof is missing the record block")

        from fastatp import dagcbor, datamodel

        try:
            record_json = datamodel.to_json(dagcbor.decode(record_block))
        except DataModelError as e:
            raise SignatureInvalid(f"record block is not valid DAG-CBOR: {e}") from None
        try:
            record = parse_record(record_json)
        except LexiconValidationError as e:
            # structurally-not-a-record blocks in crafted proofs land here;
            # UnknownLexicon (an unregistered $type) stays its own signal
            raise SignatureInvalid(f"proof does not carry a valid record: {e}") from None

        if self.authorize is not None:
            allowed = self.authorize(issuer, type(record).nsid, context)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not allowed:
                raise NotAuthorized(f"{issuer} is not authorized to send {type(record).nsid}")
        return VerifiedRecord(
            record=record, issuer_did=issuer, record_cid=record_cid, path=resolved_path
        )

    async def _resolve_keys(self, issuer: str) -> list[Any]:
        """Candidate record-signing keys for a DID, in preference order.

        The ``#atproto``-fragment method (the atproto signing-key convention)
        is tried first; other verification methods follow so multi-method
        documents still verify. Keys listed in ``rotationKeys`` are excluded:
        rotation keys manage the identity itself and must never sign records.
        """
        from fastatp.did.document import signing_key_candidates
        from fastatp.exceptions import CryptoError, DIDDocumentInvalid

        try:
            if issuer.startswith("did:key:"):
                return [decode_did_key(issuer)]
            doc = await self.resolver(issuer)
            if doc is None:
                raise DIDNotFound(f"resolver returned no document for {issuer}")
            if not isinstance(doc, DIDDocument):
                doc = DIDDocument.from_json_dict(doc)
            if doc.id != issuer:
                raise SignatureInvalid(
                    f"resolved document id {doc.id!r} does not match issuer {issuer!r}"
                )
            return signing_key_candidates(doc)
        except (DIDDocumentInvalid, CryptoError) as e:
            # exception contract: verify() raises VerificationError subclasses
            # for anything wrong with the proof or the document, and DIDNotFound
            # only for a resolution miss
            raise SignatureInvalid(f"{issuer}: unusable DID document: {e}") from None
