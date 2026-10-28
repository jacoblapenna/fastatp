"""The private-channel attestation: a signed pointer to a reachable repo.

An ``Attestation`` is one signed record that is discovery + anti-spoof +
location + lifetime for a WireGuard-reachable repo. It
travels in BOTH directions of the private-response path:

- a **self-attestation** — a requester publishing its own ephemeral ``did:key``
  channel coordinates so a service can admit it by public key; and
- an **engagement/location attestation** — a service publishing an ephemeral
  reply channel bound to a request's ``context_id``.

The trust binding is the record's own commit signature: the attestation is
signed by the poster's **stable** DID, so nobody can forge a channel that
claims to come from a given peer. Records are never encrypted (PHILOSOPHY.md):
privacy is the doorman + reachability the attestation points at, not ciphertext.

It joins ``Capability``/``Revocation`` in the sole shipped app-ish namespace
because it is authorization/transport **infrastructure**, not application
semantics — the developer's request/reply lexicons stay theirs.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastatp.authz.wireguard import (
    WireGuardPeerInfo,
    build_wireguard_uri,
    parse_wireguard_uri,
)
from fastatp.lex.types import Datetime, Did
from fastatp.lexicon import Lexicon

AUTH_NAMESPACE = "dev.fastatp.auth"


class Attestation(Lexicon):
    """A signed pointer to a WireGuard-reachable repo, bound to a context.

    ``endpoint`` is a ``wg://[host]?pubkey=..&ip=..`` URI (the attestation
    reuses the DID-document ``WireGuardFirehose`` URI shape) so
    a single field carries the peer's tunnel IP — the ``/32`` the doorman routes
    on — and its optional endpoint host. ``wg_public_key`` repeats the static
    key at top level for convenience and cross-checking.
    """

    nsid = f"{AUTH_NAMESPACE}.attestation"

    context_id: str
    ephemeral_did: Did
    wg_public_key: str
    endpoint: str  # wg://[host]?pubkey=..&ip=..  (carries the tunnel /32)
    expires_at: Datetime

    def peer_info(self) -> WireGuardPeerInfo:
        """The attested peer's WireGuard identity (key + tunnel ip + host)."""
        ctx = f"attestation {self.context_id!r} endpoint"
        info = parse_wireguard_uri(self.endpoint, context=ctx)
        if info.public_key != self.wg_public_key:
            # the top-level key and the URI key must agree — a mismatch means a
            # tampered or malformed attestation; fail closed
            from fastatp.exceptions import EnforcementError

            raise EnforcementError(
                f"attestation {self.context_id!r}: wg_public_key disagrees with endpoint"
            )
        return info

    def is_expired(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(timezone.utc)
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True  # unparseable expiry fails closed
        return expiry <= moment


def build_attestation(
    *,
    context_id: str,
    ephemeral_did: str,
    wg_public_key: str,
    tunnel_ip: str,
    expires_at: str,
    host: str | None = None,
) -> Attestation:
    """Assemble an :class:`Attestation` with a well-formed ``wg://`` endpoint."""
    return Attestation(
        context_id=context_id,
        ephemeral_did=ephemeral_did,
        wg_public_key=wg_public_key,
        endpoint=build_wireguard_uri(wg_public_key, tunnel_ip, host),
        expires_at=expires_at,
    )
