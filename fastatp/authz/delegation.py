"""Presence-based pen delegation: absence is revocation.

A :class:`Delegation` says "I vouch for this pen." A pen is valid **iff an
unbroken chain of delegation records is present right now**, walked from the
pen back to the durable identity's root, each link a record currently in its
delegator's repo. Revoking = deleting the record: the repo is a present-state
MST, so a membership proof for a deleted record cannot be constructed against
the current signed root, and deleting any link severs the whole subtree below
it. Comms failure is authn failure — every unresolvable step fails closed.

There is deliberately NO freshness or traveling-proof machinery here. The only
verification question is "can I see the delegation record in the delegator's
repo right now?", and who may ask is decided by repo visibility (public repo →
anyone; gated repo → admitted parties; no access → fail closed). An app that
wants stranger-verifiable pens composes it with a public delegation repo.

This is the doorman authorization pattern extended to pens: the doorman
consults *currently present* capabilities (``live_capabilities``), and
``Revocation`` is audit trail, never enforcement.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from fastatp.authz.capability import AUTH_NAMESPACE, rkey_sort_key
from fastatp.lex.types import Datetime, Did
from fastatp.lexicon import Lexicon


class Delegation(Lexicon):
    """ "I vouch for this pen": ``issuer`` delegates to ``subject``.

    ``subject`` is the delegated pen or sub-DID — typically a ``did:key``
    (the pen's Multikey rides inside the DID). ``issuer`` must equal the DID
    of the repo the record lives in; the chain walk ignores records whose
    issuer disagrees with their shelf (a repo cannot vouch in someone else's
    name). ``scope`` is reserved (unused in v1). ``expires_at`` is
    belt-and-suspenders: an expired-but-present record is treated as absent —
    deletion, not expiry, is the revocation mechanism.
    """

    nsid = f"{AUTH_NAMESPACE}.delegation"

    subject: Did
    issuer: Did
    expires_at: Datetime | None = None
    scope: str | None = None  # reserved for future narrowing; ignored in v1
    note: str | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        moment = now or datetime.now(timezone.utc)
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True  # unparseable expiry fails closed
        return expiry <= moment


RepoLike = object
"""Anything with ``list_records(collection)`` and ``get_record(collection,
rkey)`` — ``Repo``, ``RepoMirror``, and ``PeerServer.repo`` all qualify."""

RepoSource = Callable[[str], "RepoLike | None | Awaitable[RepoLike | None]"]
"""``repo_source(did) -> repo_like | None`` (sync or async): read access to a
DID's repo, or ``None`` when it cannot be reached or the caller is not allowed
to see it. ``None`` always fails closed — the walk makes no distinction between
"offline", "refused", and "no such repo". Injectable: a host passes its hosted
repos; a counterparty passes whatever repos it can actually read."""


@dataclass
class ChainLink:
    """One presence-checked link: ``issuer`` currently vouches for
    ``delegation.subject`` via the record at ``rkey`` in issuer's repo."""

    issuer: str
    rkey: str
    delegation: Delegation


def live_delegations(
    repo_like: RepoLike, now: datetime | None = None, *, issuer: str | None = None
) -> dict[str, tuple[str, Delegation]]:
    """Newest live delegation per subject: ``{subject: (rkey, Delegation)}``.

    Mirrors ``live_capabilities``: expired records are treated as absent, and
    when several unexpired delegations exist for one subject the newest rkey
    (TIDs sort chronologically) wins. With ``issuer`` given, records naming a
    different issuer are dropped BEFORE supersession — a malformed newer
    record in someone else's name must not shadow an older valid one.
    """
    newest: dict[str, tuple[str, Delegation]] = {}
    for rkey, _cid in repo_like.list_records(Delegation.nsid):  # type: ignore[attr-defined]
        data = repo_like.get_record(Delegation.nsid, rkey)  # type: ignore[attr-defined]
        if data is None:
            continue
        delegation = Delegation.from_json_dict(data)
        if delegation.is_expired(now):
            continue
        if issuer is not None and delegation.issuer != issuer:
            continue  # a repo cannot vouch in someone else's name
        current = newest.get(delegation.subject)
        if current is None or rkey_sort_key(rkey) > rkey_sort_key(current[0]):
            newest[delegation.subject] = (rkey, delegation)
    return newest


async def walk_delegation_chain(
    pen: str,
    root: str,
    repo_source: RepoSource,
    *,
    now: datetime | None = None,
) -> list[ChainLink] | None:
    """The presence walk: is ``pen`` currently vouched for by ``root``?

    Returns the chain of links root → … → pen (``[]`` when ``pen`` *is*
    ``root`` — presenting the root's own key is not a delegation question), or
    ``None`` when no live chain exists. ``None`` is the only failure mode:
    absent record, expired record, unreachable repo, and refused access are
    indistinguishable by design — all fail closed.

    Depth is unbounded; a visited set makes cycles terminate and
    bounds the walk by the number of distinct delegator DIDs.
    """
    if pen == root:
        return []
    visited: set[str] = set()

    async def _walk(issuer: str) -> list[ChainLink] | None:
        if issuer in visited:
            return None
        visited.add(issuer)
        repo = repo_source(issuer)
        if inspect.isawaitable(repo):
            repo = await repo
        if repo is None:
            return None  # unreachable / refused / unknown: fail closed
        live = live_delegations(repo, now, issuer=issuer)
        direct = live.get(pen)
        if direct is not None:
            return [ChainLink(issuer, direct[0], direct[1])]
        for subject, (rkey, delegation) in live.items():
            below = await _walk(subject)
            if below is not None:
                return [ChainLink(issuer, rkey, delegation), *below]
        return None

    return await _walk(root)
