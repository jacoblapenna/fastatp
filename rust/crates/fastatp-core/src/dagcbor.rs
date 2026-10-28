//! Canonical DAG-CBOR codec, mirroring `fastatp/dagcbor.py` exactly.
//!
//! Encodes the atproto data model: maps with string keys sorted length-first
//! then bytewise, minimal-width integer heads, tag 42 for CID links, no
//! floats, no indefinite-length items. The decoder is strict: it rejects
//! non-canonical input (wide integer heads, unsorted or duplicate map keys,
//! unknown tags, floats, extended simples), which matters when verifying
//! repo bytes from other implementations.
//!
//! Floats and non-string map keys are unrepresentable in [`Value`] by
//! construction — where Python bans them at runtime, the type system bans
//! them here. The JSON `$link`/`$bytes` dollar-forms live at the data-model
//! bridge (Python `datamodel.py`), not in this codec.

use thiserror::Error;

use crate::cid::Cid;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum DagCborError {
    #[error("truncated DAG-CBOR")]
    Truncated,
    #[error("indefinite-length items are not allowed")]
    IndefiniteLength,
    #[error("invalid additional info {0}")]
    InvalidAdditional(u8),
    #[error("non-minimal integer head")]
    NonMinimalHead,
    #[error("floats are not allowed in the atproto data model")]
    Float,
    #[error("extended simple values are not allowed")]
    ExtendedSimple,
    #[error("simple value {0} is not allowed")]
    BadSimple(u64),
    #[error("integer out of data-model range")]
    IntegerRange,
    #[error("invalid UTF-8 in string")]
    InvalidUtf8,
    #[error("map keys must be strings")]
    NonStringKey,
    #[error("duplicate map key")]
    DuplicateKey,
    #[error("map keys are not in canonical order")]
    UnsortedKeys,
    #[error("tag {0} is not allowed (only 42 for CID links)")]
    BadTag(u64),
    #[error("tag 42 content must be a byte string")]
    BadTagContent,
    #[error("tag 42 CID must use the identity multibase prefix")]
    BadTagPrefix,
    #[error("invalid CID in tag 42: {0}")]
    BadCid(String),
    #[error("trailing bytes after DAG-CBOR item")]
    TrailingBytes,
}

/// A tree in the atproto data model.
///
/// `Map` preserves insertion order; [`encode`] emits canonical order (keys
/// sorted length-first then bytewise) regardless, and map equality is
/// order-insensitive to match Python dict semantics.
#[derive(Clone, Debug)]
pub enum Value {
    Null,
    Bool(bool),
    Integer(i64),
    String(String),
    Bytes(Vec<u8>),
    Array(Vec<Value>),
    Map(Vec<(String, Value)>),
    Link(Cid),
}

impl Value {
    /// Convenience constructor for a map from `(key, value)` pairs.
    pub fn map<K: Into<String>>(entries: Vec<(K, Value)>) -> Value {
        Value::Map(entries.into_iter().map(|(k, v)| (k.into(), v)).collect())
    }

    /// Look a key up in a `Map` (first match); `None` for other variants.
    pub fn get(&self, key: &str) -> Option<&Value> {
        match self {
            Value::Map(entries) => entries.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::String(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_integer(&self) -> Option<i64> {
        match self {
            Value::Integer(i) => Some(*i),
            _ => None,
        }
    }

    pub fn as_bytes(&self) -> Option<&[u8]> {
        match self {
            Value::Bytes(b) => Some(b),
            _ => None,
        }
    }

    pub fn as_link(&self) -> Option<&Cid> {
        match self {
            Value::Link(c) => Some(c),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&[Value]> {
        match self {
            Value::Array(a) => Some(a),
            _ => None,
        }
    }
}

impl PartialEq for Value {
    fn eq(&self, other: &Value) -> bool {
        match (self, other) {
            (Value::Null, Value::Null) => true,
            (Value::Bool(a), Value::Bool(b)) => a == b,
            (Value::Integer(a), Value::Integer(b)) => a == b,
            (Value::String(a), Value::String(b)) => a == b,
            (Value::Bytes(a), Value::Bytes(b)) => a == b,
            (Value::Array(a), Value::Array(b)) => a == b,
            (Value::Link(a), Value::Link(b)) => a == b,
            (Value::Map(a), Value::Map(b)) => {
                // order-insensitive, like a Python dict
                a.len() == b.len()
                    && a.iter()
                        .all(|(k, v)| b.iter().any(|(bk, bv)| bk == k && bv == v))
            }
            _ => false,
        }
    }
}

impl Eq for Value {}

impl From<i64> for Value {
    fn from(v: i64) -> Value {
        Value::Integer(v)
    }
}

impl From<bool> for Value {
    fn from(v: bool) -> Value {
        Value::Bool(v)
    }
}

impl From<&str> for Value {
    fn from(v: &str) -> Value {
        Value::String(v.to_owned())
    }
}

impl From<String> for Value {
    fn from(v: String) -> Value {
        Value::String(v)
    }
}

impl From<Cid> for Value {
    fn from(v: Cid) -> Value {
        Value::Link(v)
    }
}

impl From<Vec<Value>> for Value {
    fn from(v: Vec<Value>) -> Value {
        Value::Array(v)
    }
}

const MAX_HEAD_1: u64 = 24;
const MAX_HEAD_2: u64 = 0x100;
const MAX_HEAD_4: u64 = 0x10000;
const MAX_HEAD_8: u64 = 0x1_0000_0000;

fn write_head(major: u8, value: u64, out: &mut Vec<u8>) {
    if value < MAX_HEAD_1 {
        out.push((major << 5) | value as u8);
    } else if value < MAX_HEAD_2 {
        out.push((major << 5) | 24);
        out.push(value as u8);
    } else if value < MAX_HEAD_4 {
        out.push((major << 5) | 25);
        out.extend_from_slice(&(value as u16).to_be_bytes());
    } else if value < MAX_HEAD_8 {
        out.push((major << 5) | 26);
        out.extend_from_slice(&(value as u32).to_be_bytes());
    } else {
        out.push((major << 5) | 27);
        out.extend_from_slice(&value.to_be_bytes());
    }
}

fn encode_into(value: &Value, out: &mut Vec<u8>) -> Result<(), DagCborError> {
    match value {
        Value::Null => out.push(0xf6),
        Value::Bool(true) => out.push(0xf5),
        Value::Bool(false) => out.push(0xf4),
        Value::Integer(i) => {
            if *i >= 0 {
                write_head(0, *i as u64, out);
            } else {
                write_head(1, !(*i) as u64, out); // !(i) == -1 - i in two's complement
            }
        }
        Value::String(s) => {
            write_head(3, s.len() as u64, out);
            out.extend_from_slice(s.as_bytes());
        }
        Value::Bytes(b) => {
            write_head(2, b.len() as u64, out);
            out.extend_from_slice(b);
        }
        Value::Link(cid) => {
            let bytes = cid.as_bytes();
            out.extend_from_slice(&[0xd8, 0x2a]); // tag(42)
            write_head(2, bytes.len() as u64 + 1, out);
            out.push(0x00); // identity multibase prefix inside tag 42
            out.extend_from_slice(bytes);
        }
        Value::Array(items) => {
            write_head(4, items.len() as u64, out);
            for item in items {
                encode_into(item, out)?;
            }
        }
        Value::Map(entries) => {
            let mut sorted: Vec<(&[u8], &Value)> =
                entries.iter().map(|(k, v)| (k.as_bytes(), v)).collect();
            sorted.sort_by_key(|(k, _)| (k.len(), *k));
            for pair in sorted.windows(2) {
                if pair[0].0 == pair[1].0 {
                    return Err(DagCborError::DuplicateKey);
                }
            }
            write_head(5, sorted.len() as u64, out);
            for (raw_key, item) in sorted {
                write_head(3, raw_key.len() as u64, out);
                out.extend_from_slice(raw_key);
                encode_into(item, out)?;
            }
        }
    }
    Ok(())
}

/// Encode a data-model tree to canonical DAG-CBOR bytes.
pub fn encode(value: &Value) -> Result<Vec<u8>, DagCborError> {
    let mut out = Vec::new();
    encode_into(value, &mut out)?;
    Ok(out)
}

// --- strict decoder ---------------------------------------------------------

struct Decoder<'a> {
    data: &'a [u8],
    pos: usize,
}

impl<'a> Decoder<'a> {
    fn byte(&mut self) -> Result<u8, DagCborError> {
        let byte = *self.data.get(self.pos).ok_or(DagCborError::Truncated)?;
        self.pos += 1;
        Ok(byte)
    }

    fn read(&mut self, n: usize) -> Result<&'a [u8], DagCborError> {
        if self.pos + n > self.data.len() {
            return Err(DagCborError::Truncated);
        }
        let chunk = &self.data[self.pos..self.pos + n];
        self.pos += n;
        Ok(chunk)
    }

    fn head(&mut self) -> Result<(u8, u64), DagCborError> {
        let initial = self.byte()?;
        let major = initial >> 5;
        let additional = initial & 0x1f;
        if additional < 24 {
            return Ok((major, u64::from(additional)));
        }
        if additional == 31 {
            return Err(DagCborError::IndefiniteLength);
        }
        if additional > 27 {
            return Err(DagCborError::InvalidAdditional(additional));
        }
        if major == 7 {
            // 0xf8 (extended simple) would allow non-canonical encodings of
            // false/true/null; 0xf9-0xfb are floats — none are allowed
            if additional == 24 {
                return Err(DagCborError::ExtendedSimple);
            }
            return Err(DagCborError::Float);
        }
        let width = 1usize << (additional - 24);
        let mut value: u64 = 0;
        for byte in self.read(width)? {
            value = (value << 8) | u64::from(*byte);
        }
        let minimum = match additional {
            24 => MAX_HEAD_1,
            25 => MAX_HEAD_2,
            26 => MAX_HEAD_4,
            _ => MAX_HEAD_8,
        };
        if value < minimum {
            return Err(DagCborError::NonMinimalHead);
        }
        Ok((major, value))
    }

    fn item(&mut self) -> Result<Value, DagCborError> {
        let (major, value) = self.head()?;
        match major {
            0 => i64::try_from(value)
                .map(Value::Integer)
                .map_err(|_| DagCborError::IntegerRange),
            1 => i64::try_from(value)
                .map(|v| Value::Integer(-1 - v))
                .map_err(|_| DagCborError::IntegerRange),
            2 => Ok(Value::Bytes(self.read(value as usize)?.to_vec())),
            3 => {
                let raw = self.read(value as usize)?;
                std::str::from_utf8(raw)
                    .map(|s| Value::String(s.to_owned()))
                    .map_err(|_| DagCborError::InvalidUtf8)
            }
            4 => {
                let mut items = Vec::with_capacity(value.min(4096) as usize);
                for _ in 0..value {
                    items.push(self.item()?);
                }
                Ok(Value::Array(items))
            }
            5 => self.map(value),
            6 => self.tag(value),
            _ => match value {
                20 => Ok(Value::Bool(false)),
                21 => Ok(Value::Bool(true)),
                22 => Ok(Value::Null),
                other => Err(DagCborError::BadSimple(other)),
            },
        }
    }

    fn map(&mut self, count: u64) -> Result<Value, DagCborError> {
        let mut entries: Vec<(String, Value)> = Vec::with_capacity(count.min(4096) as usize);
        let mut prev_key: Option<&'a [u8]> = None;
        for _ in 0..count {
            let (major, length) = self.head()?;
            if major != 3 {
                return Err(DagCborError::NonStringKey);
            }
            let raw_key = self.read(length as usize)?;
            if let Some(prev) = prev_key {
                let this = (raw_key.len(), raw_key);
                let last = (prev.len(), prev);
                if this == last {
                    return Err(DagCborError::DuplicateKey);
                }
                if this < last {
                    return Err(DagCborError::UnsortedKeys);
                }
            }
            prev_key = Some(raw_key);
            let key = std::str::from_utf8(raw_key)
                .map_err(|_| DagCborError::InvalidUtf8)?
                .to_owned();
            let value = self.item()?;
            entries.push((key, value));
        }
        Ok(Value::Map(entries))
    }

    fn tag(&mut self, tag: u64) -> Result<Value, DagCborError> {
        if tag != 42 {
            return Err(DagCborError::BadTag(tag));
        }
        let (major, length) = self.head()?;
        if major != 2 {
            return Err(DagCborError::BadTagContent);
        }
        let payload = self.read(length as usize)?;
        let Some((0x00, cid_bytes)) = payload.split_first() else {
            return Err(DagCborError::BadTagPrefix);
        };
        Cid::from_bytes(cid_bytes)
            .map(Value::Link)
            .map_err(|e| DagCborError::BadCid(e.to_string()))
    }
}

/// Strictly decode canonical DAG-CBOR bytes into a data-model tree.
pub fn decode(data: &[u8]) -> Result<Value, DagCborError> {
    let mut decoder = Decoder { data, pos: 0 };
    let value = decoder.item()?;
    if decoder.pos != data.len() {
        return Err(DagCborError::TrailingBytes);
    }
    Ok(value)
}

/// Decode one DAG-CBOR item, returning it and the remaining bytes.
pub fn decode_first(data: &[u8]) -> Result<(Value, &[u8]), DagCborError> {
    let mut decoder = Decoder { data, pos: 0 };
    let value = decoder.item()?;
    Ok((value, &data[decoder.pos..]))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn enc(value: &Value) -> Vec<u8> {
        encode(value).unwrap()
    }

    #[test]
    fn canonical_encoding_rules() {
        // map keys sort length-first, then bytewise, regardless of insertion
        let a = Value::map(vec![("bb", 1.into()), ("a", 2.into()), ("ab", 3.into())]);
        let b = Value::map(vec![("a", 2.into()), ("ab", 3.into()), ("bb", 1.into())]);
        assert_eq!(enc(&a), enc(&b));
        let two = enc(&Value::map(vec![("bb", 1.into()), ("a", 2.into())]));
        let a_at = two.windows(2).position(|w| w == b"\x61a").unwrap();
        let bb_at = two.windows(3).position(|w| w == b"\x62bb").unwrap();
        assert!(a_at < bb_at);
        // ints are minimal width
        assert_eq!(enc(&23.into()), b"\x17");
        assert_eq!(enc(&24.into()), b"\x18\x18");
        assert_eq!(enc(&(-1).into()), b"\x20");
        assert_eq!(enc(&true.into()), b"\xf5");
        assert_eq!(enc(&Value::Null), b"\xf6");
        // duplicate keys are an encode error too
        let dupes = Value::map(vec![("a", 1.into()), ("a", 2.into())]);
        assert_eq!(encode(&dupes), Err(DagCborError::DuplicateKey));
    }

    #[test]
    fn strict_decode() {
        // indefinite-length map
        assert_eq!(
            decode(b"\xbf\x61\x61\x01\xff"),
            Err(DagCborError::IndefiniteLength)
        );
        // non-minimal int head
        assert_eq!(decode(b"\x18\x17"), Err(DagCborError::NonMinimalHead));
        // trailing garbage
        assert_eq!(decode(b"\x01\x01"), Err(DagCborError::TrailingBytes));
        // unsorted keys
        assert_eq!(
            decode(b"\xa2\x61b\x02\x61a\x01"),
            Err(DagCborError::UnsortedKeys)
        );
        // duplicate keys
        assert_eq!(
            decode(b"\xa2\x61a\x01\x61a\x02"),
            Err(DagCborError::DuplicateKey)
        );
        // a canonical map round-trips
        let value = Value::map(vec![("a", 1.into()), ("b", 2.into())]);
        assert_eq!(decode(&enc(&value)).unwrap(), value);
    }

    #[test]
    fn non_canonical_simples_and_floats_rejected() {
        for bad in [&b"\xf8\x14"[..], b"\xf8\x15", b"\xf8\x16", b"\xf8\x20"] {
            assert_eq!(decode(bad), Err(DagCborError::ExtendedSimple));
        }
        assert_eq!(decode(b"\xf9\x3c\x00"), Err(DagCborError::Float));
        assert_eq!(decode(b"\xfa\x3f\x80\x00\x00"), Err(DagCborError::Float));
        assert_eq!(
            decode(&[b"\xfb".as_slice(), &[0u8; 8]].concat()),
            Err(DagCborError::Float)
        );
        assert_eq!(decode(b"\xf4").unwrap(), Value::Bool(false));
        assert_eq!(decode(b"\xf5").unwrap(), Value::Bool(true));
        assert_eq!(decode(b"\xf6").unwrap(), Value::Null);
    }

    #[test]
    fn tag_42_round_trip_and_rejections() {
        let cid = Cid::from_dag_cbor(b"\xf6");
        let value = Value::map(vec![("prev", Value::Link(cid.clone()))]);
        let encoded = enc(&value);
        assert!(encoded.windows(2).any(|w| w == [0xd8, 0x2a]));
        let decoded = decode(&encoded).unwrap();
        assert_eq!(decoded.get("prev").unwrap().as_link().unwrap(), &cid);
        // tag other than 42
        assert!(matches!(
            decode(b"\xd8\x2b\x41\x00"),
            Err(DagCborError::BadTag(43))
        ));
        // tag 42 without the identity prefix (36-byte CID: head 0x58 0x24)
        let cid_bytes = cid.to_bytes();
        let mut no_prefix = vec![0xd8, 0x2a, 0x58, cid_bytes.len() as u8];
        no_prefix.extend_from_slice(&cid_bytes);
        assert_eq!(decode(&no_prefix), Err(DagCborError::BadTagPrefix));
    }

    #[test]
    fn negative_integers_round_trip() {
        for i in [-1i64, -24, -25, -256, -65536, i64::MIN + 1, i64::MIN] {
            let encoded = enc(&i.into());
            assert_eq!(decode(&encoded).unwrap(), Value::Integer(i));
        }
    }

    #[test]
    fn decode_first_returns_remainder() {
        let mut data = enc(&1.into());
        data.extend_from_slice(b"rest");
        let (value, rest) = decode_first(&data).unwrap();
        assert_eq!(value, Value::Integer(1));
        assert_eq!(rest, b"rest");
    }

    #[test]
    fn map_equality_is_order_insensitive() {
        let a = Value::map(vec![("x", 1.into()), ("y", 2.into())]);
        let b = Value::map(vec![("y", 2.into()), ("x", 1.into())]);
        assert_eq!(a, b);
    }
}
