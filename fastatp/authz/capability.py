"""The authorization plane: signed capability and revocation records.

A capability answers "why is this DID allowed" — signed by the granting
peer, committed to its repo, broadcast on its firehose, verifiable and
replayable by anyone. It is provenance, not enforcement; the teeth live in
``fastatp.authz.wireguard``.

Present-state lifecycle: a capability is **live** while its record exists
and is unexpired. Deleting the record is the revocation act; a
``Revocation`` record is written alongside as the audit trail that survives
deletion (present-state repos keep no history, so revocations are how "why
was this removed" stays answerable).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastatp.lex.types import Cid, Datetime, Did, Nsid, RecordKey
from fastatp.lexicon import Lexicon
from fastatp.tid import decode_tid

AUTH_NAMESPACE = "dev.fastatp.auth"
"""Namespace for the shipped capability lexicons.
Organizations may define their own conforming ``*.auth.capability`` lexicons;
these are FastATP's canonical ones."""

ACTION_SUBSCRIBE = "subscribe"
ACTION_SEND = "send"


class Capability(Lexicon):
    """A signed grant: ``subject`` may perform ``actions`` against this peer."""

    nsid = f"{AUTH_NAMESPACE}.capability"

    subject: Did
    actions: list[str]  # knownValues: subscribe, send (advisory, per Lexicon)
    collections: list[Nsid] | None = None  # None = the whole firehose
    expires_at: Datetime | None = None
    note: str | None = None
    # the grantee's WireGuard key. The doorman derives the grantee's overlay
    # address from it, so a record without it admits nobody over a tunnel.
    # Given by hand for a documentless did:key grantee; read from the DID
    # document for a did:web grantee, once, at grant time. A grantee that
    # rotates its key is a stranger until granted again: the record is what
    # the grantor signed, and nothing follows a document on its own. The
    # overlay address is never recorded: it is derived from the key. Optional
    # so a capability that is not about a tunnel at all (a browser-gate
    # grant) can omit it; a tunnel record without it (written by 0.2 for a
    # did:web grantee) still parses, and admits nobody until granted again.
    wg_public_key: str | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        moment = now or datetime.now(UTC)
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True  # unparseable expiry fails closed
        return expiry <= moment


class Revocation(Lexicon):
    """The audit record left behind when a capability ends.

    ``reason`` is free text; the reconciler uses "revoked", "expired", and
    "superseded" for its own lifecycle events.
    """

    nsid = f"{AUTH_NAMESPACE}.revocation"

    subject: Did
    capability_rkey: RecordKey
    capability_cid: Cid
    revoked_at: Datetime
    reason: str | None = None


def live_capabilities(
    repo_like: object, now: datetime | None = None
) -> dict[str, tuple[str, Capability]]:
    """Newest live capability per subject: ``{subject: (rkey, Capability)}``.

    ``repo_like`` needs ``list_records(collection)`` and
    ``get_record(collection, rkey)`` (both ``Repo`` and ``PeerServer.repo``
    qualify). Supersession: when several unexpired capabilities exist for one
    subject, the newest rkey (TIDs sort chronologically) wins.
    """
    newest: dict[str, tuple[str, Capability]] = {}
    for rkey, _cid in repo_like.list_records(Capability.nsid):  # type: ignore[attr-defined]
        data = repo_like.get_record(Capability.nsid, rkey)  # type: ignore[attr-defined]
        if data is None:
            continue
        cap = Capability.from_json_dict(data)
        if cap.is_expired(now):
            continue
        current = newest.get(cap.subject)
        if current is None or rkey_sort_key(rkey) > rkey_sort_key(current[0]):
            newest[cap.subject] = (rkey, cap)
    return newest


def rkey_sort_key(rkey: str) -> tuple[int, str]:
    """TIDs sort by timestamp; non-TID rkeys fall back to string order.

    Shared supersession order for record lifecycles (capabilities,
    delegations): newest rkey wins."""
    try:
        timestamp_us, _clock = decode_tid(rkey)
        return (timestamp_us, rkey)
    except ValueError:
        return (0, rkey)


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
