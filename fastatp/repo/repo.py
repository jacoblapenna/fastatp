"""Present-state repository: current MST + signed commit, nothing else.

One DID = one repo holding many collections (NSIDs). A delete restructures
the tree and orphans blocks; every commit ends with a garbage collection that
deletes blocks unreachable from the new commit, so store size tracks current
records, never total writes. Commits carry ``prev: null`` — there is no
walkable history chain. Retention is a per-collection application policy
(e.g. FIFO a telemetry collection), not a repo setting.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Union

from fastatp import dagcbor, datamodel
from fastatp.cid import CID
from fastatp.crypto import PrivateKey, PublicKey
from fastatp.exceptions import DataModelError, RepoError
from fastatp.lex.types import Blob, CidLink, validate_nsid
from fastatp.lexicon import Lexicon
from fastatp.repo.blobs import BlobInfo, BlobStore
from fastatp.repo.car import decode_car, encode_car
from fastatp.repo.commit import Commit
from fastatp.repo.mst import MST, validate_mst_key
from fastatp.repo.store import BlockStore, MemoryBlockStore
from fastatp.tid import TidClock

RecordLike = Union[Lexicon, dict[str, Any]]  # noqa: UP007 - runtime alias


@dataclass(frozen=True)
class WriteOp:
    action: str  # "create" | "update" | "delete"
    collection: str
    rkey: str
    record: RecordLike | None = None

    @property
    def path(self) -> str:
        return f"{self.collection}/{self.rkey}"


@dataclass(frozen=True)
class CommitEvent:
    """Everything a firehose needs to announce one commit."""

    commit: Commit
    commit_cid: CID
    rev: str
    since: str | None
    prev_data: CID | None
    ops: list[dict[str, Any]]
    blocks: dict[CID, bytes] = field(repr=False)
    time: str = ""

    def blocks_car(self) -> bytes:
        return encode_car([self.commit_cid], self.blocks)


def _record_bytes(record: RecordLike) -> bytes:
    if isinstance(record, Lexicon):
        return record.to_dag_cbor()
    return dagcbor.encode(datamodel.from_json(record))


class Repo:
    """A DID's signed, present-state repository over a block store."""

    def __init__(
        self,
        did: str,
        signing_key: PrivateKey,
        store: BlockStore | None = None,
        clock: TidClock | None = None,
        *,
        head_cid: CID | None = None,
        on_commit: Callable[[CommitEvent], None] | None = None,
        blobs: BlobStore | None = None,
    ) -> None:
        self.did = did
        self.signing_key = signing_key
        self.store: BlockStore = store if store is not None else MemoryBlockStore()
        # the media capability is optional wiring: media-free
        # peers carry no blob store and pay nothing
        self.blobs = blobs
        self.clock = clock or TidClock()
        self.mst = MST(self.store)
        self._entries: dict[str, CID] = {}
        self._commit: Commit | None = None
        self._commit_cid: CID | None = None
        self._root: CID | None = None
        self._mst_nodes: set[CID] = set()
        self._on_commit = on_commit
        if head_cid is not None:
            self._load_head(head_cid)  # load-forever: reconstruct from a durable store
        else:
            self._commit_now([])  # genesis: signed empty tree

    def _load_head(self, head_cid: CID) -> None:
        """Reconstruct state from a persisted head commit already in the store
        (no genesis). Integrity is by content address — the block must hash to
        ``head_cid`` — not by signature: after a key rotation the head is signed
        by the NEW key, and coupling loadability to the current signing key would
        make a crash mid-rotation unrecoverable."""
        block = self.store.get(head_cid)
        if block is None:
            raise RepoError(f"cannot load repo: head commit {head_cid} not in store")
        if CID.for_dag_cbor(block) != head_cid:
            raise RepoError(f"stored head block does not hash to {head_cid} (corruption)")
        try:
            commit = Commit.from_dag_cbor(block)
        except DataModelError as e:
            raise RepoError(f"stored head commit is not valid DAG-CBOR: {e}") from None
        if commit.did != self.did:
            raise RepoError(f"stored repo is for {commit.did}, not {self.did}")
        try:
            self._entries = self.mst.load(commit.data)
        except DataModelError as e:
            raise RepoError(f"stored MST is malformed: {e}") from None
        self._commit = commit
        self._commit_cid = head_cid
        self._root = commit.data
        self._mst_nodes = self.mst.node_cids(commit.data)

    # --- state --------------------------------------------------------------

    @property
    def commit(self) -> Commit:
        assert self._commit is not None
        return self._commit

    @property
    def commit_cid(self) -> CID:
        assert self._commit_cid is not None
        return self._commit_cid

    @property
    def rev(self) -> str:
        return self.commit.rev

    @property
    def root(self) -> CID:
        assert self._root is not None
        return self._root

    def record_count(self, collection: str | None = None) -> int:
        if collection is None:
            return len(self._entries)
        prefix = collection + "/"
        return sum(1 for key in self._entries if key.startswith(prefix))

    def list_records(self, collection: str) -> list[tuple[str, CID]]:
        prefix = collection + "/"
        return sorted(
            (key[len(prefix) :], cid)
            for key, cid in self._entries.items()
            if key.startswith(prefix)
        )

    def get_record(self, collection: str, rkey: str) -> dict[str, Any] | None:
        cid = self._entries.get(f"{collection}/{rkey}")
        if cid is None:
            return None
        data = self.store.get(cid)
        if data is None:
            raise RepoError(f"record block {cid} missing from store")
        return datamodel.to_json(dagcbor.decode(data))

    # --- writes -------------------------------------------------------------

    def put_record(self, collection: str, rkey: str, record: RecordLike) -> CommitEvent:
        action = "update" if f"{collection}/{rkey}" in self._entries else "create"
        return self.apply_writes([WriteOp(action, collection, rkey, record)])

    def delete_record(self, collection: str, rkey: str) -> CommitEvent:
        return self.apply_writes([WriteOp("delete", collection, rkey)])

    def recommit(self) -> CommitEvent:
        """Re-sign the current state under the current ``signing_key`` (no data
        change). Used after a key rotation so the persisted head verifies under
        the new key on the next load."""
        return self._commit_now([])

    def apply_writes(self, writes: list[WriteOp]) -> CommitEvent:
        if not writes:
            raise RepoError("apply_writes requires at least one write")
        # validate against the ACCUMULATED batch state, not just the committed
        # state — two creates for one path in a batch is a protocol error
        pending = set(self._entries)
        for write in writes:
            validate_nsid(write.collection)
            validate_mst_key(write.path)
            if write.action in ("create", "update") and write.record is None:
                raise RepoError(f"{write.action} requires a record")
            if write.action == "create":
                if write.path in pending:
                    raise RepoError(f"record already exists at {write.path}")
                pending.add(write.path)
            elif write.action == "update":
                if write.path not in pending:
                    raise RepoError(f"no record at {write.path}")
            elif write.action == "delete":
                if write.path not in pending:
                    raise RepoError(f"no record at {write.path}")
                pending.discard(write.path)
            else:
                raise RepoError(f"unknown write action {write.action!r}")
        return self._commit_now(writes)

    def _commit_now(self, writes: list[WriteOp]) -> CommitEvent:
        old_entries = dict(self._entries)
        old_mst_nodes = set(self._mst_nodes)
        since = self._commit.rev if self._commit else None
        prev_data = self._root

        new_entries = dict(self._entries)
        ops: list[dict[str, Any]] = []
        new_record_blocks: dict[CID, bytes] = {}
        for write in writes:
            prev_cid = old_entries.get(write.path)
            if write.action == "delete":
                del new_entries[write.path]
                ops.append({"action": "delete", "path": write.path, "cid": None, "prev": prev_cid})
                continue
            data = _record_bytes(write.record)  # type: ignore[arg-type]
            cid = CID.for_dag_cbor(data)
            self.store.put(cid, data)
            new_record_blocks[cid] = data
            new_entries[write.path] = cid
            op = {"action": write.action, "path": write.path, "cid": cid}
            if write.action == "update":
                op["prev"] = prev_cid
            ops.append(op)

        root = self.mst.build(new_entries)
        commit = Commit.create(self.did, root, self.clock.now(), self.signing_key)
        commit_bytes = commit.to_dag_cbor()
        commit_cid = commit.cid()
        self.store.put(commit_cid, commit_bytes)

        new_mst_nodes = self.mst.node_cids(root)
        self._entries = new_entries
        self._commit = commit
        self._commit_cid = commit_cid
        self._root = root
        self._mst_nodes = new_mst_nodes

        # event blocks: the commit, MST nodes new in this commit, new records,
        # and — per the repository spec's diff rules — the covering proof for
        # every written path, so consumers can verify by operation inversion
        event_blocks: dict[CID, bytes] = {commit_cid: commit_bytes}
        needed = new_mst_nodes - old_mst_nodes
        for write in writes:
            needed |= self.mst.covering_proof(root, write.path)
        for cid in needed:
            block = self.store.get(cid)
            if block is None:  # a custom BlockStore violating its contract
                raise RepoError(f"store lost freshly written MST block {cid}")
            event_blocks[cid] = block
        event_blocks.update(new_record_blocks)

        event = CommitEvent(
            commit=commit,
            commit_cid=commit_cid,
            rev=commit.rev,
            since=since,
            prev_data=prev_data,
            ops=ops,
            blocks=event_blocks,
            time=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        # persist the new head BEFORE GC deletes the old commit block: a crash
        # between the two must never leave repo_head naming a block GC removed.
        # Ordering is safe either way — pre-persist crash keeps the old head
        # (old blocks still present, not yet GC'd); post-persist crash keeps the
        # new head (old blocks leak until the next GC, harmless).
        if self._on_commit is not None:
            self._on_commit(event)  # durable head tracking (fires on genesis too)
        self._collect_garbage()
        return event

    def _collect_garbage(self) -> None:
        reachable = {self.commit_cid} | self._mst_nodes | set(self._entries.values())
        for cid in list(self.store.cids()):  # snapshot: we mutate while iterating
            if cid not in reachable:
                self.store.delete(cid)

    # --- blobs (the optional media capability) ----------------------------------

    def _require_blobs(self) -> BlobStore:
        if self.blobs is None:
            raise RepoError("this repo has no blob store; pass Repo(blobs=...) to enable media")
        return self.blobs

    def add_blob(self, data: bytes, mime_type: str) -> Blob:
        """Store blob bytes; returns the palette ``Blob`` object to embed in a
        record. The CID is raw codec (0x55) + sha-256 — never dag-cbor."""
        store = self._require_blobs()
        cid = CID.for_raw(data)
        # rev stamp = current head rev, i.e. the rev BEFORE the commit that
        # references this blob. listBlobs?since= therefore matches with >=
        # (a relay cursored at exactly this rev has not seen that commit)
        store.put(cid, data, mime_type=mime_type, rev=self.rev)
        return Blob(ref=CidLink(cid.encode()), mime_type=mime_type, size=len(data))

    def get_blob(self, cid: CID) -> bytes | None:
        return self.blobs.get(cid) if self.blobs is not None else None

    def blob_info(self, cid: CID) -> BlobInfo | None:
        return self.blobs.info(cid) if self.blobs is not None else None

    def list_blobs(self) -> list[BlobInfo]:
        """All stored blobs, sorted by CID string (deterministic cursoring)."""
        if self.blobs is None:
            return []
        return sorted(self.blobs.infos(), key=lambda b: b.cid.encode())

    def blob_refs(self) -> set[CID]:
        """Blob CIDs referenced by the CURRENT records (walks every record)."""
        refs: set[CID] = set()
        for cid in set(self._entries.values()):
            data = self.store.get(cid)
            if data is None:
                raise RepoError(f"record block {cid} missing from store")
            refs |= datamodel.blob_refs(dagcbor.decode(data))
        return refs

    def gc_blobs(self) -> int:
        """Delete blobs no current record references; returns the count.

        Deliberately explicit rather than per-commit: blob
        retention is application policy, like record retention.
        """
        if self.blobs is None:
            return 0
        live = self.blob_refs()
        dropped = 0
        for info in list(self.blobs.infos()):
            if info.cid not in live:
                self.blobs.delete(info.cid)
                dropped += 1
        return dropped

    # --- CAR export / import ---------------------------------------------------

    def commit_car(self) -> bytes:
        """CAR carrying the current commit block only (the #sync event body)."""
        block = self.store.get(self.commit_cid)
        if block is None:
            raise RepoError(f"commit block {self.commit_cid} missing from store")
        return encode_car([self.commit_cid], {self.commit_cid: block})

    def export_car(self) -> bytes:
        """Current-state CAR: commit root + every reachable block."""
        blocks: dict[CID, bytes] = {}
        reachable = [self.commit_cid, *self._mst_nodes, *set(self._entries.values())]
        for cid in reachable:
            data = self.store.get(cid)
            if data is None:
                raise RepoError(f"reachable block {cid} missing from store")
            blocks[cid] = data
        return encode_car([self.commit_cid], blocks)

    @staticmethod
    def read_car(
        data: bytes, verify_key: PublicKey | None = None
    ) -> tuple[Commit, dict[str, CID], dict[CID, bytes]]:
        """Parse a repo CAR into (commit, key->record CID map, blocks).

        Integrity comes from content addressing: every CAR block is checked
        against its CID on decode, and the entry map is read by walking only
        CIDs reachable from the signed commit's ``data`` root (missing or
        substituted blocks fail the walk). Pass ``verify_key`` to also check
        the commit signature.
        """
        roots, blocks = decode_car(data)
        if len(roots) != 1:
            raise RepoError("repo CAR must have exactly one root (the commit)")
        commit_block = blocks.get(roots[0])
        if commit_block is None:
            raise RepoError("repo CAR is missing its commit block")
        try:
            commit = Commit.from_dag_cbor(commit_block)
        except DataModelError as e:
            raise RepoError(f"CAR commit block is not valid DAG-CBOR: {e}") from None
        if verify_key is not None and not commit.verify(verify_key):
            raise RepoError("commit signature does not verify")
        store = MemoryBlockStore()
        for cid, block in blocks.items():
            store.put(cid, block)
        try:
            entries = MST(store).load(commit.data)
        except DataModelError as e:
            raise RepoError(f"CAR contains malformed MST blocks: {e}") from None
        return commit, entries, blocks

    def import_car(
        self, data: bytes, verify_key: PublicKey | None = None, *, gc: bool = True
    ) -> None:
        """Replace this repo's state with a CAR's contents (checkpoint load).

        A checkpoint load is not a commit: no ``on_commit`` fires (there is
        no honest CommitEvent to announce — no ops, no valid ``since``). A
        caller that tracks a durable head pointer must therefore pass
        ``gc=False``, persist its pointer, and then call
        :meth:`collect_garbage` — the same persist-head-before-GC ordering
        ``_commit_now`` enforces, so a crash mid-import never leaves the
        pointer naming a deleted block (see ``Identity.import_repo_car``).
        """
        commit, entries, blocks = self.read_car(data, verify_key)
        if commit.did != self.did:
            raise RepoError(f"CAR is for {commit.did}, not {self.did}")
        for cid, block in blocks.items():
            self.store.put(cid, block)
        self._entries = entries
        self._commit = commit
        self._commit_cid = commit.cid()
        self._root = commit.data
        self._mst_nodes = self.mst.node_cids(commit.data)
        if gc:
            self._collect_garbage()

    def collect_garbage(self) -> None:
        """Drop store blocks unreachable from the current head.

        Runs automatically on every commit and (by default) on import; only
        an ``import_car(gc=False)`` caller needs to invoke it, after its
        durable head pointer is persisted."""
        self._collect_garbage()

    def prove(self, collection: str, rkey: str) -> bytes:
        """A standalone record proof: CAR of commit + MST path + record block.

        Anyone holding this slice can verify — with nothing but a DID
        resolver — that this exact record is present in this repo's signed
        current state. This is the atproto-native "signed message": emit it
        over any transport, verify with ``Peer.verify()``.
        """
        key = f"{collection}/{rkey}"
        record_cid = self._entries.get(key)
        if record_cid is None:
            raise RepoError(f"no record at {key}")
        blocks: dict[CID, bytes] = {}
        needed = [self.commit_cid, *self.mst.prove_path(self.root, key), record_cid]
        for cid in needed:
            data = self.store.get(cid)
            if data is None:
                raise RepoError(f"block {cid} missing from store")
            blocks[cid] = data
        return encode_car([self.commit_cid], blocks)
