//! Signed repository commits, mirroring `fastatp/repo/commit.py`.
//!
//! A commit is the signed root of the repo: `{did, version: 3, data: <MST
//! root CID>, rev: <TID>, prev: null}` plus `sig` (compact low-S ECDSA over
//! the canonical DAG-CBOR of the unsigned object). Present-state repos never
//! point `prev` at an older commit — there is no walkable history chain.

use thiserror::Error;

use crate::cid::Cid;
use crate::crypto::{verify_signature, CryptoError, PublicKey, Signer};
use crate::dagcbor::{self, Value};
use crate::tid;

pub const COMMIT_VERSION: i64 = 3;

#[derive(Debug, Error)]
pub enum CommitError {
    #[error("commit must be a map")]
    NotAMap,
    #[error("unsupported commit version")]
    BadVersion,
    #[error("commit is missing field {0:?}")]
    MissingField(&'static str),
    #[error("commit field has the wrong type")]
    BadFieldType,
    #[error("invalid did: {0:?}")]
    BadDid(String),
    #[error("invalid rev: {0}")]
    BadRev(#[from] tid::TidError),
    #[error("DAG-CBOR error: {0}")]
    DagCbor(#[from] dagcbor::DagCborError),
    #[error("signer error: {0}")]
    Signer(#[from] CryptoError),
}

/// Minimal did syntax check (`did:<method>:<identifier>`), matching the
/// shape `fastatp.lex.types.validate_did` enforces on the commit path.
fn validate_did(did: &str) -> Result<(), CommitError> {
    let mut parts = did.splitn(3, ':');
    let (scheme, method, ident) = (parts.next(), parts.next(), parts.next());
    let method_ok = method.is_some_and(|m| {
        !m.is_empty()
            && m.bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
    });
    let ident_ok = ident.is_some_and(|i| !i.is_empty());
    if scheme != Some("did") || !method_ok || !ident_ok {
        return Err(CommitError::BadDid(did.to_owned()));
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Commit {
    pub did: String,
    pub data: Cid,
    pub rev: String,
    pub sig: Vec<u8>,
    pub version: i64,
    pub prev: Option<Cid>,
}

impl Commit {
    fn unsigned_value(&self) -> Value {
        Value::map(vec![
            ("did", Value::String(self.did.clone())),
            ("rev", Value::String(self.rev.clone())),
            ("data", Value::Link(self.data.clone())),
            (
                "prev",
                match &self.prev {
                    Some(c) => Value::Link(c.clone()),
                    None => Value::Null,
                },
            ),
            ("version", Value::Integer(self.version)),
        ])
    }

    pub fn to_value(&self) -> Value {
        let Value::Map(mut entries) = self.unsigned_value() else {
            unreachable!("unsigned_value is a map");
        };
        entries.push(("sig".to_owned(), Value::Bytes(self.sig.clone())));
        Value::Map(entries)
    }

    pub fn to_dag_cbor(&self) -> Vec<u8> {
        dagcbor::encode(&self.to_value()).expect("commit shape is always encodable")
    }

    pub fn cid(&self) -> Cid {
        Cid::from_dag_cbor(&self.to_dag_cbor())
    }

    /// Verify the commit signature (compact low-S; high-S and DER rejected).
    pub fn verify(&self, public_key: &PublicKey) -> bool {
        let unsigned =
            dagcbor::encode(&self.unsigned_value()).expect("commit shape is always encodable");
        verify_signature(public_key, &unsigned, &self.sig)
    }

    /// Sign a new commit through the [`Signer`] seam (async so the browser's
    /// WebCrypto pen can implement it; a [`crate::crypto::SoftwareSigner`]
    /// resolves immediately).
    pub async fn create<S: Signer>(
        did: String,
        data: Cid,
        rev: String,
        signer: &S,
    ) -> Result<Commit, CommitError> {
        validate_did(&did)?;
        tid::validate_tid(&rev)?;
        let unsigned = Value::map(vec![
            ("did", Value::String(did.clone())),
            ("rev", Value::String(rev.clone())),
            ("data", Value::Link(data.clone())),
            ("prev", Value::Null),
            ("version", Value::Integer(COMMIT_VERSION)),
        ]);
        let payload = dagcbor::encode(&unsigned)?;
        let sig = signer.sign(&payload).await?;
        Ok(Commit {
            did,
            data,
            rev,
            sig: sig.to_vec(),
            version: COMMIT_VERSION,
            prev: None,
        })
    }

    /// Parse a commit from a decoded map; crafted blocks fail here with a
    /// typed error, never as a panic later.
    pub fn from_value(value: &Value) -> Result<Commit, CommitError> {
        if !matches!(value, Value::Map(_)) {
            return Err(CommitError::NotAMap);
        }
        let version = value
            .get("version")
            .ok_or(CommitError::MissingField("version"))?
            .as_integer()
            .ok_or(CommitError::BadFieldType)?;
        if version != COMMIT_VERSION {
            return Err(CommitError::BadVersion);
        }
        let did = value
            .get("did")
            .ok_or(CommitError::MissingField("did"))?
            .as_str()
            .ok_or(CommitError::BadFieldType)?
            .to_owned();
        let data = value
            .get("data")
            .ok_or(CommitError::MissingField("data"))?
            .as_link()
            .ok_or(CommitError::BadFieldType)?
            .clone();
        let rev = value
            .get("rev")
            .ok_or(CommitError::MissingField("rev"))?
            .as_str()
            .ok_or(CommitError::BadFieldType)?
            .to_owned();
        let sig = value
            .get("sig")
            .ok_or(CommitError::MissingField("sig"))?
            .as_bytes()
            .ok_or(CommitError::BadFieldType)?
            .to_vec();
        let prev = match value.get("prev") {
            None | Some(Value::Null) => None,
            Some(Value::Link(c)) => Some(c.clone()),
            Some(_) => return Err(CommitError::BadFieldType),
        };
        Ok(Commit {
            did,
            data,
            rev,
            sig,
            version,
            prev,
        })
    }

    pub fn from_dag_cbor(data: &[u8]) -> Result<Commit, CommitError> {
        Self::from_value(&dagcbor::decode(data)?)
    }
}
