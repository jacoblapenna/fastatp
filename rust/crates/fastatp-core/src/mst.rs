//! Merkle Search Tree (MST), mirroring `fastatp/repo/mst.py` exactly.
//!
//! Fully deterministic in its key set: a key's layer is the number of leading
//! zero bits of sha256(key) counted in 2-bit chunks (fanout 4); entries live
//! at exactly their layer; subtree pointers descend exactly one layer
//! (structural nodes fill multi-layer gaps); node maps are canonical
//! DAG-CBOR. Mutations rebuild the tree from the sorted key set instead of
//! splicing — correct by construction, verified against the
//! interop root CIDs.
//!
//! Node data: `{"e": [entry...], "l": CID|null}`; entry: `{"k": suffix-bytes,
//! "p": shared-prefix-len, "t": right-subtree CID|null, "v": value CID}`.

use std::collections::{BTreeMap, HashMap, HashSet};

use thiserror::Error;

use crate::cid::Cid;
use crate::crypto::sha256;
use crate::dagcbor::{self, Value};

#[derive(Debug, Error)]
pub enum MstError {
    #[error("invalid MST key: {0:?}")]
    InvalidKey(String),
    #[error("missing MST block {0}")]
    MissingBlock(Cid),
    #[error("malformed MST node {0}")]
    MalformedNode(Cid),
    #[error("malformed MST entry in node {0}")]
    MalformedEntry(Cid),
    #[error("non-UTF-8 key bytes in MST node {0}")]
    NonUtf8Key(Cid),
    #[error("key {0:?} is not present in the tree")]
    KeyAbsent(String),
    #[error("DAG-CBOR error: {0}")]
    DagCbor(#[from] dagcbor::DagCborError),
}

/// The block-store seam: content-addressed bytes, nothing more.
pub trait BlockStore {
    fn get(&self, cid: &Cid) -> Option<Vec<u8>>;
    fn put(&mut self, cid: Cid, data: Vec<u8>);
    fn delete(&mut self, cid: &Cid);
    /// A snapshot of every stored CID (callers mutate while iterating).
    fn cids(&self) -> Vec<Cid>;
}

/// Dict-backed block store (the default; swap in your own persistence).
#[derive(Debug, Default, Clone)]
pub struct MemoryBlockStore {
    blocks: HashMap<Cid, Vec<u8>>,
}

impl MemoryBlockStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn len(&self) -> usize {
        self.blocks.len()
    }

    pub fn is_empty(&self) -> bool {
        self.blocks.is_empty()
    }
}

impl BlockStore for MemoryBlockStore {
    fn get(&self, cid: &Cid) -> Option<Vec<u8>> {
        self.blocks.get(cid).cloned()
    }

    fn put(&mut self, cid: Cid, data: Vec<u8>) {
        self.blocks.insert(cid, data);
    }

    fn delete(&mut self, cid: &Cid) {
        self.blocks.remove(cid);
    }

    fn cids(&self) -> Vec<Cid> {
        self.blocks.keys().cloned().collect()
    }
}

fn key_part_ok(part: &str) -> bool {
    !part.is_empty()
        && part
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-' | b':' | b'.' | b'~'))
}

pub fn validate_mst_key(key: &str) -> Result<(), MstError> {
    let parts: Vec<&str> = key.split('/').collect();
    if key.len() > 1024 || parts.len() != 2 || !key_part_ok(parts[0]) || !key_part_ok(parts[1]) {
        return Err(MstError::InvalidKey(key.to_owned()));
    }
    Ok(())
}

/// Layer for a key: leading zero bits of sha256(key), in 2-bit chunks.
pub fn key_height(key: &str) -> u32 {
    let digest = sha256(key.as_bytes());
    let mut zeros = 0u32;
    for byte in digest {
        if byte == 0 {
            zeros += 8;
            continue;
        }
        zeros += byte.leading_zeros(); // u8: counts within 8 bits
        break;
    }
    zeros / 2
}

/// Shared leading characters of two keys (character count, like Python).
pub fn common_prefix_len(left: &str, right: &str) -> usize {
    left.chars()
        .zip(right.chars())
        .take_while(|(a, b)| a == b)
        .count()
}

struct NodeEntry {
    prefix_len: usize,
    key_suffix: Vec<u8>,
    value: Cid,
    tree: Option<Cid>,
}

struct Node {
    left: Option<Cid>,
    entries: Vec<NodeEntry>,
}

fn link_or_null(cid: &Option<Cid>) -> Value {
    match cid {
        Some(c) => Value::Link(c.clone()),
        None => Value::Null,
    }
}

fn put_node(
    store: &mut dyn BlockStore,
    left: Option<Cid>,
    entries: Vec<NodeEntry>,
) -> Result<Cid, MstError> {
    let entry_values: Vec<Value> = entries
        .into_iter()
        .map(|entry| {
            Value::map(vec![
                ("p", Value::Integer(entry.prefix_len as i64)),
                ("k", Value::Bytes(entry.key_suffix)),
                ("v", Value::Link(entry.value)),
                ("t", link_or_null(&entry.tree)),
            ])
        })
        .collect();
    let node = Value::map(vec![
        ("e", Value::Array(entry_values)),
        ("l", link_or_null(&left)),
    ]);
    let data = dagcbor::encode(&node)?;
    let cid = Cid::from_dag_cbor(&data);
    store.put(cid.clone(), data);
    Ok(cid)
}

/// Store the canonical tree for a key→CID map; return the root CID.
pub fn build(store: &mut dyn BlockStore, entries: &BTreeMap<String, Cid>) -> Result<Cid, MstError> {
    for key in entries.keys() {
        validate_mst_key(key)?;
    }
    let items: Vec<(&str, &Cid)> = entries.iter().map(|(k, v)| (k.as_str(), v)).collect();
    let heights: Vec<u32> = items.iter().map(|(k, _)| key_height(k)).collect();
    let top = heights.iter().copied().max().unwrap_or(0);
    build_node(store, &items, &heights, top)
}

fn build_node(
    store: &mut dyn BlockStore,
    items: &[(&str, &Cid)],
    heights: &[u32],
    layer: u32,
) -> Result<Cid, MstError> {
    if items.is_empty() {
        return put_node(store, None, Vec::new());
    }
    fn subtree(
        store: &mut dyn BlockStore,
        items: &[(&str, &Cid)],
        heights: &[u32],
        layer: u32,
        start: usize,
        end: usize,
    ) -> Result<Option<Cid>, MstError> {
        if start == end {
            return Ok(None);
        }
        Ok(Some(build_node(
            store,
            &items[start..end],
            &heights[start..end],
            layer - 1,
        )?))
    }

    let mut node_entries: Vec<NodeEntry> = Vec::new();
    let mut left: Option<Cid> = None;
    let mut prev_key = "";
    let mut group_start = 0usize;
    for (i, height) in heights.iter().enumerate() {
        if *height != layer {
            continue;
        }
        let child = subtree(store, items, heights, layer, group_start, i)?;
        if let Some(last) = node_entries.last_mut() {
            last.tree = child;
        } else {
            left = child;
        }
        let key = items[i].0;
        let shared = common_prefix_len(prev_key, key);
        node_entries.push(NodeEntry {
            prefix_len: shared,
            // byte indexing is safe: build() validated keys as ASCII, so the
            // char count from common_prefix_len IS a byte offset
            key_suffix: key.as_bytes()[shared..].to_vec(),
            value: items[i].1.clone(),
            tree: None,
        });
        prev_key = key;
        group_start = i + 1;
    }
    let trailing = subtree(store, items, heights, layer, group_start, items.len())?;
    if let Some(last) = node_entries.last_mut() {
        last.tree = trailing;
    } else {
        // structural node: no entries at this layer, everything below
        left = trailing;
    }
    put_node(store, left, node_entries)
}

// --- reading -----------------------------------------------------------------

fn check_node_shape(value: Value, cid: &Cid) -> Result<Node, MstError> {
    let (Some(entries_value), Some(left_value)) = (value.get("e"), value.get("l")) else {
        return Err(MstError::MalformedNode(cid.clone()));
    };
    let left = match left_value {
        Value::Null => None,
        Value::Link(c) => Some(c.clone()),
        _ => return Err(MstError::MalformedNode(cid.clone())),
    };
    let Value::Array(raw_entries) = entries_value else {
        return Err(MstError::MalformedNode(cid.clone()));
    };
    let mut entries = Vec::with_capacity(raw_entries.len());
    for raw in raw_entries {
        let (Some(p), Some(k), Some(v)) = (raw.get("p"), raw.get("k"), raw.get("v")) else {
            return Err(MstError::MalformedEntry(cid.clone()));
        };
        // a negative p would silently slice prev_key from the end; it fails
        // the usize conversion here instead
        let prefix_len = p
            .as_integer()
            .and_then(|i| usize::try_from(i).ok())
            .ok_or_else(|| MstError::MalformedEntry(cid.clone()))?;
        let key_suffix = k
            .as_bytes()
            .ok_or_else(|| MstError::MalformedEntry(cid.clone()))?
            .to_vec();
        std::str::from_utf8(&key_suffix).map_err(|_| MstError::NonUtf8Key(cid.clone()))?;
        let value_cid = v
            .as_link()
            .ok_or_else(|| MstError::MalformedEntry(cid.clone()))?
            .clone();
        let tree = match raw.get("t") {
            None | Some(Value::Null) => None,
            Some(Value::Link(c)) => Some(c.clone()),
            Some(_) => return Err(MstError::MalformedEntry(cid.clone())),
        };
        entries.push(NodeEntry {
            prefix_len,
            key_suffix,
            value: value_cid,
            tree,
        });
    }
    Ok(Node { left, entries })
}

fn get_node(store: &dyn BlockStore, cid: &Cid) -> Result<Node, MstError> {
    let data = store
        .get(cid)
        .ok_or_else(|| MstError::MissingBlock(cid.clone()))?;
    check_node_shape(dagcbor::decode(&data)?, cid)
}

fn entry_key(prev_key: &str, entry: &NodeEntry) -> String {
    let mut key: String = prev_key.chars().take(entry.prefix_len).collect();
    key.push_str(std::str::from_utf8(&entry.key_suffix).expect("shape-checked utf-8"));
    key
}

/// Read the full key→CID map reachable from a root.
pub fn load(store: &dyn BlockStore, root: &Cid) -> Result<BTreeMap<String, Cid>, MstError> {
    let mut out = BTreeMap::new();
    walk(store, root, &mut out)?;
    Ok(out)
}

fn walk(
    store: &dyn BlockStore,
    cid: &Cid,
    out: &mut BTreeMap<String, Cid>,
) -> Result<(), MstError> {
    let node = get_node(store, cid)?;
    if let Some(left) = &node.left {
        walk(store, left, out)?;
    }
    let mut prev_key = String::new();
    for entry in &node.entries {
        let key = entry_key(&prev_key, entry);
        out.insert(key.clone(), entry.value.clone());
        prev_key = key;
        if let Some(tree) = &entry.tree {
            walk(store, tree, out)?;
        }
    }
    Ok(())
}

/// The Python lookup-step semantics shared by [`get`] and [`prove_path`]:
/// either the key is AT this node (Some(value)), or the search descends to a
/// child (None, Some(child)) or bottoms out (None, None).
fn lookup_step(node: &Node, key: &str) -> (Option<Cid>, Option<Cid>) {
    let mut prev_key = String::new();
    let mut candidate = node.left.clone();
    for entry in &node.entries {
        let this_key = entry_key(&prev_key, entry);
        prev_key = this_key.clone();
        if this_key == key {
            return (Some(entry.value.clone()), None);
        }
        if key < this_key.as_str() {
            return (None, candidate); // descend left of this entry
        }
        candidate = entry.tree.clone();
    }
    // ran off the right edge: last entry's subtree (or `l` when empty)
    let candidate = match node.entries.last() {
        Some(last) => last.tree.clone(),
        None => node.left.clone(),
    };
    (None, candidate)
}

/// Point lookup without materializing the whole map.
pub fn get(store: &dyn BlockStore, root: &Cid, key: &str) -> Result<Option<Cid>, MstError> {
    let mut cursor = Some(root.clone());
    while let Some(cid) = cursor {
        let node = get_node(store, &cid)?;
        let (found, next) = lookup_step(&node, key);
        if found.is_some() {
            return Ok(found);
        }
        cursor = next;
    }
    Ok(None)
}

/// All MST node CIDs reachable from a root (not record values).
pub fn node_cids(store: &dyn BlockStore, root: &Cid) -> Result<HashSet<Cid>, MstError> {
    let mut seen = HashSet::new();
    let mut stack = vec![root.clone()];
    while let Some(cid) = stack.pop() {
        if !seen.insert(cid.clone()) {
            continue;
        }
        let node = get_node(store, &cid)?;
        if let Some(left) = node.left {
            stack.push(left);
        }
        for entry in node.entries {
            if let Some(tree) = entry.tree {
                stack.push(tree);
            }
        }
    }
    Ok(seen)
}

/// Node CIDs along the lookup path for a present key (a record proof).
///
/// Together with the signed commit and the record block, these nodes let a
/// verifier re-derive the record's membership in the signed root.
pub fn prove_path(store: &dyn BlockStore, root: &Cid, key: &str) -> Result<Vec<Cid>, MstError> {
    let mut path = Vec::new();
    let mut cursor = Some(root.clone());
    while let Some(cid) = cursor {
        path.push(cid.clone());
        let node = get_node(store, &cid)?;
        let (found, next) = lookup_step(&node, key);
        if found.is_some() {
            return Ok(path);
        }
        cursor = next;
    }
    Err(MstError::KeyAbsent(key.to_owned()))
}

/// Like [`load`], but skips subtrees whose blocks are absent.
///
/// Used to read the entries witnessed by a proof slice: only keys whose path
/// nodes are all present appear in the result.
pub fn load_partial(store: &dyn BlockStore, root: &Cid) -> Result<BTreeMap<String, Cid>, MstError> {
    let mut out = BTreeMap::new();
    walk_partial(store, root, &mut out)?;
    Ok(out)
}

fn walk_partial(
    store: &dyn BlockStore,
    cid: &Cid,
    out: &mut BTreeMap<String, Cid>,
) -> Result<(), MstError> {
    let Some(data) = store.get(cid) else {
        return Ok(());
    };
    let node = check_node_shape(dagcbor::decode(&data)?, cid)?;
    if let Some(left) = &node.left {
        walk_partial(store, left, out)?;
    }
    let mut prev_key = String::new();
    for entry in &node.entries {
        let key = entry_key(&prev_key, entry);
        out.insert(key.clone(), entry.value.clone());
        prev_key = key;
        if let Some(tree) = &entry.tree {
            walk_partial(store, tree, out)?;
        }
    }
    Ok(())
}
