//! CIDv1: the content identifier used throughout atproto.
//!
//! Mirrors `fastatp/cid.py`: only the "blessed" configuration is produced —
//! CIDv1, SHA-256 multihash, codec `dag-cbor` (0x71) for nodes or `raw`
//! (0x55) for blobs, base32 string form. Parsing accepts any well-formed
//! binary CIDv1 and the multibase prefixes that appear in atproto data
//! (`b` base32, `z` base58btc, `m` base64, `u` base64url, `f` hex).

use std::fmt;
use std::sync::LazyLock;

use sha2::{Digest, Sha256};
use thiserror::Error;

use crate::varint;

pub const DAG_CBOR_CODEC: u64 = 0x71;
pub const RAW_CODEC: u64 = 0x55;
pub const SHA2_256: u64 = 0x12;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum CidError {
    #[error("only CIDv1 is supported (got version {0})")]
    UnsupportedVersion(u64),
    #[error("CID multihash length mismatch")]
    MultihashLength,
    #[error("malformed CID: {0}")]
    Malformed(String),
    #[error("unsupported multibase prefix {0:?}")]
    UnsupportedMultibase(char),
    #[error("invalid multibase payload: {0}")]
    InvalidMultibase(String),
}

/// RFC 4648 base32, lowercase, no padding — the CID string alphabet.
static BASE32_LOWER: LazyLock<data_encoding::Encoding> = LazyLock::new(|| {
    let mut spec = data_encoding::Specification::new();
    spec.symbols.push_str("abcdefghijklmnopqrstuvwxyz234567");
    spec.encoding().expect("static base32 spec is valid")
});

static BASE64_STD: LazyLock<data_encoding::Encoding> = LazyLock::new(|| {
    let mut spec = data_encoding::Specification::new();
    spec.symbols
        .push_str("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/");
    spec.encoding().expect("static base64 spec is valid")
});

static BASE64_URL: LazyLock<data_encoding::Encoding> = LazyLock::new(|| {
    let mut spec = data_encoding::Specification::new();
    spec.symbols
        .push_str("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_");
    spec.encoding().expect("static base64url spec is valid")
});

/// An immutable CIDv1.
#[derive(Clone, PartialEq, Eq, Hash)]
pub struct Cid {
    raw: Vec<u8>,
}

impl Cid {
    /// Validate and wrap binary CIDv1 bytes (version, codec, multihash length).
    pub fn from_bytes(raw: &[u8]) -> Result<Cid, CidError> {
        let (version, off) =
            varint::decode(raw, 0).map_err(|e| CidError::Malformed(e.to_string()))?;
        if version != 1 {
            return Err(CidError::UnsupportedVersion(version));
        }
        let (_codec, off) =
            varint::decode(raw, off).map_err(|e| CidError::Malformed(e.to_string()))?;
        let (_hash_code, off) =
            varint::decode(raw, off).map_err(|e| CidError::Malformed(e.to_string()))?;
        let (hash_size, off) =
            varint::decode(raw, off).map_err(|e| CidError::Malformed(e.to_string()))?;
        if (raw.len() - off) as u64 != hash_size {
            return Err(CidError::MultihashLength);
        }
        Ok(Cid { raw: raw.to_vec() })
    }

    fn create(codec: u64, data: &[u8]) -> Cid {
        let digest = Sha256::digest(data);
        let mut raw = Vec::with_capacity(4 + 32);
        varint::write(1, &mut raw);
        varint::write(codec, &mut raw);
        varint::write(SHA2_256, &mut raw);
        varint::write(32, &mut raw);
        raw.extend_from_slice(&digest);
        Cid { raw }
    }

    /// The CID of a canonical DAG-CBOR node (hashes `encoded`).
    pub fn from_dag_cbor(encoded: &[u8]) -> Cid {
        Cid::create(DAG_CBOR_CODEC, encoded)
    }

    /// The CID of a raw blob (hashes `data`).
    pub fn from_raw(data: &[u8]) -> Cid {
        Cid::create(RAW_CODEC, data)
    }

    /// Parse a multibase CID string (`b…` base32 is atproto's native form).
    pub fn parse(value: &str) -> Result<Cid, CidError> {
        let mut chars = value.chars();
        let prefix = chars
            .next()
            .ok_or_else(|| CidError::Malformed("empty CID string".into()))?;
        let rest = chars.as_str();
        let raw = match prefix {
            'b' => BASE32_LOWER
                .decode(rest.as_bytes())
                .map_err(|e| CidError::InvalidMultibase(e.to_string()))?,
            'z' => bs58::decode(rest)
                .into_vec()
                .map_err(|e| CidError::InvalidMultibase(e.to_string()))?,
            'm' => BASE64_STD
                .decode(rest.as_bytes())
                .map_err(|e| CidError::InvalidMultibase(e.to_string()))?,
            'u' => BASE64_URL
                .decode(rest.as_bytes())
                .map_err(|e| CidError::InvalidMultibase(e.to_string()))?,
            'f' => hex_decode(rest)?,
            other => return Err(CidError::UnsupportedMultibase(other)),
        };
        Cid::from_bytes(&raw)
    }

    pub fn version(&self) -> u64 {
        1
    }

    pub fn codec(&self) -> u64 {
        let (_, off) = varint::decode(&self.raw, 0).expect("validated at construction");
        let (codec, _) = varint::decode(&self.raw, off).expect("validated at construction");
        codec
    }

    pub fn multihash(&self) -> &[u8] {
        let (_, off) = varint::decode(&self.raw, 0).expect("validated at construction");
        let (_, off) = varint::decode(&self.raw, off).expect("validated at construction");
        &self.raw[off..]
    }

    /// The binary CIDv1 form.
    pub fn to_bytes(&self) -> Vec<u8> {
        self.raw.clone()
    }

    pub fn as_bytes(&self) -> &[u8] {
        &self.raw
    }
}

impl fmt::Display for Cid {
    /// Base32 multibase string form (`b` prefix).
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "b{}", BASE32_LOWER.encode(&self.raw))
    }
}

impl fmt::Debug for Cid {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "CID({self})")
    }
}

impl std::str::FromStr for Cid {
    type Err = CidError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Cid::parse(s)
    }
}

fn hex_decode(s: &str) -> Result<Vec<u8>, CidError> {
    if !s.len().is_multiple_of(2) {
        return Err(CidError::InvalidMultibase("odd-length hex".into()));
    }
    (0..s.len())
        .step_by(2)
        .map(|i| {
            u8::from_str_radix(&s[i..i + 2], 16)
                .map_err(|e| CidError::InvalidMultibase(e.to_string()))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIXTURE: &str = "bafyreiclp443lavogvhj3d2ob2cxbfuscni2k5jk7bebjzg7khl3esabwq";

    #[test]
    fn parse_and_display_round_trip() {
        let cid = Cid::parse(FIXTURE).unwrap();
        assert_eq!(cid.version(), 1);
        assert_eq!(cid.codec(), DAG_CBOR_CODEC);
        assert_eq!(cid.to_string(), FIXTURE);
        assert_eq!(Cid::from_bytes(&cid.to_bytes()).unwrap(), cid);
    }

    #[test]
    fn raw_codec_and_equality() {
        let cid = Cid::from_raw(b"hello world");
        assert_eq!(cid.codec(), RAW_CODEC);
        assert_eq!(Cid::parse(&cid.to_string()).unwrap(), cid);
    }

    #[test]
    fn cid_v0_is_rejected() {
        // a base58 CIDv0 (starts with Qm) has no multibase prefix we accept
        assert!(Cid::parse("QmbWqxBEKC3P8tqsKc98xmWNzrzDtRLMiMPL8wBuTGsMnR").is_err());
    }

    #[test]
    fn multihash_is_sha256_of_content() {
        let cid = Cid::from_dag_cbor(b"\xf6");
        assert_eq!(cid.multihash()[0], SHA2_256 as u8);
        assert_eq!(cid.multihash()[1], 32);
        assert_eq!(cid.multihash().len(), 34);
    }

    #[test]
    fn truncated_multihash_is_rejected() {
        let cid = Cid::from_dag_cbor(b"\xf6");
        let mut raw = cid.to_bytes();
        raw.pop();
        assert_eq!(Cid::from_bytes(&raw), Err(CidError::MultihashLength));
    }
}
