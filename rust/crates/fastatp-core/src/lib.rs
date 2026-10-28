//! FastATP wire-format core: the second implementation of the
//! bytes on the wire, proven against the same `reference/` interop vectors as
//! the Python package and cross-verified against it in CI.
//!
//! Scope: canonical DAG-CBOR, CIDv1, TID, MST with record-proof
//! paths, signed commit v3, CARv1, and a present-state repo — socket-free and
//! target-agnostic (native + wasm32). Signing goes through the [`crypto::Signer`]
//! trait: a software signer natively; WebCrypto in the browser bindings
//! so a pen never exists in linear memory.

pub mod car;
pub mod cid;
pub mod commit;
pub mod continuity;
pub mod crypto;
pub mod dagcbor;
pub mod mst;
pub mod repo;
pub mod tid;
pub mod varint;
