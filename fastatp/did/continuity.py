"""Self-certifying did:web continuity — the twin + chain convention.

did:web trusts whatever a domain serves *now*; a lapsed or hijacked domain lets
the new owner publish fresh keys that look exactly like routine rotation. This
module closes that first-contact hole **without a registry**: an
identity keeps a self-certifying **twin** ``did:web:<domain>:<slug>`` whose slug
commits to the hash of its genesis document, serving an append-only chain of
every document it has had, each page rotation-signed by the previous page's
rotation key. The domain is demoted from deed to shelf: the same chain served
anywhere verifies identically.

Slug recipe (pinned; **did:webvh-inspired, not interoperable** — one
canonicalizer in this codebase, DAG-CBOR):

    slug = base32_lower( sha256( canonical_dag_cbor(genesis WITH the {SCID}
           placeholder in its id) )[:20] )            # 20 bytes -> 32 chars

A document that names itself cannot hash itself, so the genesis is hashed with
a ``{SCID}`` placeholder standing in for the slug (the did:webvh trick), then
the real slug is substituted back. ``verify()`` re-derives the slug the same
way and fails closed on any mismatch. Records never carry any of this — it is
DID-document + resolver discipline only; no wire or Lexicon change.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any

from fastatp.crypto import (
    PrivateKey,
    PublicKey,
    encode_did_key,
    sign_message,
)
from fastatp.did.document import (
    DIDDocument,
    build_did_document,
    canonical_document_bytes,
    verify_document_rotation,
)
from fastatp.exceptions import DIDDocumentInvalid

_SLUG_HASH_BYTES = 20  # 160 bits -> exactly 32 base32 chars, no padding
# The genesis names itself (its id contains the slug), so it is hashed with a
# fixed placeholder in the slug position — the did:webvh {SCID} trick, but a
# real base32-shaped token (32 chars, all valid base32) so it passes did:web
# syntax validation. Astronomically un-collidable with a real sha256 slug.
SCID_PLACEHOLDER = "scid" * 8


def derive_slug(placeholder_doc: DIDDocument) -> str:
    """base32-lower(sha256(canonical DAG-CBOR of the placeholder genesis)[:20]).

    ``placeholder_doc`` MUST carry ``SCID_PLACEHOLDER`` where the slug will go
    (its ``id`` is ``did:web:<domain>:<placeholder>``); everything else is the
    final genesis.
    """
    digest = hashlib.sha256(canonical_document_bytes(placeholder_doc)).digest()
    return base64.b32encode(digest[:_SLUG_HASH_BYTES]).decode("ascii").rstrip("=").lower()


def _pretty_did(domain: str) -> str:
    return f"did:web:{domain}"


def _placeholder_did(domain: str) -> str:
    return f"did:web:{domain}:{SCID_PLACEHOLDER}"


def _twin_did(domain: str, slug: str) -> str:
    return f"did:web:{domain}:{slug}"


def build_genesis(
    domain: str,
    signing_key: PublicKey,
    rotation_keys: list[str],
    *,
    bind_pretty: bool = True,
) -> tuple[str, DIDDocument]:
    """The self-certifying genesis document for ``domain`` and its twin DID.

    ``rotation_keys`` are ``did:key`` strings (the rotation public keys — the
    pen; their private halves stay off the serving box). Returns
    ``(twin_did, genesis_doc)``; the genesis binds ``alsoKnownAs -> pretty``.

    ``bind_pretty=False`` mints a **twin-only** genesis: no pretty
    alias — the slug DID *is* the identity. This is the shelved-identity shape:
    a multi-tenant neutral shelf hosts many identities under one domain, and
    the domain's own root document cannot alias to all of them.
    """
    if not rotation_keys:
        raise DIDDocumentInvalid("a continuity genesis requires at least one rotation key")
    also_known_as = [_pretty_did(domain)] if bind_pretty else []
    placeholder = build_did_document(
        did=_placeholder_did(domain),
        signing_key=signing_key,
        also_known_as=also_known_as,
        rotation_keys=rotation_keys,
    )
    slug = derive_slug(placeholder)
    twin = _twin_did(domain, slug)
    genesis = build_did_document(
        did=twin,
        signing_key=signing_key,
        also_known_as=also_known_as,
        rotation_keys=rotation_keys,
    )
    return twin, genesis


def build_pretty(domain: str, signing_key: PublicKey, twin_did: str) -> DIDDocument:
    """The pretty ``did:web:<domain>`` document: same signing key, ``alsoKnownAs
    -> twin``. Its keys MUST match the chain head (the resolver enforces)."""
    return build_did_document(
        did=_pretty_did(domain),
        signing_key=signing_key,
        also_known_as=[twin_did],
    )


@dataclass
class ChainPage:
    """One document the identity has had, plus the rotation signature that
    authorized it. ``signature`` is ``None`` only for the genesis (page 0),
    which is self-certified by the slug commitment rather than a predecessor."""

    document: DIDDocument
    signature: bytes | None


class ContinuityChain:
    """An append-only, rotation-signed log of an identity's DID documents."""

    def __init__(self, pages: list[ChainPage]) -> None:
        if not pages:
            raise DIDDocumentInvalid("a continuity chain has at least a genesis page")
        self.pages = pages

    # --- accessors ------------------------------------------------------------

    @property
    def genesis(self) -> DIDDocument:
        return self.pages[0].document

    @property
    def head(self) -> DIDDocument:
        return self.pages[-1].document

    @property
    def twin_did(self) -> str:
        return self.genesis.id

    @property
    def pretty_did(self) -> str:
        # the genesis binds alsoKnownAs -> pretty (verify() checks it is present)
        return self.genesis.also_known_as[0] if self.genesis.also_known_as else ""

    @property
    def slug(self) -> str:
        return self.twin_did.rsplit(":", 1)[-1]

    # --- construction ---------------------------------------------------------

    @classmethod
    def create(
        cls,
        domain: str,
        signing_key: PublicKey,
        rotation_keys: list[str],
        *,
        bind_pretty: bool = True,
    ) -> ContinuityChain:
        _twin, genesis = build_genesis(domain, signing_key, rotation_keys, bind_pretty=bind_pretty)
        return cls([ChainPage(genesis, None)])

    def append(self, new_document: DIDDocument, rotation_private_key: PrivateKey) -> ChainPage:
        """Turn the page: sign ``new_document`` with a rotation key of the
        current head and append it. The supplied pen must match a ``rotationKeys``
        entry in the head (wrong pen fails early, before any mutation)."""
        head = self.head
        if new_document.id != self.twin_did:
            raise DIDDocumentInvalid(
                f"a chain page must keep the twin id {self.twin_did!r}, got {new_document.id!r}"
            )
        pen = encode_did_key(rotation_private_key.public_key())
        if pen not in head.rotation_keys:
            raise DIDDocumentInvalid(
                "the supplied rotation key is not a rotationKeys entry of the current head"
            )
        signature = sign_message(rotation_private_key, canonical_document_bytes(new_document))
        page = ChainPage(new_document, signature)
        self.pages.append(page)
        return page

    # --- verification (fails closed) ------------------------------------------

    def verify(self) -> None:
        """Raise ``DIDDocumentInvalid`` on ANY inconsistency (fail closed):
        the slug commits to the genesis, the genesis binds to a pretty DID, and
        every page turn is rotation-signed by its predecessor under a stable
        twin id. A valid chain returns None."""
        genesis = self.genesis
        if self.pages[0].signature is not None:
            raise DIDDocumentInvalid("genesis page must not carry a rotation signature")
        if genesis.also_known_as:
            pretty = genesis.also_known_as[0]
            if not genesis.id.startswith(pretty + ":"):
                raise DIDDocumentInvalid(
                    f"twin id {genesis.id!r} is not a path segment under its pretty DID {pretty!r}"
                )
            domain = pretty[len("did:web:") :]
        else:
            # twin-only: no pretty alias — the slug DID IS the
            # identity (shelved shape). The domain comes from the twin id.
            prefix, _, slug_part = genesis.id.rpartition(":")
            if not prefix.startswith("did:web:") or not slug_part:
                raise DIDDocumentInvalid(
                    f"twin id {genesis.id!r} is not a did:web with a slug segment"
                )
            domain = prefix[len("did:web:") :]
            if not domain:
                raise DIDDocumentInvalid(f"twin id {genesis.id!r} carries no domain")

        # 1. the slug self-certifies the genesis: recompute from the placeholder
        recomputed = derive_slug(
            build_did_document(
                did=_placeholder_did(domain),
                signing_key=genesis.get_verification_method().public_key(),
                also_known_as=list(genesis.also_known_as),
                rotation_keys=list(genesis.rotation_keys),
            )
        )
        if recomputed != self.slug:
            raise DIDDocumentInvalid(
                f"slug {self.slug!r} does not match the genesis hash {recomputed!r} "
                "(forged or tampered genesis)"
            )

        # 2. every subsequent page is rotation-signed by its predecessor, under
        #    an unchanged twin id
        for i in range(1, len(self.pages)):
            prev, page = self.pages[i - 1].document, self.pages[i]
            if page.document.id != genesis.id:
                raise DIDDocumentInvalid(
                    f"chain page {i} changed the twin id to {page.document.id!r}"
                )
            if page.signature is None:
                raise DIDDocumentInvalid(f"chain page {i} is missing its rotation signature")
            if not verify_document_rotation(prev, page.document, page.signature):
                raise DIDDocumentInvalid(
                    f"chain page {i} is not rotation-signed by page {i - 1}'s rotation keys "
                    "(broken chain / unauthorized page)"
                )

    # --- serialization --------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "chain": [
                {
                    "document": page.document.to_json_dict(),
                    "signature": (
                        base64.b64encode(page.signature).decode("ascii")
                        if page.signature is not None
                        else None
                    ),
                }
                for page in self.pages
            ]
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ContinuityChain:
        pages = []
        for entry in data.get("chain", []):
            sig = entry.get("signature")
            pages.append(
                ChainPage(
                    document=DIDDocument.from_json_dict(entry["document"]),
                    signature=base64.b64decode(sig) if sig is not None else None,
                )
            )
        return cls(pages)
