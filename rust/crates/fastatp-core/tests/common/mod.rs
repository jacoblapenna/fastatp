//! Shared helpers for the interop-vector integration tests.

use std::path::PathBuf;

/// Directory holding the CC-0 atproto interop vectors.
///
/// Mirrors `tests/conftest.py`: the in-repo vendored copy (`tests/vectors/`,
/// see its PROVENANCE.md) is authoritative and always present in a checkout,
/// so CI exercises the vectors on a fresh clone. The gitignored
/// `reference/atproto-interop-tests` clone is only a fallback.
///
/// Panics when neither exists: with the vectors vendored, a missing directory
/// is a broken checkout, and skipping would let the conformance tests pass
/// without checking anything.
pub fn vectors_dir() -> PathBuf {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let vendored = root.join("tests/vectors");
    if vendored.is_dir() {
        return vendored;
    }
    let clone = root.join("reference/atproto-interop-tests");
    if clone.is_dir() {
        return clone;
    }
    panic!(
        "interop vectors not found: expected {} (vendored) or {} (reference clone)",
        vendored.display(),
        clone.display()
    );
}
