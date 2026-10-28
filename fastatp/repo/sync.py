"""Firehose framing and consumer-side verification.

Frames follow the atproto event-stream wire format: each WebSocket binary
message is two concatenated DAG-CBOR items — a header ``{"op": 1, "t":
"#commit" | "#sync" | "#account" | "#identity" | "#info"}`` (or ``{"op":
-1}`` for errors) and a body shaped like ``com.atproto.sync.subscribeRepos``.

``RepoMirror`` is the consumer half: it ingests ``#sync`` events and
``#commit`` events, verifies the commit signature against the producer's
resolved DID document, and verifies every commit two ways: by **operation
inversion** (the sync-v1.1 relay algorithm — the diff's partial MST, ops
applied in reverse, must re-derive ``prevData``) and — because the MST is
deterministic — *inductively*: applying the announced ops to its own key map
must re-derive exactly the commit's ``data`` root.
"""

from __future__ import annotations

import functools
from typing import Any

from fastatp import dagcbor, datamodel
from fastatp.cid import CID
from fastatp.crypto import PublicKey
from fastatp.did.document import DIDDocument
from fastatp.did.resolver import DIDResolver, did_key_resolver
from fastatp.exceptions import (
    CommitGap,
    DataModelError,
    DIDNotFound,
    FirehoseErrorFrame,
    RepoError,
)
from fastatp.repo.car import decode_car
from fastatp.repo.commit import Commit
from fastatp.repo.mst import MST
from fastatp.repo.repo import CommitEvent
from fastatp.repo.store import MemoryBlockStore

# --- frames -------------------------------------------------------------------


def encode_frame(t: str, body: dict[str, Any]) -> bytes:
    return dagcbor.encode({"op": 1, "t": t}) + dagcbor.encode(body)


def encode_error_frame(error: str, message: str | None = None) -> bytes:
    body: dict[str, Any] = {"error": error}
    if message:
        body["message"] = message
    return dagcbor.encode({"op": -1}) + dagcbor.encode(body)


def decode_frame(data: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        header, rest = dagcbor.decode_first(data)
        body, remainder = dagcbor.decode_first(rest)
    except DataModelError as e:
        raise RepoError(f"malformed frame: {e}") from None
    if remainder:
        raise RepoError("trailing bytes after frame body")
    if not isinstance(header, dict) or not isinstance(body, dict):
        raise RepoError("malformed frame")
    op = header.get("op")
    if op == -1:
        raise FirehoseErrorFrame(str(body.get("error")), str(body.get("message", "")))
    if op != 1:
        # the event-stream spec reserves other ops; a future op with a
        # #commit-shaped t must be ignored, not applied as if op were 1
        raise RepoError(f"ignoring frame with unknown op {op!r}")
    return header, body


def commit_frame_body(did: str, seq: int, event: CommitEvent) -> dict[str, Any]:
    """A com.atproto.sync.subscribeRepos#commit body for a repo commit.

    ``rebase`` and ``blobs`` are deprecated in the lexicon but still listed
    as required fields there (the reference PDS emits both) — so they stay.
    """
    return {
        "seq": seq,
        "rebase": False,
        "tooBig": False,
        "repo": did,
        "commit": event.commit_cid,
        "rev": event.rev,
        "since": event.since,
        "blocks": event.blocks_car(),
        "ops": [dict(op) for op in event.ops],
        "blobs": [],
        "time": event.time,
        "prevData": event.prev_data,
    }


def sync_frame_body(did: str, seq: int, rev: str, car: bytes, time: str) -> dict[str, Any]:
    """A com.atproto.sync.subscribeRepos#sync body.

    Per the sync spec, ``car`` must contain the commit block ONLY (root =
    commit CID): a #sync asserts current state; consumers re-fetch contents
    via ``getRepo``.
    """
    return {"seq": seq, "did": did, "blocks": car, "rev": rev, "time": time}


def account_frame_body(
    did: str, seq: int, time: str, *, active: bool, status: str | None = None
) -> dict[str, Any]:
    """A com.atproto.sync.subscribeRepos#account body (hosting status).

    Relays ignore #commit/#sync events for accounts they do not know to be
    ``active``, so a host announces activation before commits flow.
    """
    body: dict[str, Any] = {"seq": seq, "did": did, "time": time, "active": active}
    if status is not None:
        body["status"] = status
    return body


def identity_frame_body(
    did: str, seq: int, time: str, *, handle: str | None = None
) -> dict[str, Any]:
    """A com.atproto.sync.subscribeRepos#identity body (identity-cache prod)."""
    body: dict[str, Any] = {"seq": seq, "did": did, "time": time}
    if handle is not None:
        body["handle"] = handle
    return body


def verify_commit_inversion(body: dict[str, Any]) -> CID | None:
    """Verify a #commit body by operation inversion — the exact algorithm a
    sync-v1.1 relay runs: parse ``blocks`` as a partial MST rooted at the
    commit's ``data``, apply ``ops`` in reverse, and require the recomputed
    root to equal ``prevData``.

    Returns the recomputed previous root (``None`` for a genesis commit,
    which carries no ``prevData`` and has nothing to invert to). Raises
    :class:`RepoError` when the diff cannot support inversion or the ops are
    inaccurate — a relay's exact reasons to reject the event.
    """
    _roots, blocks = decode_car(body["blocks"])
    commit_block = blocks.get(body["commit"])
    if commit_block is None:
        raise RepoError("#commit blocks CAR is missing the commit block")
    commit = Commit.from_dag_cbor(commit_block)
    prev_data = body.get("prevData")
    if prev_data is None:
        if body.get("since") is not None:
            raise RepoError("#commit has since but no prevData (required for inversion)")
        return None
    changes = [
        (op.get("action"), op.get("path"), op.get("cid"), op.get("prev"))
        for op in body.get("ops", [])
    ]
    if not changes:
        # an empty commit (rev/signature refresh) must not change the tree;
        # nothing to invert and no tree blocks to demand (the reference PDS
        # sends the commit block only for these)
        if commit.data != prev_data:
            raise RepoError("empty ops but the data root changed")
        return prev_data
    store = MemoryBlockStore()
    for cid, block in blocks.items():
        store.put(cid, block)
    inverted = MST(store).invert_changes(commit.data, changes)
    if inverted != prev_data:
        raise RepoError(
            f"operation inversion does not re-derive prevData "
            f"(inverted to {inverted}, event says {prev_data})"
        )
    return inverted


# --- consumer -----------------------------------------------------------------


def _repo_errors(fn: Any) -> Any:
    """Boundary guard: everything a frame handler raises for malformed or
    unverifiable input surfaces as RepoError (DIDNotFound stays itself as the
    documented resolution-miss signal)."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except (DataModelError, ValueError, KeyError, TypeError) as e:
            raise RepoError(f"{fn.__name__}: malformed event data: {e}") from None

    return wrapper


class RepoMirror:
    """Verified follower of one producer's repo.

    Keeps the current key->CID map, the record blocks, the last commit, and
    the last seen firehose ``seq`` (its resume cursor).
    """

    def __init__(self, did: str, resolver: DIDResolver | None = None) -> None:
        self.did = did
        self.resolver = resolver
        self.store = MemoryBlockStore()
        self.entries: dict[str, CID] = {}
        self.commit: Commit | None = None
        self.seq: int | None = None
        #: set when a #sync announced a state this mirror does not hold; the
        #: owner of the transport should fetch getRepo and call
        #: :meth:`load_checkpoint` (spec: #sync carries the commit only)
        self.resync_target: Commit | None = None

    @property
    def rev(self) -> str | None:
        return self.commit.rev if self.commit else None

    async def signing_keys(self) -> list[PublicKey]:
        """Candidate signing keys, re-resolved per verification.

        No local caching: the resolver already caches (WebResolver honors
        ``Cache-Control``), so key rotation propagates instead of being
        pinned to whatever key the mirror saw first. Rotation-listed keys
        are excluded; the ``#atproto``-fragment method is preferred.

        Resolution failures propagate as themselves so callers can retry:
        ``DIDNotFound`` (miss) and ``DIDResolutionTimeout`` (transient) —
        deliberately NOT wrapped as RepoError, which signals a bad event.
        """
        from fastatp.did.document import signing_key_candidates
        from fastatp.exceptions import CryptoError, DIDDocumentInvalid

        try:
            if self.did.startswith("did:key:"):
                doc: DIDDocument | None = await did_key_resolver(self.did)
            elif self.resolver is not None:
                doc = await self.resolver(self.did)
            else:
                raise DIDNotFound(f"no resolver configured to resolve {self.did}")
            if doc is None:
                raise DIDNotFound(f"no document for {self.did}")
            return signing_key_candidates(doc)
        except (DIDDocumentInvalid, CryptoError) as e:
            # mirror contract: RepoError for verification problems,
            # DIDNotFound only for a resolution miss
            raise RepoError(f"{self.did}: unusable DID document: {e}") from None

    async def _verify_commit(self, commit: Commit) -> None:
        if commit.did != self.did:
            raise RepoError(f"commit is for {commit.did}, expected {self.did}")
        if not any(commit.verify(key) for key in await self.signing_keys()):
            raise RepoError(f"{self.did}: commit signature does not verify")

    async def handle_frame(self, data: bytes) -> list[dict[str, Any]]:
        """Dispatch one wire frame; returns new record JSONs for #commit."""
        header, body = decode_frame(data)
        t = header.get("t")
        if t == "#sync":
            await self.apply_sync(body)
            return []
        if t == "#commit":
            return await self.apply_commit(body)
        return []  # #info and future event types are non-fatal

    @staticmethod
    def _field(mapping: dict[str, Any], key: str, where: str) -> Any:
        try:
            return mapping[key]
        except (KeyError, TypeError):
            raise RepoError(f"{where} is missing required field {key!r}") from None

    @_repo_errors
    async def apply_sync(self, body: dict[str, Any]) -> None:
        """Handle a #sync: verify the asserted commit; adopt or flag resync.

        Per the sync spec the ``blocks`` CAR carries the commit block only.
        If the asserted state matches what this mirror already holds, it is a
        confirmation. Otherwise :attr:`resync_target` is set and the caller
        must fetch the full repo (``getRepo``) and pass it to
        :meth:`load_checkpoint`. A CAR that happens to carry the full state
        (FastATP's pre-Gate-14 checkpoint shape) is loaded directly.
        """
        roots, blocks = decode_car(self._field(body, "blocks", "#sync body"))
        commit_block = blocks.get(roots[0]) if roots else None
        if commit_block is None:
            raise RepoError("#sync CAR is missing its commit block")
        commit = Commit.from_dag_cbor(commit_block)
        await self._verify_commit(commit)
        if body.get("rev") != commit.rev:
            raise RepoError("#sync rev does not match the signed commit")
        if isinstance(body.get("seq"), int):
            self.seq = body["seq"]
        if self.commit is not None and commit.data == self.commit.data:
            self.commit = commit  # state confirmed (possibly re-signed)
            self.resync_target = None
            return
        store = MemoryBlockStore()
        for cid, block in blocks.items():
            store.put(cid, block)
        try:
            entries = MST(store).load(commit.data)
        except RepoError:
            # commit-only (or otherwise partial) #sync: state must be re-fetched
            self.resync_target = commit
            return
        self.entries = entries
        self.store = store
        self.commit = commit
        self.resync_target = None

    async def load_checkpoint(self, car: bytes) -> None:
        """Adopt a full current-state CAR (a ``getRepo`` body) as local state."""
        roots, blocks = decode_car(car)
        commit_block = blocks.get(roots[0]) if roots else None
        if commit_block is None:
            raise RepoError("checkpoint CAR is missing its commit block")
        commit = Commit.from_dag_cbor(commit_block)
        await self._verify_commit(commit)
        store = MemoryBlockStore()
        for cid, block in blocks.items():
            store.put(cid, block)
        self.entries = MST(store).load(commit.data)
        self.store = store
        self.commit = commit
        self.resync_target = None

    @_repo_errors
    async def apply_commit(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        """Verify and apply one commit event; returns created/updated records."""
        _roots, blocks = decode_car(self._field(body, "blocks", "#commit body"))
        commit_block = blocks.get(self._field(body, "commit", "#commit body"))
        if commit_block is None:
            raise RepoError("#commit blocks CAR is missing the commit block")
        commit = Commit.from_dag_cbor(commit_block)
        await self._verify_commit(commit)
        if body.get("rev") != commit.rev:
            raise RepoError("frame rev does not match the signed commit")
        if self.commit is not None and body.get("since") != self.commit.rev:
            raise CommitGap(
                f"commit gap: event since={body.get('since')!r} but local rev={self.commit.rev!r}"
            )
        if self.commit is not None and body.get("prevData") != self.commit.data:
            raise CommitGap(
                f"prevData mismatch: event says {body.get('prevData')} "
                f"but local data root is {self.commit.data}"
            )

        new_entries = dict(self.entries)
        new_records: list[dict[str, Any]] = []
        new_blocks: dict[CID, bytes] = {}
        from fastatp.repo.mst import validate_mst_key

        for op in body.get("ops", []):
            path = self._field(op, "path", "commit op")
            action = self._field(op, "action", "commit op")
            try:
                validate_mst_key(path)
            except ValueError as e:
                raise RepoError(f"commit op has invalid path: {e}") from None
            if action == "delete":
                if path not in new_entries:
                    raise RepoError(f"delete for unknown record {path}")
                del new_entries[path]
                continue
            if action not in ("create", "update"):
                raise RepoError(f"unknown op action {action!r}")
            cid = self._field(op, "cid", "commit op")
            block = blocks.get(cid)
            if block is None:
                raise RepoError(f"record block {cid} missing from event blocks")
            new_entries[path] = cid
            new_blocks[cid] = block
            try:
                record = datamodel.to_json(dagcbor.decode(block))
            except DataModelError as e:
                raise RepoError(f"record block {cid} is not valid DAG-CBOR: {e}") from None
            new_records.append({"path": path, "action": action, "cid": cid, "record": record})

        # inductive verification: the deterministic MST over the announced
        # key set must re-derive the signed data root exactly
        derived_root = MST(MemoryBlockStore()).build(new_entries)
        if derived_root != commit.data:
            raise RepoError(
                f"ops do not reproduce the signed MST root "
                f"(derived {derived_root}, commit says {commit.data})"
            )
        # the sync-v1.1 relay check: the diff's own blocks must support
        # operation inversion back to prevData (catches insufficient diffs
        # that the inductive check — which uses local state — cannot)
        verify_commit_inversion(body)

        # state (including the block store) mutates only after the event
        # fully verified — a failed commit must not leave blocks behind
        for cid, block in new_blocks.items():
            self.store.put(cid, block)
        self.entries = new_entries
        self.commit = commit
        # prune superseded/deleted record blocks so the mirror's store
        # tracks current records, mirroring the producer's own GC. This
        # deliberately also drops checkpoint commit/MST blocks: the mirror's
        # store serves get_record() only — entries + self.commit carry the
        # verified state, and proofs are the producer's job (Repo.prove)
        live = set(new_entries.values())
        for cid in list(self.store.cids()):  # snapshot: we mutate while iterating
            if cid not in live:
                self.store.delete(cid)
        if isinstance(body.get("seq"), int):
            self.seq = body["seq"]
        return new_records

    def get_record(self, collection: str, rkey: str) -> dict[str, Any] | None:
        cid = self.entries.get(f"{collection}/{rkey}")
        if cid is None:
            return None
        block = self.store.get(cid)
        if block is None:
            return None
        return datamodel.to_json(dagcbor.decode(block))
