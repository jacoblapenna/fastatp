//! Unsigned LEB128 varints (multiformats "unsigned-varint").
//!
//! Mirrors `fastatp.lex.types.read_varint` / `fastatp.cid._varint`: values are
//! `u64`, continuation bit 0x80, and a varint longer than 64 bits of payload
//! is rejected.

use thiserror::Error;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum VarintError {
    #[error("truncated varint")]
    Truncated,
    #[error("varint too long")]
    TooLong,
}

/// Append `value` to `out` as an unsigned varint.
pub fn write(value: u64, out: &mut Vec<u8>) {
    let mut value = value;
    loop {
        let byte = (value & 0x7f) as u8;
        value >>= 7;
        if value != 0 {
            out.push(byte | 0x80);
        } else {
            out.push(byte);
            return;
        }
    }
}

/// Encode `value` as an unsigned varint.
pub fn encode(value: u64) -> Vec<u8> {
    let mut out = Vec::with_capacity(10);
    write(value, &mut out);
    out
}

/// Read one varint from `data` at `offset`; returns `(value, new_offset)`.
pub fn decode(data: &[u8], offset: usize) -> Result<(u64, usize), VarintError> {
    let mut value: u64 = 0;
    let mut shift: u32 = 0;
    let mut offset = offset;
    loop {
        let byte = *data.get(offset).ok_or(VarintError::Truncated)?;
        offset += 1;
        value |= u64::from(byte & 0x7f) << shift;
        if byte & 0x80 == 0 {
            return Ok((value, offset));
        }
        shift += 7;
        if shift > 63 {
            return Err(VarintError::TooLong);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips() {
        for value in [0u64, 1, 23, 127, 128, 300, 0x71, 0xffff_ffff, u64::MAX] {
            let encoded = encode(value);
            assert_eq!(decode(&encoded, 0), Ok((value, encoded.len())));
        }
    }

    #[test]
    fn known_bytes() {
        assert_eq!(encode(0), vec![0x00]);
        assert_eq!(encode(127), vec![0x7f]);
        assert_eq!(encode(128), vec![0x80, 0x01]);
        assert_eq!(encode(300), vec![0xac, 0x02]);
    }

    #[test]
    fn truncated_and_too_long_fail() {
        assert_eq!(decode(&[0x80], 0), Err(VarintError::Truncated));
        assert_eq!(decode(&[], 0), Err(VarintError::Truncated));
        assert_eq!(decode(&[0x80; 11], 0), Err(VarintError::TooLong));
    }
}
