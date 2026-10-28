"""Content-addressed block storage for repos.

The store holds ``CID -> bytes`` blocks. Present-state repos garbage-collect
after every commit: blocks unreachable from the current root are deleted, so
on-disk size tracks current records, never total writes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from fastatp.cid import CID


class BlockStore(Protocol):
    def get(self, cid: CID) -> bytes | None: ...

    def put(self, cid: CID, data: bytes) -> None: ...

    def delete(self, cid: CID) -> None: ...

    def __contains__(self, cid: CID) -> bool: ...

    def cids(self) -> Iterator[CID]:
        """May be a live iterator; callers materialize (list()) before mutating."""
        ...

    def __len__(self) -> int: ...


class MemoryBlockStore:
    """Dict-backed block store (the default; swap in your own persistence)."""

    def __init__(self) -> None:
        self._blocks: dict[CID, bytes] = {}

    def get(self, cid: CID) -> bytes | None:
        return self._blocks.get(cid)

    def put(self, cid: CID, data: bytes) -> None:
        self._blocks[cid] = data

    def delete(self, cid: CID) -> None:
        self._blocks.pop(cid, None)

    def __contains__(self, cid: CID) -> bool:
        return cid in self._blocks

    def cids(self) -> Iterator[CID]:
        return iter(list(self._blocks.keys()))

    def __len__(self) -> int:
        return len(self._blocks)

    def total_bytes(self) -> int:
        return sum(len(b) for b in self._blocks.values())


class SqliteBlockStore:
    """Durable block store backed by stdlib ``sqlite3`` (synchronous).

    Satisfies the synchronous :class:`BlockStore` protocol exactly — ``Repo``,
    ``PeerServer``, and the reconciler use it unchanged (no async). Blocks
    survive process death, so an identity's repo persists across restarts
    (durability ladder; a repo sits *below* the identity line — its
    loss means re-grant/re-post, not identity death).

    The key is the binary CID (``bytes(cid)``); the value is the block. ``WAL``
    journaling is enabled so a crash mid-write cannot corrupt the file. Pass
    ``":memory:"`` for a transient store (tests).
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        # check_same_thread=False: the store is used synchronously from within a
        # single event loop; commits are serialized by the sync call sites.
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS blocks (cid BLOB PRIMARY KEY, data BLOB NOT NULL)"
        )
        self._db.commit()

    def get(self, cid: CID) -> bytes | None:
        row = self._db.execute("SELECT data FROM blocks WHERE cid = ?", (bytes(cid),)).fetchone()
        return bytes(row[0]) if row is not None else None

    def put(self, cid: CID, data: bytes) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO blocks (cid, data) VALUES (?, ?)", (bytes(cid), data)
        )
        self._db.commit()

    def delete(self, cid: CID) -> None:
        self._db.execute("DELETE FROM blocks WHERE cid = ?", (bytes(cid),))
        self._db.commit()

    def __contains__(self, cid: CID) -> bool:
        row = self._db.execute("SELECT 1 FROM blocks WHERE cid = ?", (bytes(cid),)).fetchone()
        return row is not None

    def cids(self) -> Iterator[CID]:
        # materialize before yielding so callers can mutate the store while
        # iterating (present-state GC deletes during a cids() sweep)
        rows = self._db.execute("SELECT cid FROM blocks").fetchall()
        return (CID.decode(bytes(row[0])) for row in rows)

    def __len__(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM blocks").fetchone()[0])

    def total_bytes(self) -> int:
        row = self._db.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM blocks").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._db.close()
