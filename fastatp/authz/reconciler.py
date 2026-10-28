"""The reconciler: keeps the WireGuard peer list equal to the live grants.

This is the decentralized replacement for a coordination server, running
locally on every peer. The capability records in the peer's own repo are the
source of truth (the authorization plane); the WireGuard interface follows
(the enforcement plane). Records always move first, WireGuard second, so a
crash between the two steps always heals in the safe direction at the next
``reconcile()``:

- grant crash: capability exists, peer missing  -> reconcile adds the peer
- revoke crash: capability gone, peer lingering -> reconcile removes it

A revoked peer can never be resurrected, because revocation deletes the
record before touching the interface.

The reconciler assumes it OWNS its interface (run it on a dedicated one,
e.g. ``wg-fastatp``): any peer on the interface that no live capability
explains is removed. ``pinned_pubkeys`` exempts keys the reconciler must
never touch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from fastatp.authz.capability import (
    ACTION_SUBSCRIBE,
    Capability,
    Revocation,
    live_capabilities,
    now_iso,
)
from fastatp.authz.wireguard import (
    WireGuardInterface,
    WireGuardPeerInfo,
    parse_wireguard_service,
    validate_wireguard_key,
)
from fastatp.did.resolver import DIDResolver
from fastatp.exceptions import AuthzError
from fastatp.lex.types import validate_did
from fastatp.repo.repo import CommitEvent, RecordLike, WriteOp
from fastatp.tid import generate_tid


class RepoWriter(Protocol):
    """The write surface shared by ``Repo`` and ``PeerServer``.

    Commit grants through a ``PeerServer`` and the capability lifecycle
    broadcasts on the firehose — the audit trail replays to every subscriber.
    """

    def put_record(self, collection: str, rkey: str, record: RecordLike) -> CommitEvent: ...

    def delete_record(self, collection: str, rkey: str) -> CommitEvent: ...

    def apply_writes(self, writes: list[WriteOp]) -> CommitEvent: ...


def _repo_of(writer: RepoWriter) -> object:
    return getattr(writer, "repo", writer)  # PeerServer wraps; Repo is itself


def _ips_key(allowed_ips: str | None) -> frozenset[str]:
    """Order-insensitive comparison key for a WireGuard allowed-ips value
    (comma- or space-separated), so equivalent lists don't churn add_peer."""
    if not allowed_ips:
        return frozenset()
    return frozenset(part for part in allowed_ips.replace(",", " ").split() if part)


@dataclass
class ReconcileReport:
    added: list[str] = field(default_factory=list)  # DIDs (or pubkeys for unknowns)
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)  # did -> reason

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.expired)


class WireGuardReconciler:
    """grant / revoke / reconcile: capabilities in, kernel enforcement out."""

    def __init__(
        self,
        writer: RepoWriter,
        interface: WireGuardInterface,
        resolver: DIDResolver,
        *,
        pinned_pubkeys: set[str] | None = None,
    ) -> None:
        self.writer = writer
        self.interface = interface
        self.resolver = resolver
        self.pinned_pubkeys = set(pinned_pubkeys or ())

    # --- resolution -----------------------------------------------------------

    async def _peer_info(self, did: str) -> WireGuardPeerInfo:
        doc = await self.resolver(did)
        if doc is None:
            raise AuthzError(f"cannot grant: no DID document for {did}")
        return parse_wireguard_service(doc)

    @staticmethod
    def _peer_info_from_cap(cap: Capability) -> WireGuardPeerInfo | None:
        """The WG identity embedded in a by-public-key capability, if any."""
        if cap.wg_public_key and cap.wg_tunnel_ip:
            return WireGuardPeerInfo(public_key=cap.wg_public_key, tunnel_ip=cap.wg_tunnel_ip)
        return None

    # --- grant ------------------------------------------------------------------

    async def grant(
        self,
        did: str,
        *,
        actions: tuple[str, ...] = (ACTION_SUBSCRIBE,),
        collections: list[str] | None = None,
        expires_at: str | None = None,
        note: str | None = None,
        wg_public_key: str | None = None,
        tunnel_ip: str | None = None,
    ) -> tuple[str, Capability]:
        """Authorize ``did``: sign a capability, then open the tunnel.

        Two admission sources for the grantee's WG identity:

        - **by DID document** (default): resolve ``did`` and read its
          ``WireGuardFirehose`` service — for ``did:web`` grantees. Strict at
          grant time (an unresolvable grantee is an error); re-resolved on each
          reconcile so key rotation propagates.
        - **by public key**: pass ``wg_public_key`` + ``tunnel_ip`` directly —
          for documentless ``did:key`` grantees whose key rides in the request
          / attestation. The identity is embedded in the capability
          so ``reconcile()`` enforces it without resolution.

        An existing grant for the subject is superseded in the same commit
        (old record deleted + revocation reason="superseded"). Returns
        ``(rkey, capability)``.
        """
        validate_did(did)
        if wg_public_key is not None:
            if not did.startswith("did:key:"):
                # by-public-key is for documentless subjects only. A did:web
                # grantee has a document; admitting it by an embedded key would
                # skip resolution forever and freeze its key rotation.
                raise AuthzError(
                    "grant by public key is only for documentless did:key subjects; "
                    "a document-backed DID (e.g. did:web) is admitted by resolving it"
                )
            validate_wireguard_key(wg_public_key)
            if tunnel_ip is None:
                raise AuthzError("grant by public key requires tunnel_ip")
            try:
                info = WireGuardPeerInfo(public_key=wg_public_key, tunnel_ip=tunnel_ip)
                _ = info.allowed_ips  # validate the tunnel ip BEFORE committing
            except ValueError as e:
                raise AuthzError(f"invalid tunnel_ip {tunnel_ip!r}: {e}") from None
        else:
            info = await self._peer_info(did)  # resolve BEFORE committing anything

        capability = Capability(
            subject=did,
            actions=list(actions),
            collections=collections,
            expires_at=expires_at,
            note=note,
            wg_public_key=wg_public_key,
            wg_tunnel_ip=tunnel_ip if wg_public_key is not None else None,
        )
        rkey = generate_tid()
        writes: list[WriteOp] = []
        existing = live_capabilities(_repo_of(self.writer)).get(did)
        if existing is not None:
            old_rkey, old_cap = existing
            writes.append(WriteOp("delete", Capability.nsid, old_rkey))
            writes.append(
                WriteOp(
                    "create",
                    Revocation.nsid,
                    generate_tid(),
                    Revocation(
                        subject=did,
                        capability_rkey=old_rkey,
                        capability_cid=old_cap.cid().encode(),
                        revoked_at=now_iso(),
                        reason="superseded",
                    ),
                )
            )
        writes.append(WriteOp("create", Capability.nsid, rkey, capability))
        self.writer.apply_writes(writes)  # record first: the source of truth

        self.interface.add_peer(info.public_key, info.allowed_ips)  # then the teeth
        return rkey, capability

    # --- revoke ------------------------------------------------------------------

    async def revoke(self, did: str, *, reason: str = "revoked") -> Revocation | None:
        """Withdraw authorization: kill the record, then the tunnel.

        Returns the revocation record, or None if no live grant existed.
        Record deletion happens before peer removal so a crash in between
        can only leave a peer that the next reconcile() removes — never a
        revoked peer that could be resurrected.
        """
        existing = live_capabilities(_repo_of(self.writer)).get(did)
        if existing is None:
            return None
        rkey, capability = existing
        revocation = Revocation(
            subject=did,
            capability_rkey=rkey,
            capability_cid=capability.cid().encode(),
            revoked_at=now_iso(),
            reason=reason,
        )
        self.writer.apply_writes(
            [
                WriteOp("delete", Capability.nsid, rkey),
                WriteOp("create", Revocation.nsid, generate_tid(), revocation),
            ]
        )
        await self._remove_subject_peer(did, capability)
        return revocation

    async def _remove_subject_peer(self, did: str, cap: Capability | None = None) -> None:
        info = self._peer_info_from_cap(cap) if cap is not None else None
        if info is None:
            try:
                info = await self._peer_info(did)  # did:web: resolve to find the key
            except Exception:
                # cannot resolve the subject anymore: prune any interface peer no
                # live capability explains. This is the enforcement diff ONLY (no
                # expiry sweep, no record writes) so it is not re-entrant with the
                # in-flight revoke that already committed the deletion record.
                await self._apply_desired(ReconcileReport())
                return
        if info.public_key not in self.pinned_pubkeys:
            self.interface.remove_peer(info.public_key)

    # --- reconcile -----------------------------------------------------------------

    async def reconcile(self, now: datetime | None = None) -> ReconcileReport:
        """Make the interface peer set equal the live capability set.

        1. Expiry sweep: expired capabilities become lifecycle revocations
           (record delete + revocation reason="expired").
        2/3. Resolve each live subject and diff the interface: add missing
           peers, remove peers no capability explains (except pinned). A
           subject that fails to RESOLVE keeps its existing peer and is
           reported in ``failed`` — a DNS blip must not drop connectivity;
           grant-time resolution is the strict gate.
        """
        report = ReconcileReport()
        await self._sweep_expired(report, now or datetime.now(timezone.utc))
        await self._apply_desired(report, now)
        return report

    async def _sweep_expired(self, report: ReconcileReport, moment: datetime) -> None:
        repo = _repo_of(self.writer)
        expired_writes: list[WriteOp] = []
        expired_subjects: list[str] = []
        for rkey, _cid in list(repo.list_records(Capability.nsid)):  # type: ignore[attr-defined]
            data = repo.get_record(Capability.nsid, rkey)  # type: ignore[attr-defined]
            if data is None:
                continue
            cap = Capability.from_json_dict(data)
            if cap.is_expired(moment):
                expired_writes.append(WriteOp("delete", Capability.nsid, rkey))
                expired_writes.append(
                    WriteOp(
                        "create",
                        Revocation.nsid,
                        generate_tid(),
                        Revocation(
                            subject=cap.subject,
                            capability_rkey=rkey,
                            capability_cid=cap.cid().encode(),
                            revoked_at=now_iso(),
                            reason="expired",
                        ),
                    )
                )
                expired_subjects.append(cap.subject)
        if expired_writes:
            # atomic commit: if it raises, the exception aborts reconcile
            # BEFORE the desired-set diff, so still-present expired records
            # can never be re-opened as tunnels. report.expired reflects only
            # a committed sweep.
            self.writer.apply_writes(expired_writes)
            report.expired.extend(expired_subjects)

    async def _apply_desired(self, report: ReconcileReport, now: datetime | None = None) -> None:
        repo = _repo_of(self.writer)
        moment = now or datetime.now(timezone.utc)

        desired: dict[str, str] = {}  # pubkey -> allowed_ips
        pubkey_to_did: dict[str, str] = {}
        unresolved_subjects: list[str] = []
        for subject, (_rkey, cap) in live_capabilities(repo, moment).items():
            try:
                # embedded WG info (by-public-key / did:key) wins; else resolve
                info = self._peer_info_from_cap(cap) or await self._peer_info(subject)
            except Exception as e:  # resolution failure keeps existing peers
                report.failed[subject] = str(e)
                unresolved_subjects.append(subject)
                continue
            desired[info.public_key] = info.allowed_ips
            pubkey_to_did[info.public_key] = subject

        current = self.interface.peers()
        for pubkey, allowed_ips in desired.items():
            if _ips_key(current.get(pubkey)) != _ips_key(allowed_ips):
                self.interface.add_peer(pubkey, allowed_ips)
                report.added.append(pubkey_to_did[pubkey])
            else:
                report.kept.append(pubkey_to_did[pubkey])
        removable = set(current) - set(desired) - self.pinned_pubkeys
        if unresolved_subjects:
            # cannot prove these interface peers aren't the unresolved
            # subjects' keys; keep everything rather than cut a live grantee
            for pubkey in removable:
                report.failed.setdefault(f"peer:{pubkey}", "kept: unresolved subjects present")
        else:
            for pubkey in removable:
                self.interface.remove_peer(pubkey)
                report.removed.append(pubkey)

    async def run(self, interval: float = 30.0) -> None:
        """Reconcile forever (expiry + drift healing). Cancel to stop."""
        while True:
            await self.reconcile()
            await asyncio.sleep(interval)
