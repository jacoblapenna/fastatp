# Changelog

All notable changes to FastATP are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
uses [Semantic Versioning](https://semver.org/) the way Cargo and npm apply
it before 1.0:

- **A minor release may break** (0.1 to 0.2 can change or remove public API).
  Breaking changes are listed under a **Breaking** heading.
- **A patch release never breaks** (0.2.0 to 0.2.1 only fixes or adds).
- **1.0.0 means stable**, and normal Semantic Versioning applies from there.

To upgrade safely within one minor line, pin with `~=`, for example
`fastatp~=0.2.0` (at least 0.2.0, below 0.3).

## [Unreleased]

### Breaking
- The browser bundle's `BrowserPeer.sign_challenge(nonce)` is removed. The
  knock at a host is now one free function, `sign_challenge(key_pair, nonce,
  repo_did)`, which needs no repo loaded and names the repo the pen is
  asking for. The browser gate verifies the new labelled message
  (`fastatp-challenge-v1\n<repo DID>\n<nonce>`), so a 0.2 client's bare-nonce
  signature is refused by a 0.3 host.
- A gated repo a caller may not read is now answered exactly like a DID the
  server does not host: `RepoNotFound` (404) with the same body, on every
  single-repo sync route and the firehose, and the caller is resolved
  before the host is looked up so the two cases run the same checks (a
  knock for an unhosted DID still burns its nonce). Previously a refused
  gated read answered `NotAuthorized` (403), which told strangers which
  DIDs the server hosts. `listRepos` already hid them.

### Added
- Browser bundle: `generate_pen()`, `pen_did(key_pair)`,
  `BrowserPeer.create(did, key_pair)`, `BrowserPeer.restore(car, key_pair,
  verify?)` and `sign_challenge(key_pair, nonce, repo_did)`. Pens cross the
  boundary as non-extractable WebCrypto handles only; every entry point
  refuses an extractable one.
- Browser bundle: `connection_zero(domain, key_pair?)` takes the pen to sign
  up with (the one `generate_pen()` minted and the app stored), so the first
  device's pen can be kept; omitted, it mints one as before.
  `rotation_did_from_secret(hex)` derives the rotation `did:key` from the
  paper secret for a typed-back check, entirely on the page.
- Browser bundle: `rotate_pen(chain_json, secret_hex, key_pair)`, recovery
  on the page: the paper secret signs a new chain page naming a new pen,
  returned as the longer chain for the app to POST to the shelf. A wrong
  secret, an extractable pen or a chain that does not verify is refused
  before the chain page is signed, and every buffer the bindings hold the
  secret in is wiped on every exit path (the hex is decoded into a fixed
  buffer, so a malformed input leaves nothing behind either).
- `Repo.import_car(..., newer_than=rev)` refuses a checkpoint whose head rev
  is not strictly newer, raising `StaleCheckpoint` before any state changes,
  so a router re-hosting pushed CARs can refuse a replay in the one parse
  that verifies it. The hosted-peer example uses it.
- `fastatp.server.browser_gate.challenge_message(repo_did, nonce)`, the
  bytes a pen signs; the Rust core builds the identical bytes.
- `PeerServer.register(repo, owner_pull=...)`: a host serves a gated repo
  to the pen that writes it, with no capability record. Off by default.
  `True` admits the `did:key` whose key verifies the head commit; an
  `OwnerRule` callable names the current pen when the host knows it (its
  chain head), so a freshly rotated pen pulls before it has pushed.
  Owner pull decides who reads; a frozen (penless) hosted repo, which will
  be required for it, is planned separately.
- `ResolvedCaller(did, proof)`: a `CallerResolver` may now say how it knows
  who is asking (`"signature"`, or `"repo:<did>"` for a tunnel binding or
  delegation chain rooted in that repo). A bare DID still works for the
  capability rule. Owner pull requires a proof that is a signature or this
  repo's own records, so a capability written in *another* hosted repo can
  never make its author the owner here. The tunnel resolver and the browser
  gate return scoped callers. `listRepos` resolves the caller once for the
  whole listing, so a browser knock (single-use) lists every gated repo the
  identity holds.
- The hosted-peer example (`examples/hosted_peer/`) runs the whole
  lifecycle: a kept pen signs up, reads its own repo back as its owner,
  enrolls devices that sync as the identity, severs one, then loses every
  device and recovers with the paper secret onto a new pen, which pulls,
  prunes and pushes while the old pen is refused. No `self` capability
  anywhere.

### Fixed
- A repo restored from a CAR, or reopened from a durable head, on a machine
  whose clock is behind that head no longer writes a lower rev: the clock
  observes the restored rev (Rust core `RevClock::observe`; Python
  `TidClock.observe`, called by `Repo.import_car` and the head load).

### Docs
- `docs/decisions/0001-browser-pen-seam.md`, the first public decision
  record: pens as handles, each device writes its own repo, one knock,
  owner pull (revising HOSTED-PEERS.md's "every device is an admitted
  subscriber"), recovery on the page, pens never imported, and "saving a
  pen is keep me signed in". HOSTED-PEERS.md's runtime shape and recovery
  ladder updated to match.

## [0.2.0] - 2026-11-05

### Breaking
- Python 3.11 or newer is required. Python 3.10 reached end of life in
  October 2026 and no longer receives security fixes, and current releases
  of some dependencies have dropped it.

### Security
- `cryptography` now requires 50.0 or newer (was 42). Older releases bundle
  an OpenSSL affected by the 9 June 2026 OpenSSL advisory
  (GHSA-537c-gmf6-5ccf) and carry four further cryptography advisories.
  FastATP only uses ECDSA signing and verification, which none of these
  touch, but the floor keeps installs off known-vulnerable versions.

### Conformance
- did:plc audit-log vectors re-vendored from `did-method-plc` at `29f1a70`
  (2026-10-27). Upstream only reformatted the JSON; every vector is
  unchanged. The atproto interop vectors are already at upstream HEAD
  (`056e574`).

### Release and supply chain
- Everything the CI and release pipelines download is pinned: actions by
  commit SHA, the Rust toolchain by `rust-toolchain.toml`, Python
  dependencies by `uv.lock`, and the wasm build tools (wasm-pack,
  wasm-bindgen, wasm-opt) and Bluesky's `goat` by version and SHA-256. The
  release build runs no third-party action, and its build backend comes
  from the lockfile.
- Dependency updates wait seven days after an upstream release before they
  are proposed (security updates excepted).
- Release candidates (`vX.Y.ZrcN`) are built from the same commit as the
  release they precede, and a development version can never be published.
- The versioning rule is stated in the README, the CHANGELOG and
  CONTRIBUTING: while FastATP is 0.x, a minor release may break and a patch
  release never does. Pin with `fastatp~=0.2.0`.

### Testing
- Every test now runs in CI, and a final `all tests ran` check fails a pull
  request if any test never passed in any job. This brought in Bluesky's
  `goat` firehose validator and the `atproto-sensorthings` reader as
  external checks, the root-mode WireGuard enforcement test, smoke runs of
  every example, and the full `com.atproto` lexicon corpus (96 schemas,
  vendored), which every lexicon round-trips and generates models for.
- The WireGuard tests pass private keys to `wg` over stdin instead of
  temporary files. Ubuntu 26.04's AppArmor profile for `wg` blocks reading
  key files outside `/etc/wireguard`; the library itself never passed `wg` a
  file and is unaffected.

### Rust core and browser bundle
- Moved to the RustCrypto 0.14 line: `k256` and `p256` 0.14, `ecdsa` 0.17,
  `sha2` 0.11. Signing and verification behave the same: low-S compact
  signatures, high-S rejected on both curves, and every cross-implementation
  vector passes unchanged.
- The Rust crates now declare a minimum supported Rust version of 1.88
  (required by `sha2` 0.11.1). This only matters if you build the Rust
  crates or the wasm bundle yourself; installing the Python package never
  needs Rust.

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

[Unreleased]: https://github.com/jacoblapenna/fastatp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/jacoblapenna/fastatp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jacoblapenna/fastatp/releases/tag/v0.1.0
