"""Presence-based pen delegation.

A pen is valid iff an unbroken chain of delegation records is present right
now; deleting any link severs the subtree below it; every unresolvable step
fails closed; visibility is repo visibility.
"""

from datetime import datetime, timedelta, timezone

import pytest

from fastatp import crypto
from fastatp.authz.delegation import (
    Delegation,
    live_delegations,
    walk_delegation_chain,
)
from fastatp.repo.repo import Repo
from fastatp.tid import generate_tid

ROOT = "did:web:user.shelf.example:aaaabbbbccccddddeeeeffffgggghhhh"


def _did_key() -> str:
    return crypto.encode_did_key(crypto.generate_keypair("p256").public_key())


def _repo(did: str) -> Repo:
    return Repo(did, crypto.generate_keypair("k256"))


def _delegate(repo: Repo, issuer: str, subject: str, **kwargs) -> str:
    rkey = generate_tid()
    repo.put_record(Delegation.nsid, rkey, Delegation(issuer=issuer, subject=subject, **kwargs))
    return rkey


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# --- the lexicon --------------------------------------------------------------


def test_delegation_exports_a_valid_lexicon_doc():
    doc = Delegation.to_lexicon_json()
    assert doc["lexicon"] == 1
    assert doc["id"] == "dev.fastatp.auth.delegation"


# --- presence walk ------------------------------------------------------------


async def test_direct_delegation_verifies():
    pen = _did_key()
    root_repo = _repo(ROOT)
    _delegate(root_repo, ROOT, pen)
    chain = await walk_delegation_chain(pen, ROOT, {ROOT: root_repo}.get)
    assert chain is not None
    assert [link.issuer for link in chain] == [ROOT]
    assert chain[0].delegation.subject == pen


async def test_deep_chain_through_sub_did():
    pen1, pen2 = _did_key(), _did_key()
    root_repo, pen1_repo = _repo(ROOT), _repo(pen1)
    _delegate(root_repo, ROOT, pen1)
    _delegate(pen1_repo, pen1, pen2)  # lateral: an already-delegated pen vouches
    source = {ROOT: root_repo, pen1: pen1_repo}.get
    chain = await walk_delegation_chain(pen2, ROOT, source)
    assert chain is not None
    assert [link.issuer for link in chain] == [ROOT, pen1]


async def test_pen_that_is_the_root_needs_no_chain():
    assert await walk_delegation_chain(ROOT, ROOT, lambda did: None) == []


async def test_absent_delegation_is_refused():
    root_repo = _repo(ROOT)
    assert await walk_delegation_chain(_did_key(), ROOT, {ROOT: root_repo}.get) is None


# --- absence is revocation ----------------------------------------------------


async def test_deleting_a_link_severs_the_subtree():
    """Severing pen1 at the root refuses pen1 AND pen2 (delegated by pen1),
    with pen2's own record untouched — the 'log out everywhere else' shape."""
    pen1, pen2 = _did_key(), _did_key()
    root_repo, pen1_repo = _repo(ROOT), _repo(pen1)
    rkey = _delegate(root_repo, ROOT, pen1)
    _delegate(pen1_repo, pen1, pen2)
    source = {ROOT: root_repo, pen1: pen1_repo}.get
    assert await walk_delegation_chain(pen2, ROOT, source) is not None

    root_repo.delete_record(Delegation.nsid, rkey)  # the revocation act
    assert await walk_delegation_chain(pen1, ROOT, source) is None
    assert await walk_delegation_chain(pen2, ROOT, source) is None


async def test_expired_but_present_is_absent():
    pen = _did_key()
    root_repo = _repo(ROOT)
    _delegate(
        root_repo,
        ROOT,
        pen,
        expires_at=_iso(datetime.now(timezone.utc) - timedelta(minutes=1)),
    )
    assert await walk_delegation_chain(pen, ROOT, {ROOT: root_repo}.get) is None
    assert live_delegations(root_repo) == {}


async def test_supersession_newest_rkey_wins():
    pen = _did_key()
    root_repo = _repo(ROOT)
    _delegate(root_repo, ROOT, pen, note="old")
    _delegate(root_repo, ROOT, pen, note="new")
    (_rkey, live) = live_delegations(root_repo)[pen]
    assert live.note == "new"


# --- fail closed ----------------------------------------------------------------


async def test_unresolvable_delegator_fails_closed():
    """pen1's repo is unreachable: pen2's chain cannot be walked — comms
    failure is authn failure."""
    pen1, pen2 = _did_key(), _did_key()
    root_repo = _repo(ROOT)
    _delegate(root_repo, ROOT, pen1)
    assert await walk_delegation_chain(pen2, ROOT, {ROOT: root_repo}.get) is None


async def test_a_repo_cannot_vouch_in_anothers_name():
    """A delegation whose issuer disagrees with the repo it lives in is
    ignored (confused-deputy guard)."""
    pen = _did_key()
    root_repo = _repo(ROOT)
    _delegate(root_repo, "did:web:someone.else.example", pen)
    assert await walk_delegation_chain(pen, ROOT, {ROOT: root_repo}.get) is None


async def test_wrong_issuer_record_cannot_shadow_a_valid_delegation():
    """A NEWER record naming a foreign issuer must not supersede an older
    valid delegation for the same pen: the issuer filter runs BEFORE
    supersession."""
    pen = _did_key()
    root_repo = _repo(ROOT)
    _delegate(root_repo, ROOT, pen)
    _delegate(root_repo, "did:web:someone.else.example", pen)  # newer, foreign
    assert await walk_delegation_chain(pen, ROOT, {ROOT: root_repo}.get) is not None


async def test_cyclic_delegation_terminates_and_refuses():
    a, b = _did_key(), _did_key()
    a_repo, b_repo = _repo(a), _repo(b)
    root_repo = _repo(ROOT)
    _delegate(root_repo, ROOT, a)
    _delegate(a_repo, a, b)
    _delegate(b_repo, b, a)  # cycle
    source = {ROOT: root_repo, a: a_repo, b: b_repo}.get
    assert await walk_delegation_chain(_did_key(), ROOT, source) is None


async def test_async_repo_source_is_supported():
    pen = _did_key()
    root_repo = _repo(ROOT)
    _delegate(root_repo, ROOT, pen)

    async def source(did):
        return root_repo if did == ROOT else None

    assert await walk_delegation_chain(pen, ROOT, source) is not None


# --- visibility is repo visibility ----------------------------------------------


async def test_counterparty_with_repo_access_verifies_and_stranger_is_refused():
    """The same identity, two verifiers: one that can read the delegation
    repo (public repo / admitted party) walks the chain; one whose access is
    refused fails closed. No freshness machinery exists to soften either."""
    pen = _did_key()
    root_repo = _repo(ROOT)
    _delegate(root_repo, ROOT, pen)

    def admitted_source(did):
        return root_repo if did == ROOT else None

    def stranger_source(did):
        return None  # gated repo: not admitted → no view → fail closed

    assert await walk_delegation_chain(pen, ROOT, admitted_source) is not None
    assert await walk_delegation_chain(pen, ROOT, stranger_source) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
