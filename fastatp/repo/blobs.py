"""Blob storage: the repo's optional media capability.

Blobs are part of the atproto repository model (data-model spec ``blob``
type; ``com.atproto.sync.getBlob`` / ``listBlobs``) but ride OUTSIDE the
MST: records embed a typed reference (``$type: blob`` with a raw-codec CID),
the bytes live here. Media-free peers pay nothing — a repo has no blob store
unless one is wired in (``Repo(blobs=...)``).

Blob CIDs are **raw codec (0x55) + sha-256** (``CID.for_raw``), never
dag-cbor. Each entry also carries the blob's ``mimeType`` (so ``getBlob``
serves the original content type) and the repo head ``rev`` at add time (so
``listBlobs?since=`` can answer).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fastatp.cid import CID


@dataclass(frozen=True)
class BlobInfo:
    """Metadata for one stored blob (the bytes live behind ``get``)."""

    cid: CID
    size: int
    mime_type: str | None
    rev: str | None  # repo head rev when the blob was added


class BlobStore(Protocol):
    def get(self, cid: CID) -> bytes | None: ...

    def info(self, cid: CID) -> BlobInfo | None: ...

    def put(
        self, cid: CID, data: bytes, *, mime_type: str | None = None, rev: str | None = None
    ) -> None: ...

    def delete(self, cid: CID) -> None: ...

    def __contains__(self, cid: CID) -> bool: ...

    def infos(self) -> Iterator[BlobInfo]:
        """May be a live iterator; callers materialize (list()) before mutating."""
        ...

    def __len__(self) -> int: ...


class MemoryBlobStore:
    """Dict-backed blob store (the default when a test wires one in)."""

    def __init__(self) -> None:
        self._blobs: dict[CID, tuple[bytes, str | None, str | None]] = {}

    def get(self, cid: CID) -> bytes | None:
        entry = self._blobs.get(cid)
        return entry[0] if entry is not None else None

    def info(self, cid: CID) -> BlobInfo | None:
        entry = self._blobs.get(cid)
        if entry is None:
            return None
        data, mime, rev = entry
        return BlobInfo(cid=cid, size=len(data), mime_type=mime, rev=rev)

    def put(
        self, cid: CID, data: bytes, *, mime_type: str | None = None, rev: str | None = None
    ) -> None:
        self._blobs[cid] = (data, mime_type, rev)

    def delete(self, cid: CID) -> None:
        self._blobs.pop(cid, None)

    def __contains__(self, cid: CID) -> bool:
        return cid in self._blobs

    def infos(self) -> Iterator[BlobInfo]:
        return iter(
            [
                BlobInfo(cid=cid, size=len(data), mime_type=mime, rev=rev)
                for cid, (data, mime, rev) in self._blobs.items()
            ]
        )

    def __len__(self) -> int:
        return len(self._blobs)

    def total_bytes(self) -> int:
        return sum(len(entry[0]) for entry in self._blobs.values())


class SqliteBlobStore:
    """Durable blob store backed by stdlib ``sqlite3`` (synchronous).

    Same shape and rationale as :class:`~fastatp.repo.store.SqliteBlockStore`:
    binary-CID key, WAL journaling, ``":memory:"`` for transient stores.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS blobs ("
            "cid BLOB PRIMARY KEY, data BLOB NOT NULL, mime TEXT, rev TEXT)"
        )
        self._db.commit()

    def get(self, cid: CID) -> bytes | None:
        row = self._db.execute("SELECT data FROM blobs WHERE cid = ?", (bytes(cid),)).fetchone()
        return bytes(row[0]) if row is not None else None

    def info(self, cid: CID) -> BlobInfo | None:
        row = self._db.execute(
            "SELECT LENGTH(data), mime, rev FROM blobs WHERE cid = ?", (bytes(cid),)
        ).fetchone()
        if row is None:
            return None
        return BlobInfo(cid=cid, size=int(row[0]), mime_type=row[1], rev=row[2])

    def put(
        self, cid: CID, data: bytes, *, mime_type: str | None = None, rev: str | None = None
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO blobs (cid, data, mime, rev) VALUES (?, ?, ?, ?)",
            (bytes(cid), data, mime_type, rev),
        )
        self._db.commit()

    def delete(self, cid: CID) -> None:
        self._db.execute("DELETE FROM blobs WHERE cid = ?", (bytes(cid),))
        self._db.commit()

    def __contains__(self, cid: CID) -> bool:
        row = self._db.execute("SELECT 1 FROM blobs WHERE cid = ?", (bytes(cid),)).fetchone()
        return row is not None

    def infos(self) -> Iterator[BlobInfo]:
        rows = self._db.execute("SELECT cid, LENGTH(data), mime, rev FROM blobs").fetchall()
        return (
            BlobInfo(cid=CID.decode(bytes(r[0])), size=int(r[1]), mime_type=r[2], rev=r[3])
            for r in rows
        )

    def __len__(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM blobs").fetchone()[0])

    def total_bytes(self) -> int:
        row = self._db.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM blobs").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._db.close()
