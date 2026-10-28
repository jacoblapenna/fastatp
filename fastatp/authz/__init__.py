"""v0.4 authorization: capability records + WireGuard enforcement.

Two planes, kept separate: a signed :class:`Capability` answers "why is this
DID allowed" (auditable, replayable, atproto-native); the live WireGuard
peer list answers "can this DID's packets reach me right now" (absence =
cryptographically unreachable). :class:`WireGuardReconciler` keeps the
second equal to the first.
"""

from fastatp.authz.capability import (
    ACTION_SEND,
    ACTION_SUBSCRIBE,
    AUTH_NAMESPACE,
    Capability,
    Revocation,
    live_capabilities,
)
from fastatp.authz.delegation import (
    ChainLink,
    Delegation,
    RepoSource,
    live_delegations,
    walk_delegation_chain,
)
from fastatp.authz.reconciler import ReconcileReport, WireGuardReconciler
from fastatp.authz.wireguard import (
    WIREGUARD_SERVICE_TYPE,
    MemoryWireGuardInterface,
    WgToolInterface,
    WireGuardInterface,
    WireGuardPeerInfo,
    build_wireguard_service,
    parse_wireguard_service,
)

__all__ = [
    "ACTION_SEND",
    "ACTION_SUBSCRIBE",
    "AUTH_NAMESPACE",
    "WIREGUARD_SERVICE_TYPE",
    "Capability",
    "ChainLink",
    "Delegation",
    "MemoryWireGuardInterface",
    "ReconcileReport",
    "RepoSource",
    "Revocation",
    "WgToolInterface",
    "WireGuardInterface",
    "WireGuardPeerInfo",
    "WireGuardReconciler",
    "build_wireguard_service",
    "live_capabilities",
    "live_delegations",
    "parse_wireguard_service",
    "walk_delegation_chain",
]
