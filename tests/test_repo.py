"""Repo semantics: signed commits, CAR round-trip, present-state GC,
and steady-state size under sustained FIFO churn."""

import pytest

from fastatp import crypto, dagcbor
from fastatp.exceptions import RepoError
from fastatp.lex.types import Datetime
from fastatp.lexicon import Lexicon
from fastatp.repo.car import encode_car
from fastatp.repo.commit import Commit
from fastatp.repo.repo import Repo, WriteOp
from fastatp.repo.store import MemoryBlockStore
from fastatp.tid import decode_tid

DID = "did:web:repo.example"


class Telemetry(Lexicon):
    nsid = "com.example.telemetry.reading"
    seq: int
    at: Datetime


class Config(Lexicon):
    nsid = "com.example.config.setting"
    name: str
    value: str


def _reading(i: int) -> Telemetry:
    return Telemetry(seq=i, at="2026-07-02T12:00:00Z")


@pytest.fixture
def repo():
    return Repo(DID, crypto.generate_keypair("k256"))


def test_genesis_commit_signed(repo):
    assert repo.record_count() == 0
    assert repo.commit.verify(repo.signing_key.public_key())
    assert repo.commit.prev is None
    decode_tid(repo.rev)  # rev is a real TID


def test_put_get_delete_round_trip(repo):
    event = repo.put_record("com.example.telemetry.reading", "3jzfcijpj2z2a", _reading(1))
    assert event.ops == [
        {
            "action": "create",
            "path": "com.example.telemetry.reading/3jzfcijpj2z2a",
            "cid": event.ops[0]["cid"],
        }
    ]
    got = repo.get_record("com.example.telemetry.reading", "3jzfcijpj2z2a")
    assert got["seq"] == 1 and got["$type"] == Telemetry.nsid

    event2 = repo.put_record("com.example.telemetry.reading", "3jzfcijpj2z2a", _reading(2))
    assert event2.ops[0]["action"] == "update"
    assert event2.ops[0]["prev"] == event.ops[0]["cid"]
    assert repo.get_record("com.example.telemetry.reading", "3jzfcijpj2z2a")["seq"] == 2

    event3 = repo.delete_record("com.example.telemetry.reading", "3jzfcijpj2z2a")
    assert event3.ops[0]["action"] == "delete"
    assert repo.get_record("com.example.telemetry.reading", "3jzfcijpj2z2a") is None
    # rev clock advances monotonically
    assert event.rev < event2.rev < event3.rev
    assert event2.since == event.rev


def test_write_validation(repo):
    with pytest.raises(RepoError):
        repo.apply_writes([])
    with pytest.raises(RepoError):
        repo.delete_record("com.example.telemetry.reading", "missing")
    with pytest.raises(RepoError):
        repo.apply_writes([WriteOp("create", "com.example.telemetry.reading", "k", None)])
    with pytest.raises(RepoError):
        repo.apply_writes(
            [WriteOp("frobnicate", "com.example.telemetry.reading", "k", _reading(1))]
        )
    repo.put_record("com.example.telemetry.reading", "dup", _reading(1))
    with pytest.raises(RepoError):
        repo.apply_writes([WriteOp("create", "com.example.telemetry.reading", "dup", _reading(2))])
    with pytest.raises(ValueError):
        repo.put_record("not-an-nsid", "k", _reading(1))


def test_multi_op_commit(repo):
    repo.put_record("com.example.config.setting", "alpha", Config(name="a", value="1"))
    event = repo.apply_writes(
        [
            WriteOp("create", "com.example.telemetry.reading", "r1", _reading(1)),
            WriteOp("create", "com.example.telemetry.reading", "r2", _reading(2)),
            WriteOp("delete", "com.example.config.setting", "alpha"),
        ]
    )
    assert [op["action"] for op in event.ops] == ["create", "create", "delete"]
    assert repo.record_count() == 2
    assert repo.record_count("com.example.config.setting") == 0


def test_car_export_import_lossless(repo):
    key = repo.signing_key
    repo.put_record("com.example.config.setting", "alpha", Config(name="a", value="1"))
    repo.put_record("com.example.telemetry.reading", "r1", _reading(7))
    car = repo.export_car()

    commit, entries, _blocks = Repo.read_car(car, verify_key=key.public_key())
    assert commit.did == DID
    assert set(entries) == {
        "com.example.config.setting/alpha",
        "com.example.telemetry.reading/r1",
    }

    clone = Repo(DID, key)
    clone.import_car(car, verify_key=key.public_key())
    assert clone.rev == repo.rev
    assert clone.root == repo.root
    rec_r1 = clone.get_record("com.example.telemetry.reading", "r1")
    assert rec_r1 is not None
    assert rec_r1["seq"] == 7
    # a clone loaded from CAR can keep writing
    clone.put_record("com.example.telemetry.reading", "r2", _reading(8))
    assert clone.record_count("com.example.telemetry.reading") == 2

    # tampered CAR fails signature verification
    other = crypto.generate_keypair("k256")
    with pytest.raises(RepoError):
        Repo.read_car(car, verify_key=other.public_key())


def test_commit_signature_tamper_detected(repo):
    repo.put_record("com.example.telemetry.reading", "r1", _reading(1))
    commit = repo.commit
    assert commit.verify(repo.signing_key.public_key())
    forged = Commit(did=commit.did, data=commit.data, rev=commit.rev, sig=bytes(64), prev=None)
    assert not forged.verify(repo.signing_key.public_key())


def test_no_accreted_history_and_gc(repo):
    """Present-state by construction: no prev chain, orphans reclaimed."""
    store: MemoryBlockStore = repo.store
    repo.put_record("com.example.telemetry.reading", "r1", _reading(1))
    first_commit_cid = repo.commit_cid
    repo.put_record("com.example.telemetry.reading", "r1", _reading(2))
    assert repo.commit.prev is None
    # the old commit block is gone from the store (no walkable history)
    assert store.get(first_commit_cid) is None


def test_steady_state_size_under_fifo_churn():
    """Sustained FIFO churn on one collection holds size steady
    while a durable collection in the same repo is untouched."""
    repo = Repo(DID, crypto.generate_keypair("k256"))
    store = repo.store
    # Repo defaults to a MemoryBlockStore; narrow for total_bytes() below
    assert isinstance(store, MemoryBlockStore)

    # durable config collection: written once, never deleted
    for i in range(5):
        repo.put_record("com.example.config.setting", f"cfg{i}", Config(name=f"n{i}", value="v"))
    config_records = {
        rkey: repo.get_record("com.example.config.setting", rkey)
        for rkey, _ in repo.list_records("com.example.config.setting")
    }

    window = 20
    live: list[str] = []
    sizes: list[int] = []
    counts: list[int] = []
    for i in range(300):
        rkey = f"t{i:06d}"
        # app-level FIFO retention policy on the telemetry collection only
        writes = [WriteOp("create", "com.example.telemetry.reading", rkey, _reading(i))]
        live.append(rkey)
        if len(live) > window:
            oldest = live.pop(0)
            writes.append(WriteOp("delete", "com.example.telemetry.reading", oldest))
        repo.apply_writes(writes)
        if i >= window:
            sizes.append(store.total_bytes())
            counts.append(len(store))

    assert repo.record_count("com.example.telemetry.reading") == window
    # size is steady-state: bounded by the live window (25 records + MST +
    # 1 commit), never by the 300 total writes, and shows no growth trend
    assert max(counts) <= (window + 5) * 3
    assert max(counts[-100:]) <= max(counts[:100])
    assert max(sizes[-100:]) <= max(sizes[:100]) * 1.05
    # the durable collection is untouched by telemetry churn
    for rkey, expected in config_records.items():
        assert repo.get_record("com.example.config.setting", rkey) == expected


def test_intra_batch_duplicate_paths_rejected(repo):
    """Two creates for one path in a single batch is a protocol error; a
    delete-then-create of the same path in one batch is legitimate."""
    with pytest.raises(RepoError, match="already exists"):
        repo.apply_writes(
            [
                WriteOp("create", "com.example.telemetry.reading", "dup", _reading(1)),
                WriteOp("create", "com.example.telemetry.reading", "dup", _reading(2)),
            ]
        )
    assert repo.record_count() == 0  # nothing half-applied

    repo.put_record("com.example.telemetry.reading", "swap", _reading(1))
    repo.apply_writes(
        [
            WriteOp("delete", "com.example.telemetry.reading", "swap"),
            WriteOp("create", "com.example.telemetry.reading", "swap", _reading(2)),
        ]
    )
    assert repo.get_record("com.example.telemetry.reading", "swap")["seq"] == 2
    with pytest.raises(RepoError, match="no record"):
        repo.apply_writes(
            [
                WriteOp("delete", "com.example.telemetry.reading", "swap"),
                WriteOp("update", "com.example.telemetry.reading", "swap", _reading(3)),
            ]
        )


def test_read_car_malformed_blocks_raise_repo_error(repo):
    """Hash-valid but CBOR-invalid commit/MST blocks stay inside RepoError."""
    from fastatp.cid import CID
    from fastatp.repo.car import encode_car

    junk = b"\x18\x17"  # non-minimal int: hash-valid block, invalid DAG-CBOR
    cid = CID.for_dag_cbor(junk)
    with pytest.raises(RepoError, match="DAG-CBOR"):
        Repo.read_car(encode_car([cid], {cid: junk}))


def test_restored_repo_writes_after_its_head_even_with_the_clock_behind(monkeypatch):
    """A CAR (or a durable head) written on a machine with a faster clock
    must not make this machine's next commit sort BEFORE it: the clock
    observes the restored rev. Mirrors the Rust core's RevClock::observe."""
    import time

    from fastatp.tid import TidClock

    key = crypto.generate_keypair("k256")
    source = Repo("did:web:clock.example", key)
    source.put_record("com.example.note", "n-1", {"$type": "com.example.note", "text": "ahead"})
    head_rev = source.rev
    car = source.export_car()

    monkeypatch.setattr(time, "time_ns", lambda: 1_000_000)  # 1970-01-01, far behind the head
    restored = Repo("did:web:clock.example", key, clock=TidClock())
    restored.import_car(car, verify_key=key.public_key())
    restored.put_record("com.example.note", "n-2", {"$type": "com.example.note", "text": "late"})
    assert restored.rev > head_rev, f"{restored.rev} must sort after {head_rev}"

    # the same for a repo reopened from a durable store at a stored head
    store = restored.store
    reopened = Repo(
        "did:web:clock.example", key, store=store, head_cid=restored.commit_cid, clock=TidClock()
    )
    reopened.put_record("com.example.note", "n-3", {"$type": "com.example.note", "text": "later"})
    assert reopened.rev > restored.rev


def test_a_car_whose_head_rev_is_not_a_tid_is_refused_before_any_state_changes():
    """``import_car`` observes the head rev on the clock. A rev that is not a
    TID is refused up front, with the repo exactly as it was — not after the
    blocks and head have already been replaced."""
    key = crypto.generate_keypair("k256")
    source = Repo("did:web:rev.example", key)
    source.put_record("com.example.note", "n-1", {"$type": "com.example.note", "text": "x"})
    commit, _entries, blocks = Repo.read_car(source.export_car())
    # Commit.create validates the rev, but a CAR is decoded with from_map,
    # which takes any string: build the block the way a hostile host would
    unsigned = {
        "did": commit.did,
        "rev": "not-a-tid",
        "data": commit.data,
        "prev": None,
        "version": 3,
    }
    forged = Commit(
        did=commit.did,
        data=commit.data,
        rev="not-a-tid",
        sig=crypto.sign_message(key, dagcbor.encode(unsigned)),
    )
    blocks[forged.cid()] = forged.to_dag_cbor()
    car = encode_car([forged.cid()], blocks)

    target = Repo("did:web:rev.example", key)
    target.put_record("com.example.note", "mine", {"$type": "com.example.note", "text": "keep"})
    before = (target.commit_cid, target.rev, dict(target._entries))
    with pytest.raises(RepoError, match="not a TID"):
        target.import_car(car, verify_key=key.public_key())
    assert (target.commit_cid, target.rev, dict(target._entries)) == before
    assert target.get_record("com.example.note", "mine") is not None


def test_a_refused_head_never_advances_a_shared_clock():
    """A head with a valid, far-future rev but a malformed MST fails to load;
    the clock it was handed must not have learned that rev, or every later
    commit on a repo sharing the clock would carry a future timestamp."""
    from fastatp.tid import TidClock, encode_tid

    key = crypto.generate_keypair("k256")
    store = MemoryBlockStore()
    future = encode_tid(4_000_000_000_000_000, 7)  # ~2096
    unsigned = {
        "did": "did:web:rev.example",
        "rev": future,
        "data": None,
        "prev": None,
        "version": 3,
    }
    bogus = Commit(
        did="did:web:rev.example",
        data=None,
        rev=future,
        sig=crypto.sign_message(key, dagcbor.encode(unsigned)),
    )  # type: ignore[arg-type]
    block = bogus.to_dag_cbor()
    store.put(bogus.cid(), block)
    shared = TidClock()
    with pytest.raises(RepoError):
        Repo("did:web:rev.example", key, store=store, head_cid=bogus.cid(), clock=shared)
    assert shared.now() < future, "the refused head's rev leaked into the shared clock"
