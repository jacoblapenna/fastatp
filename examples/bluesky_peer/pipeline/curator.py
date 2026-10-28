"""The curator: an INTERNAL agent identity that selects what gets posted.

The curator is the other half of the segmentation rule: a
**twin-only slug DID** (``did:web:<domain>:<slug>`` — self-certifying, no
pretty alias, never announced to a relay) whose repo is hosted GATED on the
same process as the public persona repo. Its output is signed *records* in
its own repo — signed mail, not calls: the poster subscribes and reacts;
nobody invokes anybody.

Authorization is presence: the curator grants the persona a ``subscribe``
capability by committing a Capability record into its OWN repo. Deleting
that record is revocation; there is no token to leak or expire.
"""

from __future__ import annotations

import base64
from typing import Any

from fastatp import crypto
from fastatp.authz.capability import ACTION_SUBSCRIBE, Capability
from fastatp.crypto import encode_did_key
from fastatp.did.continuity import build_genesis
from fastatp.did.document import DIDDocument
from fastatp.repo.repo import Repo
from fastatp.server.peer_server import PeerServer, RepoHandle

SELECTION_NSID = "com.example.curation.selection"


class Curator:
    """Mints its slug identity, hosts its gated repo, publishes selections."""

    def __init__(self, server: PeerServer, domain: str) -> None:
        self.signing_key = crypto.generate_keypair("k256")
        # rotation pen for a demo agent: minted and immediately discarded —
        # an internal agent identity is replaceable (mint a new slug), which
        # is exactly why slugs are unlimited and never network-visible
        rotation = crypto.generate_keypair("k256")
        self.did, self.genesis = build_genesis(
            domain,
            self.signing_key.public_key(),
            [encode_did_key(rotation.public_key())],
            bind_pretty=False,  # twin-only: the slug IS the identity
        )
        repo = Repo(self.did, self.signing_key)
        self.handle: RepoHandle = server.register(repo, public=False)  # gated

    def document(self) -> DIDDocument:
        """The self-certifying genesis document (what a verifier resolves)."""
        return self.genesis

    def grant_subscribe(self, subject_did: str) -> None:
        """Presence IS authorization: commit the capability into OUR repo."""
        cap = Capability(subject=subject_did, actions=[ACTION_SUBSCRIBE])
        self.handle.put_record(Capability.nsid, "persona-poster", cap)

    def publish_selection(self, caption: str, image_png: bytes, alt: str) -> dict[str, Any]:
        """Sign one selection record into the gated repo; returns the record."""
        record: dict[str, Any] = {
            "$type": SELECTION_NSID,
            "caption": caption,
            # data-model bytes in JSON form (unpadded base64) -> dag-cbor
            # byte string on the wire; small demo image rides in the record
            "imagePng": {"$bytes": base64.b64encode(image_png).decode().rstrip("=")},
            "alt": alt,
        }
        event = self.handle.put_record(SELECTION_NSID, f"sel-{self.handle.seq + 1}", record)
        assert event.commit.did == self.did
        return record
