"""MST conformance: interop vectors (common prefix, key heights) and the
commit-proof fixtures' known root CIDs."""

import json

import pytest

from fastatp.cid import CID
from fastatp.repo.mst import (
    MST,
    common_prefix_len,
    key_height,
    validate_mst_key,
)
from fastatp.repo.store import MemoryBlockStore


def _load(interop_dir, sub, name):
    return json.loads((interop_dir / sub / name).read_text())


def test_common_prefix_vectors(interop_dir):
    for case in _load(interop_dir, "mst", "common_prefix.json"):
        assert common_prefix_len(case["left"], case["right"]) == case["len"], case


def test_key_height_vectors(interop_dir):
    for case in _load(interop_dir, "mst", "key_heights.json"):
        assert key_height(case["key"]) == case["height"], case


def test_empty_tree_root():
    store = MemoryBlockStore()
    mst = MST(store)
    root = mst.build({})
    # the canonical empty MST node CID (same across implementations)
    assert root.encode() == "bafyreie5737gdxlw5i64vzichcalba3z2v5n6icifvx5xytvske7mr3hpm"


def test_commit_proof_roots(interop_dir):
    """rootBeforeCommit/rootAfterCommit are exact oracles for tree shape."""
    fixtures = _load(interop_dir, "firehose", "commit-proof-fixtures.json")
    assert len(fixtures) >= 5
    for fx in fixtures:
        leaf = CID.decode(fx["leafValue"])
        store = MemoryBlockStore()
        mst = MST(store)
        entries = dict.fromkeys(fx["keys"], leaf)
        root_before = mst.build(entries)
        assert root_before.encode() == fx["rootBeforeCommit"], fx["comment"]

        for key in fx["dels"]:
            del entries[key]
        for key in fx["adds"]:
            entries[key] = leaf
        root_after = mst.build(entries)
        assert root_after.encode() == fx["rootAfterCommit"], fx["comment"]

        # reading the tree back from blocks reproduces the map
        assert mst.load(root_after) == entries, fx["comment"]


def test_example_keys_build_and_read(interop_dir):
    keys = [
        line
        for line in (interop_dir / "mst" / "example_keys.txt").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert len(keys) > 20
    store = MemoryBlockStore()
    mst = MST(store)
    value = CID.for_raw(b"value")
    entries = dict.fromkeys(keys, value)
    root = mst.build(entries)
    assert mst.load(root) == entries
    # deterministic: insertion order does not matter
    root2 = MST(MemoryBlockStore()).build(dict.fromkeys(reversed(keys), value))
    assert root2 == root


def test_key_validation():
    validate_mst_key("app.bsky.feed.post/454397e440ec")
    for bad in ("", "nokeypart", "/leading", "trailing/", "a//b", "col/rkey/extra", "x" * 300):
        with pytest.raises(ValueError):
            validate_mst_key(bad)


def test_negative_prefix_len_rejected():
    """A crafted node with p < 0 must fail shape validation, not silently
    mis-slice the previous key."""
    from fastatp import dagcbor
    from fastatp.cid import CID
    from fastatp.exceptions import RepoError

    value = CID.for_raw(b"v")
    node = dagcbor.encode({"e": [{"k": b"x", "p": -1, "t": None, "v": value}], "l": None})
    cid = CID.for_dag_cbor(node)
    store = MemoryBlockStore()
    store.put(cid, node)
    with pytest.raises(RepoError, match="malformed MST entry"):
        MST(store).load(cid)


# --- covering proofs + operation inversion ------------------------------------


def test_commit_proof_covering_and_inversion(interop_dir):
    """The interop firehose fixtures are the oracle for both halves: the
    covering proof must contain every block the reference implementation
    proves with, and inverting the ops over ONLY those blocks must re-derive
    rootBeforeCommit."""
    fixtures = _load(interop_dir, "firehose", "commit-proof-fixtures.json")
    for fx in fixtures:
        leaf = CID.decode(fx["leafValue"])
        store = MemoryBlockStore()
        mst = MST(store)
        entries = dict.fromkeys(fx["keys"], leaf)
        root_before = mst.build(entries)
        after = dict(entries)
        for key in fx["dels"]:
            del after[key]
        for key in fx["adds"]:
            after[key] = leaf
        root_after = mst.build(after)

        proof: set[CID] = set()
        for key in [*fx["adds"], *fx["dels"]]:
            proof |= mst.covering_proof(root_after, key)
        expected = {CID.decode(c) for c in fx["blocksInProof"]}
        assert expected <= proof, fx["comment"]

        # inversion over a store holding ONLY the covering-proof blocks
        proof_store = MemoryBlockStore()
        for cid in proof:
            block = store.get(cid)
            assert block is not None
            proof_store.put(cid, block)
        changes = [("create", k, leaf, None) for k in fx["adds"]] + [
            ("delete", k, None, leaf) for k in fx["dels"]
        ]
        inverted = MST(proof_store).invert_changes(root_after, changes)
        assert inverted == root_before, fx["comment"]


def test_frontier_round_trip(interop_dir):
    """flatten_frontier + build_frontier over a fully-present tree is the
    identity, and over a proof slice reproduces the same root without the
    missing blocks."""
    keys = [
        line
        for line in (interop_dir / "mst" / "example_keys.txt").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    value = CID.for_raw(b"value")
    store = MemoryBlockStore()
    mst = MST(store)
    root = mst.build(dict.fromkeys(keys, value))
    assert mst.build_frontier(mst.flatten_frontier(root)) == root

    partial_store = MemoryBlockStore()
    for cid in mst.covering_proof(root, keys[len(keys) // 2]):
        block = store.get(cid)
        assert block is not None
        partial_store.put(cid, block)
    partial = MST(partial_store)
    assert partial.build_frontier(partial.flatten_frontier(root)) == root


def test_inversion_rejects_wrong_ops(interop_dir):
    """An inaccurate op list must not invert to the previous root."""
    from fastatp.exceptions import RepoError

    value = CID.for_raw(b"value")
    other = CID.for_raw(b"other")
    store = MemoryBlockStore()
    mst = MST(store)
    before = {"coll/a": value, "coll/b": value, "coll/c": value}
    root_before = mst.build(before)
    after = dict(before, **{"coll/d": value})
    root_after = mst.build(after)
    proof_store = MemoryBlockStore()
    for cid in mst.covering_proof(root_after, "coll/d"):
        proof_store.put(cid, store.get(cid))
    partial = MST(proof_store)
    # omitting the op: root does not invert back
    assert partial.invert_changes(root_after, []) != root_before
    # claiming the wrong cid for the created record: rejected
    with pytest.raises(RepoError, match="does not match"):
        partial.invert_changes(root_after, [("create", "coll/d", other, None)])
    # claiming a create for a key not in the tree: rejected
    with pytest.raises(RepoError, match="does not match"):
        partial.invert_changes(root_after, [("create", "coll/zz", value, None)])
    # the correct op inverts exactly
    assert partial.invert_changes(root_after, [("create", "coll/d", value, None)]) == root_before


def test_new_nodes_only_diffs_are_insufficient(interop_dir):
    """Why covering proofs exist: at least one interop fixture ("merge and
    split in multi-op commit") CANNOT be inverted from new-MST-nodes-only
    blocks — the pre-Gate-14 diff shape. The verifier must reject it rather
    than derive a wrong root."""
    from fastatp.exceptions import RepoError

    fixtures = _load(interop_dir, "firehose", "commit-proof-fixtures.json")
    rejected = 0
    for fx in fixtures:
        leaf = CID.decode(fx["leafValue"])
        store = MemoryBlockStore()
        mst = MST(store)
        before = dict.fromkeys(fx["keys"], leaf)
        root_before = mst.build(before)
        before_nodes = mst.node_cids(root_before)
        after = dict(before)
        for k in fx["dels"]:
            del after[k]
        for k in fx["adds"]:
            after[k] = leaf
        root_after = mst.build(after)
        new_only_store = MemoryBlockStore()
        for c in mst.node_cids(root_after) - before_nodes:
            new_only_store.put(c, store.get(c))
        changes = [("create", k, leaf, None) for k in fx["adds"]] + [
            ("delete", k, None, leaf) for k in fx["dels"]
        ]
        try:
            assert MST(new_only_store).invert_changes(root_after, changes) == root_before
        except RepoError:
            rejected += 1  # insufficient diff detected, never a wrong root
    assert rejected >= 1
