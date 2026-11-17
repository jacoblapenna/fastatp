"""The head store: a repo's pointer and seq as one compare-and-swap."""

import pytest

from fastatp.cid import CID
from fastatp.repo.head import Head, HeadStore, MemoryHeadStore, SqliteHeadStore

DID = "did:web:heads.example"


def _cid(label: str) -> CID:
    return CID.for_raw(label.encode())


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryHeadStore()
    return SqliteHeadStore(tmp_path / "heads.sqlite")


async def test_a_head_is_created_advanced_and_forgotten(store):
    assert isinstance(store, HeadStore)
    assert await store.load(DID) is None
    genesis = Head(_cid("genesis"), 1)
    assert await store.swap(DID, None, genesis)
    assert await store.load(DID) == genesis

    # a commit moves the cid and the seq together
    second = Head(_cid("second"), 2)
    assert await store.swap(DID, genesis, second)
    # a non-commit frame moves only the seq
    announced = Head(_cid("second"), 3)
    assert await store.swap(DID, second, announced)
    assert await store.load(DID) == announced

    await store.delete(DID)
    assert await store.load(DID) is None
    await store.delete(DID)  # idempotent


async def test_a_swap_from_a_stale_head_does_not_take(store):
    genesis = Head(_cid("genesis"), 1)
    await store.swap(DID, None, genesis)
    assert not await store.swap(DID, None, Head(_cid("other-genesis"), 1))  # already exists
    second = Head(_cid("second"), 2)
    assert await store.swap(DID, genesis, second)
    assert not await store.swap(DID, genesis, Head(_cid("fork"), 2))  # built on the old head
    assert not await store.swap(DID, Head(_cid("second"), 1), Head(_cid("x"), 3))  # wrong seq
    assert await store.load(DID) == second


async def test_heads_are_per_did(store):
    a, b = "did:web:a.example", "did:web:b.example"
    assert await store.swap(a, None, Head(_cid("a1"), 1))
    assert await store.load(b) is None
    assert await store.swap(b, None, Head(_cid("b1"), 7))
    assert (await store.load(a)).seq == 1 and (await store.load(b)).seq == 7


async def test_a_swap_from_a_stale_snapshot_loses_on_one_sqlite_file(tmp_path):
    """Two connections, as two processes would have, read the same head; the
    second to swap holds a stale snapshot and the conditional UPDATE refuses
    it. (The swaps here run one after the other; what this pins is that the
    WHERE clause, not timing, decides. Overlapping writers are sqlite's own
    serialization and the same clause.)"""
    path = tmp_path / "heads.sqlite"
    one, two = SqliteHeadStore(path), SqliteHeadStore(path)
    genesis = Head(_cid("genesis"), 1)
    assert await one.swap(DID, None, genesis)
    assert await two.load(DID) == genesis  # the other process sees it

    seen_by_one = await one.load(DID)
    seen_by_two = await two.load(DID)
    first = await one.swap(DID, seen_by_one, Head(_cid("from-one"), 2))
    second = await two.swap(DID, seen_by_two, Head(_cid("from-two"), 2))
    assert [first, second] == [True, False]
    assert (await two.load(DID)).cid == _cid("from-one")

    # the loser reloads and advances from the winner's head
    assert await two.swap(DID, await two.load(DID), Head(_cid("from-two"), 3))
    one.close(), two.close()


async def test_a_sqlite_head_survives_reopening(tmp_path):
    path = tmp_path / "heads.sqlite"
    store = SqliteHeadStore(path)
    await store.swap(DID, None, Head(_cid("genesis"), 5))
    store.close()
    assert await SqliteHeadStore(path).load(DID) == Head(_cid("genesis"), 5)
