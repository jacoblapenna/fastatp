"""One place for everything a peer keeps: blocks, heads and blobs.

A deployment picks one database. Blocks (the repo's content), heads (its
pointer and firehose seq) and blobs (its media) belong together: the
compare-and-swap on a head only protects a repo whose blocks every process
can see, so splitting them across databases would protect nothing. The three
store protocols stay separate because each is a small thing to implement;
:class:`Storage` is the one decision a builder makes.

Two bundles ship. :class:`SqliteStorage` is the built-in: a directory with
``blocks.sqlite`` (blocks and heads in one file) and ``blobs.sqlite``, what an
``Identity`` on a ``state_dir`` uses without being asked. :class:`MemoryStorage`
is for ephemeral peers and tests. Bringing your own database is a class with
the same three attributes over your own adapters; nothing in this package
depends on a database driver.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from fastatp.repo.blobs import BlobStore, MemoryBlobStore, SqliteBlobStore
from fastatp.repo.head import HeadStore, MemoryHeadStore, SqliteHeadStore
from fastatp.repo.store import BlockStore, MemoryBlockStore, SqliteBlockStore

__all__ = ["BLOBS_FILE", "BLOCKS_FILE", "MemoryStorage", "SqliteStorage", "Storage"]

BLOCKS_FILE = "blocks.sqlite"
BLOBS_FILE = "blobs.sqlite"


@runtime_checkable
class Storage(Protocol):
    """Everything a peer's repos keep, in one database of the builder's choice."""

    @property
    def blocks(self) -> BlockStore: ...

    @property
    def heads(self) -> HeadStore: ...

    @property
    def blobs(self) -> BlobStore | None:
        """Media, when the peer serves any; ``None`` for a media-free peer."""
        ...


class MemoryStorage:
    """Everything in process memory: ephemeral peers, tests."""

    def __init__(self, *, blobs: bool = False) -> None:
        self.blocks: BlockStore = MemoryBlockStore()
        self.heads: HeadStore = MemoryHeadStore()
        self.blobs: BlobStore | None = MemoryBlobStore() if blobs else None


class SqliteStorage:
    """The built-in durable storage: a directory holding ``blocks.sqlite``
    (blocks and heads, one file, so a consistent copy of it is the whole repo)
    and ``blobs.sqlite`` (created when first used). Several processes may
    open the same directory; sqlite serializes the writers and the head
    swap decides who wins."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.blocks: BlockStore = SqliteBlockStore(self.directory / BLOCKS_FILE)
        self.heads: HeadStore = SqliteHeadStore(self.directory / BLOCKS_FILE)
        self._blobs: SqliteBlobStore | None = None

    @property
    def blobs(self) -> BlobStore:
        if self._blobs is None:
            self._blobs = SqliteBlobStore(self.directory / BLOBS_FILE)
        return self._blobs

    def close(self) -> None:
        for store in (self.blocks, self.heads, self._blobs):
            close = getattr(store, "close", None)
            if close is not None:
                close()
