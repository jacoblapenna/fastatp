//! CARv1 (Content Addressable aRchive) encode/decode, mirroring
//! `fastatp/repo/car.py`.
//!
//! Layout: varint-prefixed DAG-CBOR header `{"roots": [CID], "version": 1}`,
//! then blocks as `varint(len(cid_bytes + data)) || cid_bytes || data`.
//! Block CIDs are re-derived and verified on decode — a CAR cannot smuggle a
//! block that does not hash to its claimed address.

use thiserror::Error;

use crate::cid::{Cid, DAG_CBOR_CODEC, RAW_CODEC};
use crate::dagcbor::{self, Value};
use crate::varint;

#[derive(Debug, Error)]
pub enum CarError {
    #[error("invalid CAR: {0}")]
    Invalid(String),
    #[error("invalid CAR header: {0}")]
    Header(String),
    #[error("truncated CAR section")]
    TruncatedSection,
    #[error("invalid CID in CAR section: {0}")]
    SectionCid(String),
    #[error("unsupported codec {0:#x} in CAR block")]
    UnsupportedCodec(u64),
    #[error("CAR block does not hash to its CID ({0})")]
    BlockHashMismatch(Cid),
}

/// Encode a CARv1: header roots + blocks in the given order.
pub fn encode_car(roots: &[Cid], blocks: &[(Cid, Vec<u8>)]) -> Vec<u8> {
    let header = Value::map(vec![
        (
            "roots",
            Value::Array(roots.iter().cloned().map(Value::Link).collect()),
        ),
        ("version", Value::Integer(1)),
    ]);
    let header_bytes = dagcbor::encode(&header).expect("static header shape");
    let mut out = Vec::new();
    varint::write(header_bytes.len() as u64, &mut out);
    out.extend_from_slice(&header_bytes);
    for (cid, data) in blocks {
        let cid_bytes = cid.to_bytes();
        varint::write((cid_bytes.len() + data.len()) as u64, &mut out);
        out.extend_from_slice(&cid_bytes);
        out.extend_from_slice(data);
    }
    out
}

/// Decode a CARv1 into (roots, blocks). Block CIDs are verified.
#[allow(clippy::type_complexity)]
pub fn decode_car(data: &[u8]) -> Result<(Vec<Cid>, Vec<(Cid, Vec<u8>)>), CarError> {
    let (header_len, mut offset) =
        varint::decode(data, 0).map_err(|e| CarError::Invalid(e.to_string()))?;
    let header_end = offset
        .checked_add(header_len as usize)
        .filter(|end| *end <= data.len())
        .ok_or(CarError::TruncatedSection)?;
    let header =
        dagcbor::decode(&data[offset..header_end]).map_err(|e| CarError::Header(e.to_string()))?;
    offset = header_end;
    if header.get("version").and_then(Value::as_integer) != Some(1) {
        return Err(CarError::Header("expected version 1".into()));
    }
    let roots: Vec<Cid> = match header.get("roots") {
        Some(Value::Array(items)) => items
            .iter()
            .map(|item| item.as_link().cloned())
            .collect::<Option<Vec<Cid>>>()
            .ok_or_else(|| CarError::Header("invalid roots".into()))?,
        _ => return Err(CarError::Header("invalid roots".into())),
    };

    let mut blocks: Vec<(Cid, Vec<u8>)> = Vec::new();
    while offset < data.len() {
        let (section_len, next) =
            varint::decode(data, offset).map_err(|e| CarError::Invalid(e.to_string()))?;
        let end = next
            .checked_add(section_len as usize)
            .filter(|end| *end <= data.len())
            .ok_or(CarError::TruncatedSection)?;
        let section = &data[next..end];
        offset = end;
        // CIDv1 prefix: version varint, codec varint, hash code, digest length
        let cid_end = (|| -> Result<usize, varint::VarintError> {
            let (_, pos) = varint::decode(section, 0)?;
            let (_, pos) = varint::decode(section, pos)?;
            let (_, pos) = varint::decode(section, pos)?;
            let (digest_len, pos) = varint::decode(section, pos)?;
            Ok(pos + digest_len as usize)
        })()
        .map_err(|e| CarError::SectionCid(e.to_string()))?;
        if cid_end > section.len() {
            return Err(CarError::TruncatedSection);
        }
        let cid = Cid::from_bytes(&section[..cid_end])
            .map_err(|e| CarError::SectionCid(e.to_string()))?;
        let block = &section[cid_end..];
        let expected = match cid.codec() {
            DAG_CBOR_CODEC => Cid::from_dag_cbor(block),
            RAW_CODEC => Cid::from_raw(block),
            other => return Err(CarError::UnsupportedCodec(other)),
        };
        if expected != cid {
            return Err(CarError::BlockHashMismatch(cid));
        }
        blocks.push((cid, block.to_vec()));
    }
    Ok((roots, blocks))
}
