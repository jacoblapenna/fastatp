"""One storage decision: the builder's database, or the built-in sqlite."""

import pytest

from fastatp import Identity, MemoryStorage, Peer, PeerServer, Repo, SqliteStorage, Storage, crypto
from fastatp.repo.head import Head, MemoryHeadStore, SqliteHeadStore
from fastatp.repo.store import SqliteBlockStore

NOTE = "com.example.note"


class _TheirDatabase:
    """What bringing your own database looks like: a class with the three
    stores over your own adapters. Memory stands in for Postgres here."""

    def __init__(self) -> None:
        self.blocks = SqliteBlockStore(":memory:")  # any BlockStore
        self.heads = MemoryHeadStore()  # any HeadStore
        self.blobs = None


async def test_sqlite_storage_is_one_directory_with_blocks_and_heads_in_one_file(tmp_path):
    storage = SqliteStorage(tmp_path / "state")
    assert isinstance(storage, Storage)
    key = crypto.generate_keypair("k256")
    repo = await Repo.create("did:web:one.example", key, storage=storage)
    await repo.put_record(NOTE, "n", {"$type": NOTE, "text": "kept"})
    assert (tmp_path / "state" / "blocks.sqlite").exists()
    assert (tmp_path / "state" / "blobs.sqlite").exists()  # a repo on it is media-capable
    assert repo.blobs is storage.blobs

    again = SqliteStorage(tmp_path / "state")
    reopened = await Repo.open("did:web:one.example", key, storage=again)
    assert reopened.get_record(NOTE, "n") == {"$type": NOTE, "text": "kept"}
    assert reopened.seq == 1
    storage.close()
    again.close()


async def test_storage_and_explicit_stores_are_exclusive():
    key = crypto.generate_keypair("k256")
    with pytest.raises(ValueError, match="not both"):
        Repo("did:web:x.example", key, store=SqliteBlockStore(":memory:"), storage=MemoryStorage())
    with pytest.raises(ValueError, match="needs a head_store= or a storage="):
        await Repo.open("did:web:x.example", key)


async def test_an_identity_lives_in_the_builders_database_when_one_is_given(tmp_path):
    """BYODB: the repo, its head and seq go to the builder's storage; the
    state_dir keeps only the small files, and no sqlite is created."""
    sdir = tmp_path / "id"
    theirs = _TheirDatabase()
    ident = await Identity.bootstrap(domain="byo.example", state_dir=sdir, storage=theirs)
    assert ident.storage is theirs and ident.store is theirs.blocks
    await ident.repo.put_record(NOTE, "n", {"$type": NOTE, "text": "in postgres"})
    assert not (sdir / "blocks.sqlite").exists()
    assert sorted(p.name for p in sdir.iterdir()) == ["chain.json", "identity.json", "signing.key"]
    assert (await theirs.heads.load(ident.did)) == Head(ident.repo.commit_cid, 1)

    # every boot brings the same storage back, like the signer
    loaded = await Identity.bootstrap(domain="byo.example", state_dir=sdir, storage=theirs)
    assert loaded.repo.commit_cid == ident.repo.commit_cid and loaded.repo.seq == 1
    assert loaded.repo.get_record(NOTE, "n") == {"$type": NOTE, "text": "in postgres"}
    assert not (sdir / "blocks.sqlite").exists()

    # adopt and did:key take it the same way
    adopted = await Identity.adopt_web(
        "adopt.example", crypto.generate_keypair("k256"), tmp_path / "adopt", storage=theirs
    )
    assert adopted.storage is theirs
    device = await Identity.bootstrap(state_dir=tmp_path / "dev", storage=theirs)
    assert device.storage is theirs and not (tmp_path / "dev" / "blocks.sqlite").exists()


async def test_a_state_dir_alone_means_the_built_in_sqlite(tmp_path):
    sdir = tmp_path / "id"
    ident = await Identity.bootstrap(domain="native.example", state_dir=sdir)
    assert isinstance(ident.storage, SqliteStorage)
    assert isinstance(ident.repo.head_store, SqliteHeadStore)
    assert (sdir / "blocks.sqlite").exists()
    ephemeral = await Identity.bootstrap()
    assert isinstance(ephemeral.storage, MemoryStorage)


async def test_a_server_over_a_storage(tmp_path):
    key = crypto.generate_keypair("k256")
    storage = SqliteStorage(tmp_path / "srv")
    srv = await PeerServer.create(Peer(did="did:web:srv.example", signing_key=key), storage=storage)
    await srv.put_record(NOTE, "n", {"$type": NOTE, "text": "served"})
    assert srv.repo.head_store is storage.heads and srv.repo.store is storage.blocks
    assert (await storage.heads.load("did:web:srv.example")).seq == srv.seq
    storage.close()
