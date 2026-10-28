"""The durable stdlib-sqlite3 BlockStore.

Satisfies the synchronous BlockStore protocol; blocks survive process death so
a repo persists across restarts (the durability ladder)."""

from fastatp.cid import CID
from fastatp.lex.types import Datetime
from fastatp.lexicon import Lexicon
from fastatp.repo.repo import Repo
from fastatp.repo.store import SqliteBlockStore


def _cid(data: bytes) -> CID:
    return CID.for_raw(data)


def test_round_trip_and_protocol_surface(tmp_path):
    store = SqliteBlockStore(tmp_path / "blocks.sqlite")
    a, b = _cid(b"alpha"), _cid(b"beta")
    store.put(a, b"alpha")
    store.put(b, b"beta")

    assert store.get(a) == b"alpha"
    assert a in store and b in store
    assert store.get(_cid(b"missing")) is None
    assert len(store) == 2
    assert store.total_bytes() == len(b"alpha") + len(b"beta")
    assert set(store.cids()) == {a, b}

    store.delete(a)
    assert a not in store
    assert store.get(a) is None
    assert len(store) == 1
    store.close()


def test_persists_across_reopen(tmp_path):
    path = tmp_path / "durable.db"
    store = SqliteBlockStore(path)
    c = _cid(b"survives restart")
    store.put(c, b"survives restart")
    store.close()

    # a fresh instance on the same file (a new "process") still has the block
    reopened = SqliteBlockStore(path)
    assert reopened.get(c) == b"survives restart"
    assert len(reopened) == 1
    reopened.close()


def test_put_is_idempotent_replace(tmp_path):
    store = SqliteBlockStore(":memory:")
    c = _cid(b"x")
    store.put(c, b"x")
    store.put(c, b"x")  # INSERT OR REPLACE — no duplicate row
    assert len(store) == 1
    store.close()


def test_cids_iteration_tolerates_mutation(tmp_path):
    """cids() materializes, so present-state GC can delete while iterating."""
    store = SqliteBlockStore(":memory:")
    cids = [_cid(f"b{i}".encode()) for i in range(5)]
    for c in cids:
        store.put(c, b"x")
    for c in store.cids():  # deleting mid-iteration must not raise
        store.delete(c)
    assert len(store) == 0
    store.close()


class Note(Lexicon):
    nsid = "com.example.durablenote"
    text: str
    at: Datetime


def test_repo_uses_sqlite_store_as_backend(tmp_path):
    """A Repo drives put/commit/GC/export through the sqlite store unchanged."""
    from fastatp import crypto

    key = crypto.generate_keypair("k256")
    did = crypto.encode_did_key(key.public_key())

    repo = Repo(did, key, store=SqliteBlockStore(tmp_path / "repo.db"))
    repo.put_record(Note.nsid, "n1", Note(text="durable", at="2026-07-06T12:00:00Z"))
    car = repo.export_car()

    # a consumer imports the exported CAR and reads the record — proof the
    # store served every reachable block through commit + GC + export
    consumer = Repo(did, key)
    consumer.import_car(car)
    rec_n1 = consumer.get_record(Note.nsid, "n1")
    assert rec_n1 is not None
    assert rec_n1["text"] == "durable"
