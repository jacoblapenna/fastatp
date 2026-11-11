//! The challenge: the one knock a browser pen makes at a host. The pen signs
//! a host-issued nonce naming the repo it wants, and the host decides what
//! that pen is to *that* repo: its own pen (owner pull, if the host allows
//! it) or a reader it admits by capability record (the doorman). The knock
//! takes only the pen and the DID of the repo asked for, so no repo needs
//! to be loaded to make it.
//!
//! The message is fixed and labelled, so a pen can never be tricked into
//! signing something else — a commit, a proof, another repo's challenge:
//!
//! ```text
//! fastatp-challenge-v1\n<repo DID>\n<nonce>
//! ```
//!
//! Neither field may contain a newline (a DID is `[A-Za-z0-9._:%-]`, a nonce
//! is base64url), so the framing is unambiguous. The Python package builds
//! the identical bytes; `tests` pins them as a vector.

use thiserror::Error;

/// First line of every challenge message. Bump the suffix, never reuse it.
pub const CHALLENGE_LABEL: &str = "fastatp-challenge-v1";

/// A nonce is what the host mints (`secrets.token_urlsafe(32)` → 43 chars).
/// Accept any base64url string of a sane length, so a host is free to mint
/// longer ones; refuse the empty string and anything outside the alphabet.
pub const MAX_NONCE_LEN: usize = 256;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum ChallengeError {
    #[error("not a valid DID: {0:?}")]
    BadDid(String),
    #[error("not a valid nonce (base64url, 1..={MAX_NONCE_LEN} chars): {0:?}")]
    BadNonce(String),
}

/// The atproto DID syntax (spec: did): `did:<method>:<id>`, method lowercase
/// letters, id from `[A-Za-z0-9._:%-]`, not ending in `:` or `%`. Syntax only;
/// whether the DID resolves is the app's business.
pub fn is_valid_did_syntax(did: &str) -> bool {
    did.len() <= 2048
        && did.strip_prefix("did:").is_some_and(|rest| {
            rest.split_once(':').is_some_and(|(method, id)| {
                !method.is_empty()
                    && method.bytes().all(|b| b.is_ascii_lowercase())
                    && !id.is_empty()
                    && id
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b".:_%-".contains(&b))
                    && !id.ends_with(':')
                    && !id.ends_with('%')
            })
        })
}

/// Unpadded base64url, non-empty, at most [`MAX_NONCE_LEN`] bytes.
pub fn is_valid_nonce(nonce: &str) -> bool {
    !nonce.is_empty()
        && nonce.len() <= MAX_NONCE_LEN
        && nonce
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

/// The bytes a pen signs to knock for `repo_did`. Checks both fields first.
pub fn challenge_message(repo_did: &str, nonce: &str) -> Result<Vec<u8>, ChallengeError> {
    if !is_valid_did_syntax(repo_did) {
        return Err(ChallengeError::BadDid(repo_did.to_string()));
    }
    if !is_valid_nonce(nonce) {
        return Err(ChallengeError::BadNonce(nonce.to_string()));
    }
    Ok(format!("{CHALLENGE_LABEL}\n{repo_did}\n{nonce}").into_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;

    // The vector the Python package's test asserts byte for byte.
    const DID: &str = "did:web:shelf.example:abc";
    const NONCE: &str = "n0nce_-A";
    const EXPECTED: &[u8] = b"fastatp-challenge-v1\ndid:web:shelf.example:abc\nn0nce_-A";

    #[test]
    fn message_is_label_did_nonce_newline_separated() {
        assert_eq!(challenge_message(DID, NONCE).unwrap(), EXPECTED);
    }

    #[test]
    fn a_did_key_repo_works_too() {
        let did = "did:key:zDnaerx9CtbPJ1q36T5Ln3wYt3MQYeGRG5ehnPAmxcf5mDZpv";
        let message = challenge_message(did, NONCE).unwrap();
        assert!(message.starts_with(b"fastatp-challenge-v1\ndid:key:zDnaer"));
    }

    #[test]
    fn message_can_never_look_like_dag_cbor() {
        // a commit is a dag-cbor map; its first byte is 0xa0..=0xbf, never ASCII
        let message = challenge_message(DID, NONCE).unwrap();
        assert!(message[0].is_ascii_alphabetic());
    }

    #[test]
    fn bad_dids_refused() {
        for did in [
            "",
            "shelf.example",
            "did:",
            "did:web:",
            "did:Web:x",
            "did:web:x:",
            "did:web:x%",
        ] {
            assert_eq!(
                challenge_message(did, NONCE),
                Err(ChallengeError::BadDid(did.to_string())),
                "{did:?}"
            );
        }
        let too_long = format!("did:web:{}", "a".repeat(2048));
        assert!(matches!(
            challenge_message(&too_long, NONCE),
            Err(ChallengeError::BadDid(_))
        ));
    }

    #[test]
    fn bad_nonces_refused() {
        for nonce in [
            "",
            "has space",
            "has\nnewline",
            "padded==",
            "plus+slash/",
            "did:web:x",
        ] {
            assert_eq!(
                challenge_message(DID, nonce),
                Err(ChallengeError::BadNonce(nonce.to_string())),
                "{nonce:?}"
            );
        }
        assert!(challenge_message(DID, &"A".repeat(MAX_NONCE_LEN)).is_ok());
        assert!(challenge_message(DID, &"A".repeat(MAX_NONCE_LEN + 1)).is_err());
    }

    #[test]
    fn the_gate_shaped_nonce_passes() {
        // secrets.token_urlsafe(32): 43 base64url chars, unpadded
        let nonce = "Zm9vYmFyYmF6cXV4cXV1eHF1dXhxdXV4cXV1eHF1dXh";
        assert_eq!(nonce.len(), 43);
        assert!(challenge_message(DID, nonce).is_ok());
    }
}
