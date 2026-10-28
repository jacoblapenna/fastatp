"""Merkle Search Tree (MST), the repository's key/value structure.

The MST is fully deterministic in its key set: a key's layer is the number of
leading zero bits in sha256(key) counted in 2-bit chunks (fanout 4), entries
live at exactly their layer, subtree pointers always descend exactly one
layer (structural nodes with no entries fill multi-layer gaps), and node maps
are canonical DAG-CBOR. FastATP exploits that determinism: mutations rebuild
the tree from the (sorted) key set instead of splicing — correct by
construction, verified against the interop root CIDs.

Node data: ``{"e": [entry...], "l": CID|null}``; entry:
``{"k": suffix-bytes, "p": shared-prefix-len, "t": right-subtree CID|null,
"v": value CID}``.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from fastatp import dagcbor
from fastatp.cid import CID
from fastatp.exceptions import RepoError
from fastatp.repo.store import BlockStore, MemoryBlockStore

_KEY_CHARS = re.compile(r"^[a-zA-Z0-9_\-:.~]+$")


def validate_mst_key(key: str) -> str:
    parts = key.split("/")
    if (
        len(key) > 1024
        or len(parts) != 2
        or not parts[0]
        or not parts[1]
        or not _KEY_CHARS.match(parts[0])
        or not _KEY_CHARS.match(parts[1])
    ):
        raise ValueError(f"invalid MST key: {key!r}")
    return key


def key_height(key: str) -> int:
    """Layer for a key: leading zero bits of sha256(key), in 2-bit chunks."""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    zeros = 0
    for byte in digest:
        if byte == 0:
            zeros += 8
            continue
        zeros += 8 - byte.bit_length()
        break
    return zeros // 2


def common_prefix_len(left: str, right: str) -> int:
    count = 0
    for a, b in zip(left, right, strict=False):
        if a != b:
            break
        count += 1
    return count


class MST:
    """Canonical MST builder/reader over a block store."""

    def __init__(self, store: BlockStore) -> None:
        self.store = store

    # --- construction ------------------------------------------------------

    def build(self, entries: dict[str, CID]) -> CID:
        """Store the canonical tree for a key->CID map; return the root CID."""
        items = sorted(entries.items())
        for key, _ in items:
            validate_mst_key(key)
        heights = [key_height(key) for key, _ in items]
        top = max(heights, default=0)
        return self._build_node(items, heights, top)

    def _put_node(self, left: CID | None, entries: list[dict[str, Any]]) -> CID:
        data = dagcbor.encode({"e": entries, "l": left})
        cid = CID.for_dag_cbor(data)
        self.store.put(cid, data)
        return cid

    def _build_node(self, items: list[tuple[str, CID]], heights: list[int], layer: int) -> CID:
        if not items:
            return self._put_node(None, [])

        def subtree(start: int, end: int) -> CID | None:
            if start == end:
                return None
            return self._build_node(items[start:end], heights[start:end], layer - 1)

        node_entries: list[dict[str, Any]] = []
        left: CID | None = None
        prev_key = ""
        group_start = 0
        indexes = [i for i, h in enumerate(heights) if h == layer]
        for i in indexes:
            child = subtree(group_start, i)
            if not node_entries:
                left = child
            else:
                node_entries[-1]["t"] = child
            key = items[i][0]
            shared = common_prefix_len(prev_key, key)
            node_entries.append(
                {"p": shared, "k": key[shared:].encode("utf-8"), "v": items[i][1], "t": None}
            )
            prev_key = key
            group_start = i + 1
        trailing = subtree(group_start, len(items))
        if node_entries:
            node_entries[-1]["t"] = trailing
        else:
            # structural node: no entries at this layer, everything below
            left = trailing
        return self._put_node(left, node_entries)

    # --- reading ------------------------------------------------------------

    @staticmethod
    def _check_node_shape(node: Any, cid: CID) -> dict[str, Any]:
        if (
            not isinstance(node, dict)
            or "e" not in node
            or "l" not in node
            or not isinstance(node["e"], list)
            or not (node["l"] is None or isinstance(node["l"], CID))
        ):
            raise RepoError(f"malformed MST node {cid}")
        for entry in node["e"]:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("p"), int)
                or entry["p"] < 0  # negative would silently slice prev_key from the end
                or not isinstance(entry.get("k"), bytes)
                or not isinstance(entry.get("v"), CID)
                or not (entry.get("t") is None or isinstance(entry.get("t"), CID))
            ):
                raise RepoError(f"malformed MST entry in node {cid}")
            try:
                entry["k"].decode("utf-8")
            except UnicodeDecodeError:
                raise RepoError(f"non-UTF-8 key bytes in MST node {cid}") from None
        return node

    def _get_node(self, cid: CID) -> dict[str, Any]:
        data = self.store.get(cid)
        if data is None:
            raise RepoError(f"missing MST block {cid}")
        return self._check_node_shape(dagcbor.decode(data), cid)

    def load(self, root: CID) -> dict[str, CID]:
        """Read the full key->CID map reachable from a root."""
        out: dict[str, CID] = {}
        self._walk(root, out)
        return out

    def _walk(self, cid: CID, out: dict[str, CID]) -> None:
        node = self._get_node(cid)
        if node["l"] is not None:
            self._walk(node["l"], out)
        prev_key = ""
        for entry in node["e"]:
            key = prev_key[: entry["p"]] + entry["k"].decode("utf-8")
            out[key] = entry["v"]
            prev_key = key
            if entry["t"] is not None:
                self._walk(entry["t"], out)

    def get(self, root: CID, key: str) -> CID | None:
        """Point lookup without materializing the whole map."""
        node = self._get_node(root)
        prev_key = ""
        candidate: CID | None = node["l"]
        for entry in node["e"]:
            entry_key = prev_key[: entry["p"]] + entry["k"].decode("utf-8")
            prev_key = entry_key
            if entry_key == key:
                return entry["v"]
            if key < entry_key:
                break
            candidate = entry["t"]
        else:
            candidate = node["e"][-1]["t"] if node["e"] else node["l"]
        return self.get(candidate, key) if candidate is not None else None

    def node_cids(self, root: CID) -> set[CID]:
        """All MST node CIDs reachable from a root (not record values)."""
        seen: set[CID] = set()
        stack = [root]
        while stack:
            cid = stack.pop()
            if cid in seen:
                continue
            seen.add(cid)
            node = self._get_node(cid)
            if node["l"] is not None:
                stack.append(node["l"])
            for entry in node["e"]:
                if entry["t"] is not None:
                    stack.append(entry["t"])
        return seen

    def prove_path(self, root: CID, key: str) -> list[CID]:
        """Node CIDs along the lookup path for a present key (a record proof).

        Together with the signed commit and the record block, these nodes let
        a verifier re-derive the record's membership in the signed root.
        """
        path: list[CID] = []
        cid: CID | None = root
        while cid is not None:
            path.append(cid)
            node = self._get_node(cid)
            prev_key = ""
            candidate: CID | None = node["l"]
            for entry in node["e"]:
                entry_key = prev_key[: entry["p"]] + entry["k"].decode("utf-8")
                prev_key = entry_key
                if entry_key == key:
                    return path
                if key < entry_key:
                    break
                candidate = entry["t"]
            else:
                candidate = node["e"][-1]["t"] if node["e"] else node["l"]
            cid = candidate
        raise RepoError(f"key {key!r} is not present in the tree")

    # --- covering proofs (repository spec: "Repository Diffs") ---------------

    def _mixed_entries(self, cid: CID) -> list[tuple[Any, ...]]:
        """A node's entries in reading order, subtrees interleaved with leaves:
        ``("tree", cid)`` / ``("leaf", key, value_cid)``."""
        node = self._get_node(cid)
        out: list[tuple[Any, ...]] = []
        if node["l"] is not None:
            out.append(("tree", node["l"]))
        prev_key = ""
        for entry in node["e"]:
            key = prev_key[: entry["p"]] + entry["k"].decode("utf-8")
            out.append(("leaf", key, entry["v"]))
            prev_key = key
            if entry["t"] is not None:
                out.append(("tree", entry["t"]))
        return out

    @staticmethod
    def _ge_leaf_index(entries: list[tuple[Any, ...]], key: str) -> int:
        """Index of the first leaf with key >= ``key`` (len(entries) if none)."""
        for i, e in enumerate(entries):
            if e[0] == "leaf" and e[1] >= key:
                return i
        return len(entries)

    def covering_proof(self, root: CID, key: str) -> set[CID]:
        """Node CIDs needed to invert an operation on ``key`` (repository spec:
        the diff must include "any additional MST nodes needed to support
        operation inversion", i.e. nodes referencing paths directly adjacent
        to those mutated). Computed on the tree the ops produced — the union
        of the key's own lookup path and the paths to its left and right
        neighbor leaves; a faithful port of the reference implementation's
        ``MST.getCoveringProof``."""
        return (
            self._proof_for_key(root, key)
            | self._proof_left_sib(root, key)
            | self._proof_right_sib(root, key)
        )

    def _proof_for_key(self, cid: CID, key: str) -> set[CID]:
        entries = self._mixed_entries(cid)
        i = self._ge_leaf_index(entries, key)
        found = entries[i] if i < len(entries) else None
        if found is not None and found[0] == "leaf" and found[1] == key:
            blocks: set[CID] = set()
        else:
            prev = entries[i - 1] if i >= 1 else None
            if prev is None or prev[0] == "leaf":
                return set()  # absent key at a leaf gap: sibling proofs cover it
            blocks = self._proof_for_key(prev[1], key)
        blocks.add(cid)
        return blocks

    def _proof_left_sib(self, cid: CID, key: str) -> set[CID]:
        entries = self._mixed_entries(cid)
        i = self._ge_leaf_index(entries, key)
        prev = entries[i - 1] if i >= 1 else None
        if prev is None or prev[0] == "leaf":
            blocks: set[CID] = set()
        else:
            blocks = self._proof_left_sib(prev[1], key)
        blocks.add(cid)
        return blocks

    def _proof_right_sib(self, cid: CID, key: str) -> set[CID]:
        entries = self._mixed_entries(cid)
        i = self._ge_leaf_index(entries, key)
        found = entries[i] if i < len(entries) else None
        if found is None:
            found = entries[i - 1] if i >= 1 else None
        if found is None:
            blocks: set[CID] = set()
        elif found[0] == "tree":
            blocks = self._proof_right_sib(found[1], key)
        else:
            j = i + 1 if found[1] == key else i - 1
            node = entries[j] if 0 <= j < len(entries) else None
            if node is None or node[0] == "leaf":
                blocks = set()
            else:
                blocks = self._proof_right_sib(node[1], key)
        blocks.add(cid)
        return blocks

    def load_partial(self, root: CID) -> dict[str, CID]:
        """Like load(), but skips subtrees whose blocks are absent.

        Used to read the entries witnessed by a proof slice: only keys whose
        path nodes are all present appear in the result.
        """
        out: dict[str, CID] = {}
        self._walk_partial(root, out)
        return out

    def _walk_partial(self, cid: CID, out: dict[str, CID]) -> None:
        data = self.store.get(cid)
        if data is None:
            return
        node = self._check_node_shape(dagcbor.decode(data), cid)
        if node["l"] is not None:
            self._walk_partial(node["l"], out)
        prev_key = ""
        for entry in node["e"]:
            key = prev_key[: entry["p"]] + entry["k"].decode("utf-8")
            out[key] = entry["v"]
            prev_key = key
            if entry["t"] is not None:
                self._walk_partial(entry["t"], out)

    # --- operation inversion (sync spec: "Reliable Repository Synchronization") --

    def _node_layer(self, cid: CID) -> int | None:
        """A stored node's layer: the height of any entry key, or one above the
        first entry-bearing descendant for structural nodes. ``None`` when the
        chain descends into an absent block (undeterminable)."""
        node = self._get_node(cid)
        prev_key = ""
        for entry in node["e"]:
            key = prev_key[: entry["p"]] + entry["k"].decode("utf-8")
            return key_height(key)
        if node["l"] is None:
            return 0  # empty tree
        if self.store.get(node["l"]) is None:
            return None
        below = self._node_layer(node["l"])
        return None if below is None else below + 1

    def flatten_frontier(self, root: CID) -> list[tuple[Any, ...]]:
        """The partial tree's reading frontier, in key order: every leaf whose
        node block is present as ``("leaf", key, value_cid)``, every pointer
        into an absent block as ``("sub", cid, layer)`` (``layer`` may be
        ``None`` when undeterminable). The root block must be present."""
        out: list[tuple[Any, ...]] = []
        self._walk_frontier(root, self._node_layer(root), out)
        return out

    def _walk_frontier(self, cid: CID, layer: int | None, out: list[tuple[Any, ...]]) -> None:
        below = None if layer is None else layer - 1
        node = self._get_node(cid)

        def child(sub: CID) -> None:
            if self.store.get(sub) is None:
                out.append(("sub", sub, below))
            else:
                self._walk_frontier(sub, below, out)

        if node["l"] is not None:
            child(node["l"])
        prev_key = ""
        for entry in node["e"]:
            key = prev_key[: entry["p"]] + entry["k"].decode("utf-8")
            out.append(("leaf", key, entry["v"]))
            prev_key = key
            if entry["t"] is not None:
                child(entry["t"])

    def build_frontier(self, elements: list[tuple[Any, ...]]) -> CID:
        """Recompute the canonical root over a frontier without entering opaque
        subtrees. The inverse of :meth:`flatten_frontier` — and, after a
        frontier has been edited, the root the edited tree MUST hash to.
        Raises when an opaque subtree makes the shape ambiguous (i.e. the
        frontier's blocks were insufficient for the edit)."""
        if not elements:
            return self._put_node(None, [])
        if len(elements) == 1 and elements[0][0] == "sub":
            return elements[0][1]
        heights: list[int] = []
        for el in elements:
            if el[0] == "leaf":
                heights.append(key_height(el[1]))
            elif el[2] is None:
                raise RepoError("opaque subtree of unknown layer is not alone in the tree")
            else:
                heights.append(el[2])
        cid = self._build_frontier_seg(elements, heights, max(heights))
        assert cid is not None  # elements is non-empty
        return cid

    def _build_frontier_seg(
        self, elements: list[tuple[Any, ...]], heights: list[int], layer: int
    ) -> CID | None:
        if not elements:
            return None
        if len(elements) == 1 and elements[0][0] == "sub" and heights[0] == layer:
            return elements[0][1]  # an untouched subtree, reused whole
        for el, h in zip(elements, heights, strict=True):
            if el[0] == "sub" and h >= layer:
                raise RepoError(
                    "diff blocks are insufficient to invert: an unopened subtree "
                    "must merge with its neighbors"
                )

        def subtree(start: int, end: int) -> CID | None:
            if start == end:
                return None
            return self._build_frontier_seg(elements[start:end], heights[start:end], layer - 1)

        node_entries: list[dict[str, Any]] = []
        left: CID | None = None
        prev_key = ""
        group_start = 0
        indexes = [
            i
            for i, (el, h) in enumerate(zip(elements, heights, strict=True))
            if el[0] == "leaf" and h == layer
        ]
        for i in indexes:
            child = subtree(group_start, i)
            if not node_entries:
                left = child
            else:
                node_entries[-1]["t"] = child
            key = elements[i][1]
            shared = common_prefix_len(prev_key, key)
            node_entries.append(
                {"p": shared, "k": key[shared:].encode("utf-8"), "v": elements[i][2], "t": None}
            )
            prev_key = key
            group_start = i + 1
        trailing = subtree(group_start, len(elements))
        if node_entries:
            node_entries[-1]["t"] = trailing
        else:
            left = trailing  # structural node filling a multi-layer gap
        return self._put_node(left, node_entries)

    def invert_changes(self, root: CID, changes: list[tuple[Any, ...]]) -> CID:
        """Recompute the PREVIOUS root by applying ``changes`` in reverse to the
        partial tree at ``root`` (the sync spec's "operation inversion").

        ``changes`` are forward ops: ``(action, key, cid, prev_cid)`` where
        ``cid`` is the new record CID (``None`` for deletes) and ``prev_cid``
        the pre-op CID (``None`` for creates). Order-independent — each key
        may appear at most once per commit. Raises :class:`RepoError` when the
        blocks at hand cannot support the inversion, which is exactly a
        relay's reason to reject the diff."""
        elements = self.flatten_frontier(root)

        def leaf_index(key: str) -> int | None:
            for i, el in enumerate(elements):
                if el[0] == "leaf" and el[1] == key:
                    return i
            return None

        for action, key, cid, prev in changes:
            idx = leaf_index(key)
            if action == "create":
                if idx is None or elements[idx][2] != cid:
                    raise RepoError(f"create op for {key!r} does not match the diff tree")
                del elements[idx]
            elif action == "update":
                if idx is None or elements[idx][2] != cid:
                    raise RepoError(f"update op for {key!r} does not match the diff tree")
                if prev is None:
                    raise RepoError(f"update op for {key!r} lacks prev (required for inversion)")
                elements[idx] = ("leaf", key, prev)
            elif action == "delete":
                if idx is not None:
                    raise RepoError(f"delete op for {key!r} but the key is in the diff tree")
                if prev is None:
                    raise RepoError(f"delete op for {key!r} lacks prev (required for inversion)")
                # re-insert at the key's sorted position; an opaque subtree in
                # that gap means the position is ambiguous -> insufficient diff
                pos = len(elements)
                for i, el in enumerate(elements):
                    if el[0] == "leaf" and el[1] > key:
                        pos = i
                        break
                gap_start = pos
                while gap_start > 0 and elements[gap_start - 1][0] == "sub":
                    gap_start -= 1
                if gap_start != pos:
                    raise RepoError(f"diff blocks are insufficient to invert the delete of {key!r}")
                elements.insert(pos, ("leaf", key, prev))
            else:
                raise RepoError(f"unknown op action {action!r}")
        return MST(MemoryBlockStore()).build_frontier(elements)
