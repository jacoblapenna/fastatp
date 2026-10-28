//! Present-state repository, mirroring `fastatp/repo/repo.py`: current MST +
//! signed commit, nothing else.
//!
//! One DID = one repo holding many collections. A delete restructures the
//! tree and orphans blocks; every commit ends with a garbage collection that
//! deletes blocks unreachable from the new commit, so store size tracks
//! current records, never total writes. Commits carry `prev: null` — there is
//! no walkable history chain.
//!
//! Deviations from the Python module, both deliberate: records are raw
//! data-model [`Value`]s (the Lexicon type layer stays Python-side; the wasm
//! bindings pass JSON through the data model), and [`CommitEvent`] carries no
//! wall-clock `time` field (that stamp belongs to the serving firehose, which
//! is not part of the core).

use std::collections::{BTreeMap, HashSet};

use thiserror::Error;

use crate::car::{self, CarError};
use crate::cid::Cid;
use crate::commit::{Commit, CommitError};
use crate::crypto::{PublicKey, Signer};
use crate::dagcbor::{self, Value};
use crate::mst::{self, BlockStore, MemoryBlockStore, MstError};
use crate::tid;

#[derive(Debug, Error)]
pub enum RepoError {
    #[error("{0}")]
    Invalid(String),
    #[error("no record at {0}")]
    NoRecord(String),
    #[error("record already exists at {0}")]
    RecordExists(String),
    #[error("block {0} missing from store")]
    MissingBlock(Cid),
    #[error("MST error: {0}")]
    Mst(#[from] MstError),
    #[error("CAR error: {0}")]
    Car(#[from] CarError),
    #[error("commit error: {0}")]
    Commit(#[from] CommitError),
    #[error("DAG-CBOR error: {0}")]
    DagCbor(#[from] dagcbor::DagCborError),
}

/// Where a repo's `rev` TIDs come from. Injectable so the wasm peer can stamp
/// time from its own environment (`performance.timeOrigin` etc.) while native
/// code uses [`tid::TidClock`] directly.
pub trait RevClock {
    /// A TID strictly greater than every TID this clock has produced.
    fn next_rev(&mut self) -> String;
}

#[cfg(not(target_arch = "wasm32"))]
impl RevClock for tid::TidClock {
    fn next_rev(&mut self) -> String {
        self.now()
    }
}

/// A [`RevClock`] over any caller-supplied microsecond-timestamp source;
/// monotonicity is enforced here so the source only has to be roughly right.
pub struct ExternalClock<F: FnMut() -> u64> {
    provider: F,
    clock_id: u16,
    last_us: u64,
}

impl<F: FnMut() -> u64> ExternalClock<F> {
    pub fn new(provider: F, clock_id: u16) -> Self {
        Self {
            provider,
            clock_id: clock_id & 0x3FF,
            last_us: 0,
        }
    }
}

impl<F: FnMut() -> u64> RevClock for ExternalClock<F> {
    fn next_rev(&mut self) -> String {
        let provided = (self.provider)();
        self.last_us = provided.max(self.last_us + 1);
        tid::encode_tid(self.last_us, self.clock_id).expect("in-range timestamp")
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WriteAction {
    Create,
    Update,
    Delete,
}

impl WriteAction {
    fn name(self) -> &'static str {
        match self {
            WriteAction::Create => "create",
            WriteAction::Update => "update",
            WriteAction::Delete => "delete",
        }
    }
}

#[derive(Debug, Clone)]
pub struct WriteOp {
    pub action: WriteAction,
    pub collection: String,
    pub rkey: String,
    pub record: Option<Value>,
}

impl WriteOp {
    pub fn create(collection: &str, rkey: &str, record: Value) -> Self {
        Self {
            action: WriteAction::Create,
            collection: collection.into(),
            rkey: rkey.into(),
            record: Some(record),
        }
    }

    pub fn update(collection: &str, rkey: &str, record: Value) -> Self {
        Self {
            action: WriteAction::Update,
            collection: collection.into(),
            rkey: rkey.into(),
            record: Some(record),
        }
    }

    pub fn delete(collection: &str, rkey: &str) -> Self {
        Self {
            action: WriteAction::Delete,
            collection: collection.into(),
            rkey: rkey.into(),
            record: None,
        }
    }

    pub fn path(&self) -> String {
        format!("{}/{}", self.collection, self.rkey)
    }
}

/// One committed write, as a firehose would announce it.
#[derive(Debug, Clone)]
pub struct RepoOp {
    pub action: &'static str,
    pub path: String,
    pub cid: Option<Cid>,
    pub prev: Option<Cid>,
}

/// Everything a firehose needs to announce one commit.
#[derive(Debug, Clone)]
pub struct CommitEvent {
    pub commit: Commit,
    pub commit_cid: Cid,
    pub rev: String,
    pub since: Option<String>,
    pub prev_data: Option<Cid>,
    pub ops: Vec<RepoOp>,
    pub blocks: Vec<(Cid, Vec<u8>)>,
}

impl CommitEvent {
    pub fn blocks_car(&self) -> Vec<u8> {
        car::encode_car(std::slice::from_ref(&self.commit_cid), &self.blocks)
    }
}

/// A DID's signed, present-state repository over a block store.
pub struct Repo<S: Signer> {
    did: String,
    signer: S,
    store: Box<dyn BlockStore>,
    clock: Box<dyn RevClock>,
    entries: BTreeMap<String, Cid>,
    commit: Commit,
    commit_cid: Cid,
    root: Cid,
    mst_nodes: HashSet<Cid>,
}

impl<S: Signer> Repo<S> {
    /// A fresh repo over explicit parts; signs the genesis (empty) commit.
    pub async fn create(
        did: String,
        signer: S,
        store: Box<dyn BlockStore>,
        clock: Box<dyn RevClock>,
    ) -> Result<Self, RepoError> {
        let mut repo = Repo {
            did,
            signer,
            store,
            clock,
            entries: BTreeMap::new(),
            // placeholder state; the genesis commit below replaces it
            commit: Commit {
                did: String::new(),
                data: Cid::from_raw(b""),
                rev: String::new(),
                sig: Vec::new(),
                version: 3,
                prev: None,
            },
            commit_cid: Cid::from_raw(b""),
            root: Cid::from_raw(b""),
            mst_nodes: HashSet::new(),
        };
        repo.commit_now(Vec::new()).await?;
        Ok(repo)
    }

    /// Native convenience: in-memory store, OS-random monotonic TID clock.
    #[cfg(all(feature = "rand", not(target_arch = "wasm32")))]
    pub async fn new(did: &str, signer: S) -> Result<Self, RepoError> {
        Self::create(
            did.to_owned(),
            signer,
            Box::new(MemoryBlockStore::new()),
            Box::new(tid::TidClock::random()),
        )
        .await
    }

    /// Reconstruct a repo from a current-state CAR (checkpoint load). Pass
    /// `verify_key` to also check the commit signature; integrity always
    /// comes from content addressing regardless.
    pub fn from_car(
        data: &[u8],
        signer: S,
        clock: Box<dyn RevClock>,
        verify_key: Option<&PublicKey>,
    ) -> Result<Self, RepoError> {
        let (commit, entries, blocks) = read_car(data, verify_key)?;
        let mut store: Box<dyn BlockStore> = Box::new(MemoryBlockStore::new());
        for (cid, block) in &blocks {
            store.put(cid.clone(), block.clone());
        }
        let commit_cid = commit.cid();
        let root = commit.data.clone();
        let mst_nodes = mst::node_cids(store.as_ref(), &root)?;
        Ok(Repo {
            did: commit.did.clone(),
            signer,
            store,
            clock,
            entries,
            commit,
            commit_cid,
            root,
            mst_nodes,
        })
    }

    // --- state ---------------------------------------------------------------

    pub fn did(&self) -> &str {
        &self.did
    }

    pub fn commit(&self) -> &Commit {
        &self.commit
    }

    pub fn commit_cid(&self) -> &Cid {
        &self.commit_cid
    }

    pub fn rev(&self) -> &str {
        &self.commit.rev
    }

    pub fn root(&self) -> &Cid {
        &self.root
    }

    pub fn record_count(&self, collection: Option<&str>) -> usize {
        match collection {
            None => self.entries.len(),
            Some(name) => {
                let prefix = format!("{name}/");
                self.entries
                    .keys()
                    .filter(|k| k.starts_with(&prefix))
                    .count()
            }
        }
    }

    pub fn list_records(&self, collection: &str) -> Vec<(String, Cid)> {
        let prefix = format!("{collection}/");
        self.entries
            .iter()
            .filter(|(key, _)| key.starts_with(&prefix))
            .map(|(key, cid)| (key[prefix.len()..].to_owned(), cid.clone()))
            .collect()
    }

    pub fn get_record(&self, collection: &str, rkey: &str) -> Result<Option<Value>, RepoError> {
        let Some(cid) = self.entries.get(&format!("{collection}/{rkey}")) else {
            return Ok(None);
        };
        let data = self
            .store
            .get(cid)
            .ok_or_else(|| RepoError::MissingBlock(cid.clone()))?;
        Ok(Some(dagcbor::decode(&data)?))
    }

    /// The store's current block count (steady-state/GC assertions in tests).
    pub fn block_count(&self) -> usize {
        self.store.cids().len()
    }

    // --- writes ----------------------------------------------------------------

    pub async fn put_record(
        &mut self,
        collection: &str,
        rkey: &str,
        record: Value,
    ) -> Result<CommitEvent, RepoError> {
        let path = format!("{collection}/{rkey}");
        let op = if self.entries.contains_key(&path) {
            WriteOp::update(collection, rkey, record)
        } else {
            WriteOp::create(collection, rkey, record)
        };
        self.apply_writes(vec![op]).await
    }

    pub async fn delete_record(
        &mut self,
        collection: &str,
        rkey: &str,
    ) -> Result<CommitEvent, RepoError> {
        self.apply_writes(vec![WriteOp::delete(collection, rkey)])
            .await
    }

    /// Re-sign the current state under the current signer (no data change).
    pub async fn recommit(&mut self) -> Result<CommitEvent, RepoError> {
        self.commit_now(Vec::new()).await
    }

    pub async fn apply_writes(&mut self, writes: Vec<WriteOp>) -> Result<CommitEvent, RepoError> {
        if writes.is_empty() {
            return Err(RepoError::Invalid(
                "apply_writes requires at least one write".into(),
            ));
        }
        // validate against the ACCUMULATED batch state, not just the committed
        // state — two creates for one path in a batch is a protocol error
        let mut pending: HashSet<String> = self.entries.keys().cloned().collect();
        for write in &writes {
            validate_nsid_shape(&write.collection)?;
            mst::validate_mst_key(&write.path())?;
            let path = write.path();
            match write.action {
                WriteAction::Create => {
                    if write.record.is_none() {
                        return Err(RepoError::Invalid("create requires a record".into()));
                    }
                    if !pending.insert(path.clone()) {
                        return Err(RepoError::RecordExists(path));
                    }
                }
                WriteAction::Update => {
                    if write.record.is_none() {
                        return Err(RepoError::Invalid("update requires a record".into()));
                    }
                    if !pending.contains(&path) {
                        return Err(RepoError::NoRecord(path));
                    }
                }
                WriteAction::Delete => {
                    if !pending.remove(&path) {
                        return Err(RepoError::NoRecord(path));
                    }
                }
            }
        }
        self.commit_now(writes).await
    }

    async fn commit_now(&mut self, writes: Vec<WriteOp>) -> Result<CommitEvent, RepoError> {
        let old_entries = self.entries.clone();
        let old_mst_nodes = self.mst_nodes.clone();
        let since = if self.commit.rev.is_empty() {
            None
        } else {
            Some(self.commit.rev.clone())
        };
        let prev_data = if self.commit.rev.is_empty() {
            None
        } else {
            Some(self.root.clone())
        };

        let mut new_entries = self.entries.clone();
        let mut ops = Vec::new();
        let mut new_record_blocks: Vec<(Cid, Vec<u8>)> = Vec::new();
        for write in &writes {
            let path = write.path();
            let prev_cid = old_entries.get(&path).cloned();
            if write.action == WriteAction::Delete {
                new_entries.remove(&path);
                ops.push(RepoOp {
                    action: "delete",
                    path,
                    cid: None,
                    prev: prev_cid,
                });
                continue;
            }
            let record = write.record.as_ref().expect("validated in apply_writes");
            let data = dagcbor::encode(record)?;
            let cid = Cid::from_dag_cbor(&data);
            self.store.put(cid.clone(), data.clone());
            new_record_blocks.push((cid.clone(), data));
            new_entries.insert(path.clone(), cid.clone());
            ops.push(RepoOp {
                action: write.action.name(),
                path,
                cid: Some(cid),
                prev: if write.action == WriteAction::Update {
                    prev_cid
                } else {
                    None
                },
            });
        }

        let root = mst::build(self.store.as_mut(), &new_entries)?;
        let rev = self.clock.next_rev();
        let commit = Commit::create(self.did.clone(), root.clone(), rev, &self.signer).await?;
        let commit_bytes = commit.to_dag_cbor();
        let commit_cid = commit.cid();
        self.store.put(commit_cid.clone(), commit_bytes.clone());

        let new_mst_nodes = mst::node_cids(self.store.as_ref(), &root)?;
        self.entries = new_entries;
        self.commit = commit.clone();
        self.commit_cid = commit_cid.clone();
        self.root = root;
        self.mst_nodes = new_mst_nodes.clone();

        // event blocks: the commit, MST nodes new in this commit, new records
        let mut event_blocks = vec![(commit_cid.clone(), commit_bytes)];
        for cid in new_mst_nodes.difference(&old_mst_nodes) {
            let block = self
                .store
                .get(cid)
                .ok_or_else(|| RepoError::MissingBlock(cid.clone()))?;
            event_blocks.push((cid.clone(), block));
        }
        event_blocks.extend(new_record_blocks);

        let event = CommitEvent {
            rev: commit.rev.clone(),
            commit,
            commit_cid,
            since,
            prev_data,
            ops,
            blocks: event_blocks,
        };
        self.collect_garbage();
        Ok(event)
    }

    fn collect_garbage(&mut self) {
        let mut reachable: HashSet<Cid> = self.mst_nodes.clone();
        reachable.insert(self.commit_cid.clone());
        reachable.extend(self.entries.values().cloned());
        for cid in self.store.cids() {
            if !reachable.contains(&cid) {
                self.store.delete(&cid);
            }
        }
    }

    // --- CAR export / import / prove -------------------------------------------

    /// Current-state CAR: commit root + every reachable block.
    pub fn export_car(&self) -> Result<Vec<u8>, RepoError> {
        let mut blocks: Vec<(Cid, Vec<u8>)> = Vec::new();
        let mut reachable: Vec<Cid> = vec![self.commit_cid.clone()];
        reachable.extend(self.mst_nodes.iter().cloned());
        let record_cids: HashSet<Cid> = self.entries.values().cloned().collect();
        reachable.extend(record_cids);
        for cid in reachable {
            let data = self
                .store
                .get(&cid)
                .ok_or_else(|| RepoError::MissingBlock(cid.clone()))?;
            blocks.push((cid, data));
        }
        Ok(car::encode_car(
            std::slice::from_ref(&self.commit_cid),
            &blocks,
        ))
    }

    /// Replace this repo's state with a CAR's contents (checkpoint load).
    pub fn import_car(
        &mut self,
        data: &[u8],
        verify_key: Option<&PublicKey>,
    ) -> Result<(), RepoError> {
        let (commit, entries, blocks) = read_car(data, verify_key)?;
        if commit.did != self.did {
            return Err(RepoError::Invalid(format!(
                "CAR is for {}, not {}",
                commit.did, self.did
            )));
        }
        for (cid, block) in &blocks {
            self.store.put(cid.clone(), block.clone());
        }
        self.commit_cid = commit.cid();
        self.root = commit.data.clone();
        self.mst_nodes = mst::node_cids(self.store.as_ref(), &self.root)?;
        self.entries = entries;
        self.commit = commit;
        self.collect_garbage();
        Ok(())
    }

    /// A standalone record proof: CAR of commit + MST path + record block.
    ///
    /// Anyone holding this slice can verify — with nothing but the issuer's
    /// public key — that this exact record is present in this repo's signed
    /// current state.
    pub fn prove(&self, collection: &str, rkey: &str) -> Result<Vec<u8>, RepoError> {
        let key = format!("{collection}/{rkey}");
        let record_cid = self
            .entries
            .get(&key)
            .ok_or_else(|| RepoError::NoRecord(key.clone()))?;
        let mut needed = vec![self.commit_cid.clone()];
        needed.extend(mst::prove_path(self.store.as_ref(), &self.root, &key)?);
        needed.push(record_cid.clone());
        let mut blocks = Vec::new();
        for cid in needed {
            let data = self
                .store
                .get(&cid)
                .ok_or_else(|| RepoError::MissingBlock(cid.clone()))?;
            blocks.push((cid, data));
        }
        Ok(car::encode_car(
            std::slice::from_ref(&self.commit_cid),
            &blocks,
        ))
    }
}

/// Light NSID shape check on the write path (full validation is the Lexicon
/// layer's job, Python-side): dotted, ≥3 segments, ASCII.
fn validate_nsid_shape(nsid: &str) -> Result<(), RepoError> {
    let segments: Vec<&str> = nsid.split('.').collect();
    let ok = segments.len() >= 3
        && segments
            .iter()
            .all(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-'));
    if !ok {
        return Err(RepoError::Invalid(format!("invalid NSID {nsid:?}")));
    }
    Ok(())
}

/// Parse a repo CAR into (commit, key→record-CID map, blocks).
///
/// Integrity comes from content addressing: every CAR block is checked
/// against its CID on decode, and the entry map is read by walking only CIDs
/// reachable from the signed commit's `data` root. Pass `verify_key` to also
/// check the commit signature.
#[allow(clippy::type_complexity)]
pub fn read_car(
    data: &[u8],
    verify_key: Option<&PublicKey>,
) -> Result<(Commit, BTreeMap<String, Cid>, Vec<(Cid, Vec<u8>)>), RepoError> {
    let (roots, blocks) = car::decode_car(data)?;
    if roots.len() != 1 {
        return Err(RepoError::Invalid(
            "repo CAR must have exactly one root (the commit)".into(),
        ));
    }
    let commit_block = blocks
        .iter()
        .find(|(cid, _)| cid == &roots[0])
        .map(|(_, block)| block.clone())
        .ok_or_else(|| RepoError::Invalid("repo CAR is missing its commit block".into()))?;
    let commit = Commit::from_dag_cbor(&commit_block)?;
    if let Some(key) = verify_key {
        if !commit.verify(key) {
            return Err(RepoError::Invalid(
                "commit signature does not verify".into(),
            ));
        }
    }
    let mut store = MemoryBlockStore::new();
    for (cid, block) in &blocks {
        store.put(cid.clone(), block.clone());
    }
    let entries = mst::load(&store, &commit.data)?;
    Ok((commit, entries, blocks))
}

// --- proof verification --------------------------------------------------------

#[derive(Debug, Error)]
pub enum ProofError {
    #[error("malformed proof: {0}")]
    Malformed(String),
    #[error("proof must be rooted at its commit block")]
    BadRoot,
    #[error("commit signature does not verify")]
    SignatureInvalid,
    #[error("proof does not witness a record at {0:?}")]
    NotWitnessed(String),
    #[error("proof witnesses {0} records; pass a path to select one")]
    Ambiguous(usize),
    #[error("proof witnesses no record at all")]
    EmptyProof,
    #[error("proof is missing the record block")]
    MissingRecordBlock,
    #[error("record block is not valid DAG-CBOR: {0}")]
    BadRecord(String),
}

/// A verified record and its provenance, mirroring what `Peer.verify_detailed`
/// establishes minus DID resolution and Lexicon typing (caller's layers).
#[derive(Debug, Clone)]
pub struct VerifiedRecord {
    pub did: String,
    pub rev: String,
    pub collection: String,
    pub rkey: String,
    pub record_cid: Cid,
    pub record: Value,
}

/// Verify a record proof against a known public key: parse the CAR, check
/// the commit signature, re-derive the record's membership in the signed MST
/// root, and return the witnessed record. DID-document resolution stays out
/// of the core — the caller supplies the key it resolved and trusts.
pub fn verify_proof(
    proof: &[u8],
    public_key: &PublicKey,
    path: Option<&str>,
) -> Result<VerifiedRecord, ProofError> {
    let (roots, blocks) =
        car::decode_car(proof).map_err(|e| ProofError::Malformed(e.to_string()))?;
    let commit_block = match roots.as_slice() {
        [root] => blocks
            .iter()
            .find(|(cid, _)| cid == root)
            .map(|(_, block)| block.clone())
            .ok_or(ProofError::BadRoot)?,
        _ => return Err(ProofError::BadRoot),
    };
    let commit =
        Commit::from_dag_cbor(&commit_block).map_err(|e| ProofError::Malformed(e.to_string()))?;
    if !commit.verify(public_key) {
        return Err(ProofError::SignatureInvalid);
    }

    let mut store = MemoryBlockStore::new();
    for (cid, block) in &blocks {
        store.put(cid.clone(), block.clone());
    }
    let block_cids: HashSet<&Cid> = blocks.iter().map(|(cid, _)| cid).collect();

    let (resolved_path, record_cid) = match path {
        Some(wanted) => {
            let found = mst::get(&store, &commit.data, wanted)
                .ok()
                .flatten()
                .ok_or_else(|| ProofError::NotWitnessed(wanted.to_owned()))?;
            (wanted.to_owned(), found)
        }
        None => {
            // leaf values are record CIDs by construction, so the witnessed
            // values whose blocks travelled with the proof are its records
            let witnessed = mst::load_partial(&store, &commit.data)
                .map_err(|e| ProofError::Malformed(e.to_string()))?;
            let candidates: Vec<(String, Cid)> = witnessed
                .into_iter()
                .filter(|(_, cid)| block_cids.contains(cid))
                .collect();
            match candidates.as_slice() {
                [(path, cid)] => (path.clone(), cid.clone()),
                [] => return Err(ProofError::EmptyProof),
                other => return Err(ProofError::Ambiguous(other.len())),
            }
        }
    };

    let record_block = store
        .get(&record_cid)
        .ok_or(ProofError::MissingRecordBlock)?;
    let record =
        dagcbor::decode(&record_block).map_err(|e| ProofError::BadRecord(e.to_string()))?;
    let (collection, rkey) = resolved_path
        .split_once('/')
        .ok_or_else(|| ProofError::Malformed("record path has no collection".into()))?;
    Ok(VerifiedRecord {
        did: commit.did,
        rev: commit.rev,
        collection: collection.to_owned(),
        rkey: rkey.to_owned(),
        record_cid,
        record,
    })
}
