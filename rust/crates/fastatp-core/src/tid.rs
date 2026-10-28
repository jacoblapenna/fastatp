//! TID (timestamp identifier) codec + monotonic clock.
//!
//! A TID is a 64-bit value — microseconds since the UNIX epoch (53 bits, top
//! bit zero) followed by a 10-bit clock identifier — encoded as 13 characters
//! of base32-sortable. TIDs are the record-key scheme for most collections
//! and the `rev` clock for repo commits. Mirrors `fastatp/tid.py`.

use thiserror::Error;

/// The base32-sortable alphabet (NOT RFC 4648): digits sort before letters.
pub const BASE32_SORTABLE: &[u8; 32] = b"234567abcdefghijklmnopqrstuvwxyz";

#[derive(Debug, Error, PartialEq, Eq)]
pub enum TidError {
    #[error("TID syntax not valid")]
    Syntax,
    #[error("TID timestamp must fit in 53 bits")]
    TimestampRange,
    #[error("TID clock id must fit in 10 bits")]
    ClockIdRange,
}

fn char_value(c: u8) -> Option<u64> {
    BASE32_SORTABLE
        .iter()
        .position(|&b| b == c)
        .map(|i| i as u64)
}

/// Encode microseconds-since-epoch + 10-bit clock id as a 13-char TID.
pub fn encode_tid(timestamp_us: u64, clock_id: u16) -> Result<String, TidError> {
    if timestamp_us >= 1 << 53 {
        return Err(TidError::TimestampRange);
    }
    if clock_id >= 1 << 10 {
        return Err(TidError::ClockIdRange);
    }
    let value = (timestamp_us << 10) | u64::from(clock_id);
    // 13 chars x 5 bits = 65 encoded bits for a 64-bit value: the top bit is
    // always zero, which is why the first character is range-restricted
    let mut out = String::with_capacity(13);
    let mut shift: i32 = 60;
    while shift >= 0 {
        out.push(BASE32_SORTABLE[((value >> shift) & 0x1F) as usize] as char);
        shift -= 5;
    }
    Ok(out)
}

/// Syntax check per the interop rules: 13 chars, alphabet-only, and the first
/// character's value < 16 (the 64-bit value's top bit is zero).
pub fn validate_tid(tid: &str) -> Result<(), TidError> {
    let bytes = tid.as_bytes();
    if bytes.len() != 13 {
        return Err(TidError::Syntax);
    }
    let first = char_value(bytes[0]).ok_or(TidError::Syntax)?;
    if first >= 16 {
        return Err(TidError::Syntax); // first char restricted to [234567abcdefghij]
    }
    if bytes[1..].iter().any(|&b| char_value(b).is_none()) {
        return Err(TidError::Syntax);
    }
    Ok(())
}

/// Parse a TID string into `(timestamp_us, clock_id)`.
pub fn decode_tid(tid: &str) -> Result<(u64, u16), TidError> {
    validate_tid(tid)?;
    let mut value: u64 = 0;
    for &b in tid.as_bytes() {
        value = (value << 5) | char_value(b).expect("validated");
    }
    Ok((value >> 10, (value & 0x3FF) as u16))
}

/// Monotonic TID source: strictly increasing even within one microsecond.
pub struct TidClock {
    clock_id: u16,
    #[cfg(not(target_arch = "wasm32"))]
    last_us: std::sync::Mutex<u64>,
}

impl TidClock {
    /// A clock with an explicit 10-bit identifier (masked to range).
    pub fn new(clock_id: u16) -> Self {
        Self {
            clock_id: clock_id & 0x3FF,
            #[cfg(not(target_arch = "wasm32"))]
            last_us: std::sync::Mutex::new(0),
        }
    }

    /// A clock with an OS-random identifier.
    #[cfg(feature = "rand")]
    pub fn random() -> Self {
        use rand_core::RngCore;
        Self::new((rand_core::OsRng.next_u32() % 1024) as u16)
    }

    pub fn clock_id(&self) -> u16 {
        self.clock_id
    }

    /// A fresh TID, strictly greater than every TID this clock has produced.
    /// Native-only: the browser peer stamps time via its own environment;
    /// the pure codec above is target-agnostic.
    #[cfg(not(target_arch = "wasm32"))]
    pub fn now(&self) -> String {
        use std::time::{SystemTime, UNIX_EPOCH};
        let mut last = self.last_us.lock().expect("tid clock lock poisoned");
        let now_us = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_micros() as u64)
            .unwrap_or(0);
        let timestamp_us = now_us.max(*last + 1);
        *last = timestamp_us;
        encode_tid(timestamp_us, self.clock_id).expect("clock produces in-range TIDs")
    }
}

/// A fresh, monotonically-increasing TID from the process-wide clock.
#[cfg(all(feature = "rand", not(target_arch = "wasm32")))]
pub fn generate_tid() -> String {
    use std::sync::OnceLock;
    static CLOCK: OnceLock<TidClock> = OnceLock::new();
    CLOCK.get_or_init(TidClock::random).now()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encode_decode_round_trip() {
        let tid = encode_tid(1_700_000_000_000_000, 511).unwrap();
        assert_eq!(tid.len(), 13);
        assert_eq!(decode_tid(&tid).unwrap(), (1_700_000_000_000_000, 511));
    }

    #[test]
    fn range_limits_enforced() {
        assert_eq!(encode_tid(1 << 53, 0), Err(TidError::TimestampRange));
        assert_eq!(encode_tid(0, 1024), Err(TidError::ClockIdRange));
        assert_eq!(encode_tid(0, 0).unwrap(), "2222222222222");
        // max legal value is (2^53-1)<<10 | 1023 = 2^63-1: top bit zero, so
        // the first character tops out at alphabet[7] = 'b'
        assert_eq!(encode_tid((1 << 53) - 1, 1023).unwrap(), "bzzzzzzzzzzzz");
    }

    #[test]
    fn tids_sort_chronologically() {
        let a = encode_tid(1_000, 1023).unwrap();
        let b = encode_tid(1_001, 0).unwrap();
        assert!(a < b);
    }

    #[cfg(all(feature = "rand", not(target_arch = "wasm32")))]
    #[test]
    fn clock_is_strictly_monotonic() {
        let clock = TidClock::random();
        let mut previous = clock.now();
        for _ in 0..100 {
            let next = clock.now();
            assert!(next > previous);
            previous = next;
        }
    }
}
