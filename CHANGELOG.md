# Changelog

All notable changes to FastATP are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-28

First release.

### Identity
- `did:web` and `did:key` identities with DID document construction, Multikey
  encoding, and resolvers (`WebResolver`, `PLCResolver` for resolve-only
  `did:plc`, `ContinuityResolver`, and `chain`).
- ECDSA over secp256k1 and P-256 with low-S normalized compact signatures;
  high-S and DER signatures are rejected on verification.
- Self-certifying `did:web` continuity: a hash-anchored twin and a
  rotation-signed chain let an identity survive a lapsed or hijacked domain
  without a registry. Bring-your-own `did:web` documents are supported.
- `DIDServer` for hosting DID documents.

### Lexicon layer
- Define any atproto-conforming lexicon as a Pydantic model using a type
  palette (byte-length and grapheme constraints, `cid-link`, `bytes`, `blob`,
  `$type`-discriminated unions).
- `Lexicon.to_lexicon_json()` emits a valid `lexicon: 1` document; the schema
  bridge round-trips the full `com.atproto.*` corpus.

### Records and repositories
- Strict canonical DAG-CBOR codec and CIDv1, byte-identical to the interop
  vectors, which are vendored under `tests/vectors/`.
- `Repo`: a signed Merkle Search Tree of present state with per-commit
  garbage collection, CAR export and import, and `prove()` record proofs
  that verify with nothing but a DID resolver.
- Optional durable block storage on the standard library `sqlite3` module.
- Optional blob store.

### Peers and transport
- `Peer.verify()` for proof slices, with expected-signer binding, rotation-key
  rejection, and an authorization hook.
- `PeerServer`: a `com.atproto.sync.subscribeRepos`-shaped firehose over
  WebSocket with cursor resume, multi-repo routing by DID, and HTTP sync
  routes. `RepoMirror` verifies every commit against the producer's DID.
- `PeerApp`: route lexicons to handlers, emit signed replies, and reply on
  private one-shot channels (`PrivateChannel`, `Attestation`).

### Authorization
- Signed capability records decide who is allowed; a WireGuard peer set
  enforces it, and a reconciler keeps the two in lockstep with a bilateral
  kill switch.
- Browser admission gate and presence-based pen delegation for browser peers.

### Browser peer
- A WebAssembly build of the wire-format core (Rust) ships pre-built inside
  the package. Installing never needs a Rust toolchain.

[Unreleased]: https://github.com/jacoblapenna/fastatp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jacoblapenna/fastatp/releases/tag/v0.1.0
