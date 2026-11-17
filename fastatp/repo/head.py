"""The head store: a repo's current commit and firehose seq, in any database.

A repo is signed blocks plus one pointer to the newest commit. Blocks go
through :class:`~fastatp.repo.store.BlockStore`; the pointer goes through a
:class:`HeadStore`. The store is the per-repo write lock in compare-and-swap
form: a commit writes its blocks, then advances the head from the one it was
built on to the new one, and the swap fails if another writer got there
first. Nothing is locked while a commit is being signed, which matters when
the pen is a hardware signer that takes tens of milliseconds.

One store serves every repo a process hosts, keyed by DID, so several
identical processes behind one database can never fork a repo: one commit per
head wins, and the loser learns the current head and retries on it.

The head carries the firehose ``seq`` as well. A follower keeps only its own
cursor; the producer keeps the counter so that cursor still means something
after a restart. Every frame's seq is advanced through the same swap before
the frame is delivered, so a crash can leave a gap (fine: seq only has to be
monotonic) but can never hand one number to two frames.

Shipped: :class:`MemoryHeadStore` (the default, today's single-process
behaviour) and :class:`SqliteHeadStore` (one box, durable). A Postgres or
other adapter is the app's own three-method class; it is never a dependency
of this package.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from fastatp.cid import CID

__all__ = ["Head", "HeadStore", "MemoryHeadStore", "SqliteHeadStore"]


@dataclass(frozen=True)
class Head:
    """What a repo's pointer holds: the head commit and the last seq used."""

    cid: CID
    seq: int = 0


@runtime_checkable
class HeadStore(Protocol):
    """Where a repo's head lives. Implement this to put it in your database.

    ``swap`` is the whole contract: advance ``did``'s head from ``expected``
    to ``new`` in one atomic step, and say whether it took. ``expected`` is
    ``None`` for a repo that has no head yet (a genesis), and the swap must
    then fail if one exists. In SQL that is one conditional ``UPDATE`` (or an
    ``INSERT`` that tolerates a conflict) and reading the affected row count.
    """

    async def load(self, did: str) -> Head | None:
        """The current head, or ``None`` when this store has none for ``did``."""
        ...

    async def swap(self, did: str, expected: Head | None, new: Head) -> bool:
        """Move the head from ``expected`` to ``new`` only if it still is
        ``expected``; True if it took, False if the head is something else."""
        ...

    async def delete(self, did: str) -> None:
        """Forget ``did``'s head (a retired channel, a dropped repo)."""
        ...


class MemoryHeadStore:
    """Dict-backed; the default. Atomic because nothing in it awaits."""

    def __init__(self) -> None:
        self._heads: dict[str, Head] = {}

    async def load(self, did: str) -> Head | None:
        return self._heads.get(did)

    async def swap(self, did: str, expected: Head | None, new: Head) -> bool:
        if self._heads.get(did) != expected:
            return False
        self._heads[did] = new
        return True

    async def delete(self, did: str) -> None:
        self._heads.pop(did, None)


class SqliteHeadStore:
    """Durable heads in stdlib ``sqlite3``: one row per DID, the swap is one
    conditional ``UPDATE``.

    The calls are synchronous inside (microseconds on a local file) behind
    the async protocol. Several processes may open the same file: sqlite
    serializes the writers, and the conditional update decides who wins.
    Pass the same path a :class:`~fastatp.repo.store.SqliteBlockStore` uses
    to keep blocks and heads in one file.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS heads "
            "(did TEXT PRIMARY KEY, cid BLOB NOT NULL, seq INTEGER NOT NULL)"
        )
        self._db.commit()

    async def load(self, did: str) -> Head | None:
        row = self._db.execute("SELECT cid, seq FROM heads WHERE did = ?", (did,)).fetchone()
        if row is None:
            return None
        return Head(CID.decode(bytes(row[0])), int(row[1]))

    async def swap(self, did: str, expected: Head | None, new: Head) -> bool:
        if expected is None:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO heads (did, cid, seq) VALUES (?, ?, ?)",
                (did, bytes(new.cid), new.seq),
            )
        else:
            cursor = self._db.execute(
                "UPDATE heads SET cid = ?, seq = ? WHERE did = ? AND cid = ? AND seq = ?",
                (bytes(new.cid), new.seq, did, bytes(expected.cid), expected.seq),
            )
        self._db.commit()
        return cursor.rowcount == 1

    async def delete(self, did: str) -> None:
        self._db.execute("DELETE FROM heads WHERE did = ?", (did,))
        self._db.commit()

    def close(self) -> None:
        self._db.close()
