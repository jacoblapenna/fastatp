"""Present-state repository: current MST + signed commit, nothing else.

A fresh repo is ``await Repo.create(did, signing_key)``: the genesis commit
is signed through the signer seam (``fastatp.crypto.Signer``), so a key the
process cannot hold works the same as one in memory. ``Repo(did, signing_key,
head_cid=...)`` reopens a stored head without signing anything. Every write
is awaited for the same reason: a hardware signer takes tens of
milliseconds, and the peer keeps serving meanwhile.

Where the head lives is the app's choice (``fastatp.repo.head``). With a
``head_store=`` the pointer and the firehose seq sit in the app's database and
every commit advances them with a compare-and-swap, so several processes
behind one database can never fork a repo: ``await Repo.create(...)`` makes
the genesis, ``await Repo.open(...)`` loads the current head, and a commit
that loses the swap reloads the new head and retries on it (a write the other
process already made, byte for byte, is then a no-op). Without one, an
in-memory head store gives today's single-process behaviour.

Garbage collection is per commit and per repo: a commit deletes only the
blocks it orphaned (reachable from the head it replaced and not from the new
one), never a sweep of the store, so a block store shared between repos, or
between processes on one repo, loses nothing that another head still reaches
(a store that is this repo's alone can still be swept with
``collect_garbage()``). What a shared store cannot tell apart is one block two
repos both reference by content: an adapter for a database holding many repos
keys blocks by ``(did, cid)`` so that never happens.

A repo reopened with no pen (``Repo(did, None, store=..., head_cid=...)``) is
**frozen**: it serves ``getRepo`` and the firehose exactly as before, to the
public or to whoever holds a live capability record in it, and refuses every
commit. Pushes still land through ``import_car``, which signs nothing. This is
the shape of a host serving a repo whose pen lives elsewhere (a browser, a
device): requiring the pen to *serve* would protect nothing, since records
are never encrypted in process and anyone who can start a peer over the store
can read it; requiring the pen to *write* is the whole point, and that stays.

Frozen is not what keeps a repo honest; signatures are. Anyone with write
access to the storage (a privileged user on the disk, a PDS operator) can
write bytes into any repo, on any atproto host, and it has always been the
consumer's job to verify that a commit was signed by the identity's pen.
That is atproto's design, not FastATP's. Frozen adds that the same rule
holds in this host's own code: without the pen there is no path here that
makes a commit, so the host cannot produce one the owner did not sign.

One DID = one repo holding many collections (NSIDs). A delete restructures
the tree and orphans blocks; every commit ends with a garbage collection that
deletes blocks unreachable from the new commit, so store size tracks current
records, never total writes. Commits carry ``prev: null`` — there is no
walkable history chain. Retention is a per-collection application policy
(e.g. FIFO a telemetry collection), not a repo setting.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Union

from fastatp import dagcbor, datamodel
from fastatp.cid import CID
from fastatp.crypto import PrivateKey, PublicKey, Signer, as_signer
from fastatp.exceptions import (
    DataModelError,
    FastATPError,
    HeadMoved,
    RepoError,
    StaleCheckpoint,
)
from fastatp.lex.types import Blob, CidLink, validate_nsid
from fastatp.lexicon import Lexicon
from fastatp.repo.blobs import BlobInfo, BlobStore
from fastatp.repo.car import decode_car, encode_car
from fastatp.repo.commit import Commit
from fastatp.repo.head import Head, HeadStore, MemoryHeadStore
from fastatp.repo.mst import MST, validate_mst_key
from fastatp.repo.storage import Storage
from fastatp.repo.store import BlockStore, MemoryBlockStore
from fastatp.tid import TidClock, decode_tid

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
    #: the firehose seq the head store assigned this commit (0 with none)
    seq: int = 0

    def blocks_car(self) -> bytes:
        return encode_car([self.commit_cid], self.blocks)


def _record_bytes(record: RecordLike) -> bytes:
    if isinstance(record, Lexicon):
        return record.to_dag_cbor()
    return dagcbor.encode(datamodel.from_json(record))


def _check_rev(rev: str) -> None:
    """A head rev must be a TID (``Commit.from_map`` takes any string), and
    must be checked before a repo loads it, without touching any clock."""
    try:
        decode_tid(rev)
    except (ValueError, FastATPError) as e:
        raise RepoError(f"head commit rev {rev!r} is not a TID: {e}") from None


class Repo:
    """A DID's signed, present-state repository over a block store."""

    def __init__(
        self,
        did: str,
        signing_key: PrivateKey | Signer | None,
        store: BlockStore | None = None,
        clock: TidClock | None = None,
        *,
        head_cid: CID | None = None,
        head_store: HeadStore | None = None,
        on_commit: Callable[[CommitEvent], None] | None = None,
        blobs: BlobStore | None = None,
        storage: Storage | None = None,
    ) -> None:
        """Reopen a repo from ``head_cid`` in ``store``, or set one up for
        :meth:`create` or :meth:`open`. ``signing_key`` is a private key or
        any :class:`~fastatp.crypto.Signer`, held as ``self.signer``; ``None``
        makes the repo :attr:`frozen` (it serves, and never commits).
        ``storage`` is the one-decision form of ``store``, ``head_store`` and
        ``blobs`` (one database for all three, :mod:`fastatp.repo.storage`);
        pass it or them, not both. ``head_store`` is where the head and seq
        live; without one an in-memory store is used, and ``head_cid`` is how
        a caller-managed head is reopened. Without either the repo has no
        commit until its first write or import."""
        if storage is not None:
            if store is not None or head_store is not None or blobs is not None:
                raise ValueError("pass storage=, or store=/head_store=/blobs=, not both")
            store, head_store, blobs = storage.blocks, storage.heads, storage.blobs
        if head_cid is not None and head_store is not None:
            raise ValueError(
                "pass head_cid to reopen a caller-managed head, or head_store "
                "and Repo.open() to load one from the store; not both"
            )
        self.did = did
        self.head_store: HeadStore = head_store if head_store is not None else MemoryHeadStore()
        self._head: Head | None = None  # the store's view: what the next swap expects
        self.signer: Signer | None = as_signer(signing_key) if signing_key is not None else None
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
        # one head, one writer at a time: a commit awaits its signer, and two
        # coroutines on this object must not build on the same snapshot
        self._write_lock = asyncio.Lock()
        if head_cid is not None:
            self._load_head(head_cid)  # load-forever: reconstruct from a durable store

    @classmethod
    async def create(
        cls,
        did: str,
        signing_key: PrivateKey | Signer,
        store: BlockStore | None = None,
        clock: TidClock | None = None,
        *,
        head_store: HeadStore | None = None,
        on_commit: Callable[[CommitEvent], None] | None = None,
        blobs: BlobStore | None = None,
        storage: Storage | None = None,
    ) -> Repo:
        """A new repo with its genesis commit (a signed empty tree). With a
        head store that already holds a head for ``did`` this fails:
        :meth:`open` that one instead."""
        if signing_key is None:
            raise RepoError(
                "a fresh repo needs a pen to sign its genesis commit; a frozen repo "
                "reopens a stored head: Repo(did, None, store=..., head_cid=...)"
            )
        repo = cls(
            did,
            signing_key,
            store,
            clock,
            head_store=head_store,
            on_commit=on_commit,
            blobs=blobs,
            storage=storage,
        )
        async with repo._write_lock:
            try:
                await repo._commit_now([], genesis=True)
            except HeadMoved as e:
                raise RepoError(
                    f"the head store already holds a head for {did} ({e.head}); open it "
                    "with Repo.open() instead of creating it again"
                ) from None
        return repo

    @classmethod
    async def open(
        cls,
        did: str,
        signing_key: PrivateKey | Signer | None,
        store: BlockStore | None = None,
        head_store: HeadStore | None = None,
        clock: TidClock | None = None,
        *,
        on_commit: Callable[[CommitEvent], None] | None = None,
        blobs: BlobStore | None = None,
        storage: Storage | None = None,
    ) -> Repo:
        """Load ``did``'s current head from the head store (``head_store``, or
        ``storage.heads``), its blocks from the block store. ``signing_key=None``
        opens it frozen."""
        if head_store is None and storage is None:
            raise ValueError("Repo.open needs a head_store= or a storage= to load the head from")
        repo = cls(
            did,
            signing_key,
            store,
            clock,
            head_store=head_store,
            on_commit=on_commit,
            blobs=blobs,
            storage=storage,
        )
        await repo._reload()
        return repo

    async def _reload(self) -> None:
        """Take the head the store holds now (after a lost swap, or on open)."""
        head = await self.head_store.load(self.did)
        if head is None:
            raise RepoError(
                f"the head store holds no head for {self.did}: create the repo with "
                "Repo.create(..., head_store=...) first"
            )
        if head.cid != self._commit_cid:
            self._load_head(head.cid)
        self._head = head

    @property
    def seq(self) -> int:
        """The last firehose seq used on this repo (0 before any)."""
        return self._head.seq if self._head is not None else 0

    async def advance_seq(self) -> int:
        """Take the next firehose seq for a frame that is not a commit (an
        account, identity or sync announcement): the head stays, the seq
        moves, through the same swap, so the number is durable before the
        frame it stamps is delivered. Returns the seq taken."""
        async with self._write_lock:
            for _attempt in range(self._RETRIES + 1):
                new_head = Head(self.commit_cid, self.seq + 1)
                if await self.head_store.swap(self.did, self._head, new_head):
                    self._head = new_head
                    return new_head.seq
                await self._reload()
            raise HeadMoved(await self.head_store.load(self.did))

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
        _check_rev(commit.rev)  # refused before any state changes
        try:
            self._entries = self.mst.load(commit.data)
        except DataModelError as e:
            raise RepoError(f"stored MST is malformed: {e}") from None
        self._commit = commit
        self._commit_cid = head_cid
        self._root = commit.data
        self._mst_nodes = self.mst.node_cids(commit.data)
        self.clock.observe(commit.rev)  # only once the head is in place (see import_car)

    # --- state --------------------------------------------------------------

    _FROZEN = (
        "this repo is frozen: it has no pen on this host, so it serves and never "
        "commits; a push still lands through import_car"
    )
    _NO_COMMIT = (
        "this repo has no commit yet: a fresh repo is made with "
        "`await Repo.create(did, signing_key)`; `Repo(did, signing_key, head_cid=...)` "
        "reopens a stored head"
    )

    @property
    def frozen(self) -> bool:
        """True when this host holds no pen for the repo: reads and imports
        work, every commit is refused."""
        return self.signer is None

    @property
    def commit(self) -> Commit:
        if self._commit is None:
            raise RepoError(self._NO_COMMIT)
        return self._commit

    @property
    def commit_cid(self) -> CID:
        if self._commit_cid is None:
            raise RepoError(self._NO_COMMIT)
        return self._commit_cid

    @property
    def rev(self) -> str:
        return self.commit.rev

    @property
    def root(self) -> CID:
        if self._root is None:
            raise RepoError(self._NO_COMMIT)
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

    async def put_record(
        self, collection: str, rkey: str, record: RecordLike
    ) -> CommitEvent | None:
        action = "update" if f"{collection}/{rkey}" in self._entries else "create"
        return await self.apply_writes([WriteOp(action, collection, rkey, record)])

    async def delete_record(self, collection: str, rkey: str) -> CommitEvent | None:
        return await self.apply_writes([WriteOp("delete", collection, rkey)])

    async def recommit(self) -> CommitEvent:
        """Re-sign the current state under the current ``signer`` (no data
        change). Used after a key rotation so the persisted head verifies under
        the new key on the next load."""
        async with self._write_lock:
            event = await self._commit_retrying([])
            assert event is not None  # an empty write set never dedupes away
            return event

    async def apply_writes(self, writes: list[WriteOp]) -> CommitEvent | None:
        """Commit ``writes`` as one signed change.

        Writers sharing this object take turns: validation, signing, the head
        update and garbage collection happen under one lock, so concurrent
        writes land in sequence and each builds on the previous. A sync
        ``import_car`` that lands while a commit is being signed is caught
        after the signature and refused (``RepoError``), with nothing staged
        left behind.

        Across processes the head store decides: the commit lands only if the
        store's head is still the one it was built on. When it is not, the
        repo reloads the new head and retries the writes on it, a few times,
        re-validating each time; a write the other process already made byte
        for byte is dropped as done, and if nothing is left this returns
        ``None`` (the repo already holds exactly this). A ``create`` whose
        path now holds different bytes fails as it always has. Contention
        beyond the retries surfaces as :class:`HeadMoved`.
        """
        if not writes:
            raise RepoError("apply_writes requires at least one write")
        async with self._write_lock:
            return await self._commit_retrying(writes)

    _RETRIES = 3

    async def _commit_retrying(self, writes: list[WriteOp]) -> CommitEvent | None:
        for attempt in range(self._RETRIES + 1):
            pending = writes if attempt == 0 else self._still_needed(writes)
            if attempt and writes and not pending:
                return None  # the other writer did exactly this already
            try:
                return await self._apply_writes_locked(pending)
            except HeadMoved:
                if attempt == self._RETRIES:
                    raise
        raise AssertionError("unreachable")

    def _still_needed(self, writes: list[WriteOp]) -> list[WriteOp]:
        """On a retry, the writes the reloaded head does not already hold.

        Decided per path from the batch's final intent, never op by op: a
        ``delete`` then ``create`` of one path is a replacement, and dropping
        only its create would delete the path. Per path: if the batch ends by
        deleting it, nothing is needed once it is gone, else one delete; if it
        ends by putting record X there, nothing is needed once X is there,
        else one write. A bare ``create`` keeps its meaning (it fails if the
        path now holds other bytes); a replacement becomes an update.
        """
        by_path: dict[str, list[WriteOp]] = {}
        for write in writes:
            by_path.setdefault(write.path, []).append(write)
        needed: list[WriteOp] = []
        for path, ops in by_path.items():
            final = ops[-1]
            current = self._entries.get(path)
            if final.action == "delete":
                if current is not None:
                    needed.append(WriteOp("delete", final.collection, final.rkey))
                continue
            assert final.record is not None  # validated on the first attempt
            if current is not None and CID.for_dag_cbor(_record_bytes(final.record)) == current:
                continue  # the other writer put exactly this here
            replaces = len(ops) > 1 and any(op.action == "delete" for op in ops[:-1])
            if current is None:
                action = "create"
            elif final.action == "update" or replaces:
                action = "update"
            else:
                action = "create"  # a bare create on an occupied path: still refused
            needed.append(WriteOp(action, final.collection, final.rkey, final.record))
        return needed

    async def _apply_writes_locked(self, writes: list[WriteOp]) -> CommitEvent:
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
        return await self._commit_now(writes)

    async def _commit_now(self, writes: list[WriteOp], *, genesis: bool = False) -> CommitEvent:
        if self.signer is None:
            raise RepoError(self._FROZEN)
        old_entries = dict(self._entries)
        old_mst_nodes = set(self._mst_nodes)
        old_reachable = self._reachable()
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
        staged_nodes = self.mst.node_cids(root)  # before yielding: an import may GC them
        try:
            commit = await Commit.create(self.did, root, self.clock.now(), self.signer)
            # the signer yielded; a sync import_car may have moved the head
            # under this commit, and landing it would discard that state
            if self._root != prev_data or (self._commit.rev if self._commit else None) != since:
                raise RepoError(
                    "the head moved while this commit was being signed (a checkpoint "
                    "landed); nothing was committed, retry on the new head"
                )
        except BaseException:
            self._drop_staged(staged_nodes, new_record_blocks)
            raise
        commit_bytes = commit.to_dag_cbor()
        commit_cid = commit.cid()
        self.store.put(commit_cid, commit_bytes)  # blocks before head, always

        # the genesis is the start of the stream, not an event on it: seq 0,
        # so the first announced frame is 1 and a cursor of 0 misses nothing
        new_head = Head(commit_cid, 0 if genesis else self.seq + 1)
        new_mst_nodes = staged_nodes

        # the event is assembled BEFORE the head moves: once it has, another
        # process may commit on top and collect the proof nodes this one
        # reads back, and a commit must never be durable with no event to
        # announce it. Event blocks: the commit, MST nodes new in this commit,
        # new records, and — per the repository spec's diff rules — the
        # covering proof for every written path, so consumers can verify by
        # operation inversion
        event_blocks: dict[CID, bytes] = {commit_cid: commit_bytes}
        needed = new_mst_nodes - old_mst_nodes
        for write in writes:
            needed |= self.mst.covering_proof(root, write.path)
        for cid in needed:
            block = self.store.get(cid)
            if block is None:  # a custom BlockStore violating its contract
                self._drop_staged(staged_nodes | {commit_cid}, new_record_blocks)
                raise RepoError(f"store lost freshly written MST block {cid}")
            event_blocks[cid] = block
        event_blocks.update(new_record_blocks)

        if not await self.head_store.swap(self.did, self._head, new_head):
            # another process extended this head first: take its head, then
            # drop what this commit staged, except what the new head reaches
            # (the same record written by both has the same blocks)
            current = await self.head_store.load(self.did)
            if current is not None:
                await self._reload()
            self._drop_staged(staged_nodes | {commit_cid}, new_record_blocks)
            raise HeadMoved(current)
        self._head = new_head
        self._entries = new_entries
        self._commit = commit
        self._commit_cid = commit_cid
        self._root = root
        self._mst_nodes = new_mst_nodes

        event = CommitEvent(
            commit=commit,
            commit_cid=commit_cid,
            rev=commit.rev,
            since=since,
            prev_data=prev_data,
            ops=ops,
            blocks=event_blocks,
            time=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            seq=new_head.seq,
        )
        # persist the new head BEFORE GC deletes the old commit block: a crash
        # between the two must never leave repo_head naming a block GC removed.
        # Ordering is safe either way — pre-persist crash keeps the old head
        # (old blocks still present, not yet GC'd); post-persist crash keeps the
        # new head (old blocks leak until the next GC, harmless).
        if self._on_commit is not None:
            self._on_commit(event)  # durable head tracking (fires on genesis too)
        self._collect_orphans(old_reachable)
        return event

    def _drop_staged(self, staged_nodes: set[CID], record_blocks: dict[CID, bytes]) -> None:
        """A commit that will not land leaves no blocks behind: its new MST
        nodes and record blocks go, except any the current head reaches."""
        keep: set[CID] = set(self._mst_nodes) | set(self._entries.values())
        if self._commit_cid is not None:
            keep.add(self._commit_cid)
        for cid in (staged_nodes | set(record_blocks)) - keep:
            self.store.delete(cid)

    def _reachable(self) -> set[CID]:
        """Every block the current head reaches (empty before any commit)."""
        if self._commit_cid is None:
            return set()
        return {self._commit_cid} | self._mst_nodes | set(self._entries.values())

    def _collect_orphans(self, old_reachable: set[CID]) -> None:
        """Delete what the head move orphaned: blocks the old head reached and
        the new one does not. Scoped to this repo's own history, so a store
        shared with other repos or other processes loses nothing of theirs."""
        for cid in old_reachable - self._reachable():
            self.store.delete(cid)

    def _collect_garbage(self) -> None:
        """Sweep: delete every block the current head does not reach. Only
        for a store this repo has to itself (see :meth:`collect_garbage`)."""
        reachable = self._reachable()
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

    async def import_car(
        self,
        data: bytes,
        verify_key: PublicKey | None = None,
        *,
        gc: bool = True,
        newer_than: str | None = None,
    ) -> None:
        """Replace this repo's state with a CAR's contents (checkpoint load).

        ``newer_than`` is a rev (normally this repo's current ``rev``): a CAR
        whose head rev is not strictly greater raises :class:`StaleCheckpoint`
        before any state changes, so a router that re-hosts pushed checkpoints
        can refuse a replayed or older copy in the same parse that verifies it.

        A checkpoint load is not a commit: no ``on_commit`` fires (there is
        no honest CommitEvent to announce — no ops, no valid ``since``). A
        caller that tracks a durable head pointer must therefore pass
        ``gc=False``, persist its pointer, and then call
        :meth:`collect_garbage` — the same persist-head-before-GC ordering
        ``_commit_now`` enforces, so a crash mid-import never leaves the
        pointer naming a deleted block (see ``Identity.import_repo_car``).
        """
        if self.frozen and verify_key is None:
            raise RepoError(
                "a frozen repo takes only verified pushes: pass verify_key= (the owner's "
                "public key). With no pen of its own, this host has no other way to know "
                "the bytes are the owner's"
            )
        commit, entries, blocks = self.read_car(data, verify_key)
        if commit.did != self.did:
            raise RepoError(f"CAR is for {commit.did}, not {self.did}")
        _check_rev(commit.rev)  # refused here, before any state changes
        if newer_than is not None and commit.rev <= newer_than:
            raise StaleCheckpoint(f"checkpoint rev {commit.rev} is not newer than {newer_than}")
        async with self._write_lock:
            # the head may have moved while this import waited for the lock: a
            # checkpoint that was newer than the head the caller saw must still
            # be newer than the head there is now, or it would roll it back
            if newer_than is not None and self._commit is not None and commit.rev <= self.rev:
                raise StaleCheckpoint(
                    f"checkpoint rev {commit.rev} is not newer than the head's {self.rev}, "
                    "which moved while the import waited"
                )
            old_reachable = self._reachable()
            for cid, block in blocks.items():
                self.store.put(cid, block)  # blocks before head
            new_head = Head(commit.cid(), self.seq + 1)
            if not await self.head_store.swap(self.did, self._head, new_head):
                current = await self.head_store.load(self.did)
                if current is not None:
                    await self._reload()  # the imported blocks stay until a GC
                raise HeadMoved(current)
            self._head = new_head
            self._entries = entries
            self._commit = commit
            self._commit_cid = new_head.cid
            self._root = commit.data
            self._mst_nodes = self.mst.node_cids(commit.data)
            # only once the head is in place: the next rev must sort after it,
            # clock or no clock, and a clock shared with other repos must not
            # learn a rev from a head that was refused
            self.clock.observe(commit.rev)
            if gc:
                # what the old head reached, and what this CAR brought: a
                # checkpoint may carry blocks its own commit does not reach
                self._collect_orphans(old_reachable | set(blocks))

    def collect_garbage(self) -> None:
        """Sweep the store: drop every block the current head does not reach.

        Commits and imports already collect what they orphan, per repo. The
        sweep is for a store this repo has to itself: it also removes blocks
        a crash left staged, and what an ``import_car(gc=False)`` deferred.
        Never run it over a store shared with other repos or other processes
        on this repo; there, anything beyond the per-commit collection is the
        database's own maintenance job."""
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
