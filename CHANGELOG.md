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
- Garbage collection is per commit and per repo: a commit or import
  deletes only the blocks it orphaned, never a sweep of the store, so a
  block store shared between repos or between processes loses nothing
  another head reaches. `collect_garbage()` is the sweep, for a store the
  repo has to itself; blocks a crash left staged are no longer collected by
  the next commit, only by the sweep.
- Firehose bookkeeping is awaited: `PeerServer.register`, `unregister` and
  `reap_expired`, `RepoHandle.emit_account`/`emit_identity`/`emit_sync`,
  `expire` and `unregister`, and `PrivateChannel.close`. Each frame's seq
  now comes from the repo's head store before the frame goes out, and a
  dropped repo announces its deactivation before its subscribers are cut.
  `PeerServer.create(peer, repo)` takes an existing repo as its second
  argument (`repo_store=` is keyword-only); `PeerServer(peer, repo)` still
  builds one, announced on its first emit or subscriber. An `Identity`
  keeps its head in `blocks.sqlite` instead of a `repo_head` file (an old
  file is folded in on first load).
- `Repo.import_car` is awaited (it advances the head through the head
  store), and `put_record`, `delete_record` and `apply_writes` may return
  `None`: a concurrent writer on a shared head store already made exactly
  that change, byte for byte, so there was nothing to commit.
- `PeerServer.register(repo, owner_pull=...)` requires a frozen repo. A
  repo served back to its own pen is one whose pen lives elsewhere, so a host
  holding a pen for it is refused at registration with a `ValueError`.
  Pushes land through `repo.import_car` as before.
- The repo write path is async, and a pen is a handle. `Repo.put_record`,
  `delete_record`, `apply_writes` and `recommit`, `PeerServer`/`RepoHandle`
  /`PrivateChannel` writes, `PeerApp.emit` and `publish_schemas`,
  `Peer.create_repo`, `Commit.create`, `Identity.bootstrap`/`adopt_web`/
  `rotate` are all awaited. A fresh repo is `await Repo.create(did,
  signing_key)`; `Repo(did, signing_key, head_cid=...)` reopens a stored
  head and signs nothing, and a `Repo(...)` with no head has no commit until
  its first write. `PeerServer(peer, repo)` takes the repo; `await
  PeerServer.create(peer)` makes a fresh one, and `await server.serve()`
  serves on a running loop where `run()` cannot. Every `signing_key=`
  argument that takes a private key (`Repo`, `Repo.create`, `Peer`,
  `Commit.create`, `Identity.adopt_web`) also accepts a `Signer`, so the
  same code runs with a key in memory or in a TPM; the repo and peer hold
  it as `.signer`. `build_did_document(signing_key=)` takes the public key
  as before: pass `signer.public_key`.
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

- **WireGuard overlay addresses are derived from keys, never assigned or
  published.** Every peer's address is `OVERLAY_PREFIX` (one fixed IPv6
  /64) plus 64 bits of SHA-256 over its WireGuard public key, so both ends
  of a tunnel agree with no address crossing the wire (the public key
  crosses it exactly as before). `Capability.wg_tunnel_ip`
  is removed; `build_wireguard_uri`, `build_wireguard_service`,
  `build_attestation`, `WireGuardReconciler.grant` and
  `PeerApp.open_private_channel` no longer take an address, and a `wg://`
  URI that still carries `ip=` is refused. The overlay is IPv6 only (the
  transport can be anything); `check_overlay_support` fails loudly with the
  sysctl to fix when a kernel has IPv6 disabled on the interface.

### Added
- Bring your own database, part three: `fastatp.Storage` is the one
  decision, anything with `blocks`, `heads` and `blobs`. `SqliteStorage`
  (the built-in: a directory, blocks and heads in one file) and
  `MemoryStorage` ship. `Repo.create`/`Repo.open`, `Peer.create_repo`,
  `PeerServer.create` and `Identity.bootstrap`/`adopt_web` take `storage=`;
  an `Identity` given one keeps its repo, head and seq there and creates no
  sqlite in its `state_dir`, which then holds only the chain, the marker
  and a software pen. `Identity.storage` is the bundle; `Identity.store`
  is its block store, as before. `docs/HOSTED-PEERS.md` "Bring your own
  database" sketches the Postgres adapters and the doorman's cache table.
- `TunnelIdentityResolver(lookup=...)`: a builder's cache in front of the
  doorman's walk. The lookup maps an overlay address to `(DID, granting
  repo)` from the builder's own store, sync or async; a hit is used only
  after the doorman confirms the named repo still holds a live capability
  for that DID whose key derives that address, and a miss or a failed
  confirmation falls back to the walk over every hosted repo, which stays
  the behaviour with no lookup. The cache is for speed, never authority: a
  stale or wrong entry costs a lookup and cannot admit.
- Bring your own database, part two: the firehose seq lives in the head
  store. A commit's seq is the one its swap assigned (`CommitEvent.seq`);
  every other frame takes the next through the same swap
  (`Repo.advance_seq`), so with a durable store a restarted peer resumes
  where its last delivered frame left off and followers' cursors keep
  working instead of hitting `FutureCursor`. The genesis commit is seq 0,
  the start of the stream, so a cursor of 0 misses nothing. An `Identity`
  on a `state_dir` gets this with no new argument: its head and seq are in
  the same sqlite file as its blocks.
- Bring your own database, part one: `fastatp.repo.head.HeadStore`, the
  per-repo write lock as a compare-and-swap. `Repo.create(...,
  head_store=)` puts a repo's head and firehose seq in the app's store,
  `await Repo.open(did, signing_key, store, head_store)` loads it, and
  every commit and import writes its blocks first, then advances the head
  only if it is still the one it was built on. A lost swap reloads the new
  head and retries the write on it (bounded); contention beyond that is
  `HeadMoved`, carrying the current head. `MemoryHeadStore` (the default,
  today's behaviour) and `SqliteHeadStore` ship; `CommitEvent.seq` is the
  seq the swap assigned.
- The signer seam: `fastatp.Signer` (a `public_key` and `async sign`) is
  what an app implements to sign with a key the process cannot hold (a TPM,
  an HSM, a secure element, a YubiKey); `fastatp.SoftwareSigner` is the
  in-memory implementation every bare `PrivateKey` is wrapped in.
  `crypto.normalize_signature` turns a device's DER or raw `r||s`, low-S or
  not, into compact low-S in one place; `crypto.sign_with` signs,
  normalizes and verifies against the signer's own key before anything is
  committed, so a misbehaving device fails at the write with the repo
  unchanged. `Identity.bootstrap(..., signer=)`, `adopt_web(domain, signer,
  ...)` and `rotate(signer, ...)` put a durable identity's pen in hardware:
  its state directory records only the public key, the signer is required
  again on every load and checked against it, and `Identity.signer` is what
  the identity signs with (`signing_key` is the software key when there is
  one, else `None`). The package ships no adapter; `docs/HOSTED-PEERS.md`
  "Hardware-held pens" sketches one.
- `RepoHandle.emit_sync()` announces a hosted repo's current head as a
  `#sync` event, for a host whose repo took a push through `import_car`
  (which commits nothing and so emits nothing by itself). The hosted-peer
  example calls it after every landed push.
- Frozen repos: `Repo(did, None, store=..., head_cid=...)` reopens a repo
  with no pen on this host. It serves `getRepo` and the firehose exactly as
  before, publicly or to live capability holders, refuses every commit with
  a clear error, and still takes pushes through `import_car`, which for a
  frozen repo requires `verify_key` (a host with no pen has no other way to
  know the bytes are the owner's). `Repo.frozen`
  says which it is, and a `PeerServer` no longer needs its peer to carry a
  pen, only a DID.
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
  Owner pull decides who reads; a frozen (penless) hosted repo is required
  for it (see Breaking).
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
- A `did:web` grantee admitted through `WireGuardReconciler.grant` was
  programmed into the kernel but its capability record carried no key, so
  the doorman could not name it and refused every gated read (on 0.2.0 the
  record carried no `wg_tunnel_ip` either). `grant` now writes the key it
  resolved into the record for every grantee, the same record the
  getting-started tutorial writes by hand, and `reconcile` enforces the
  record without consulting any document again. A grantee that rotates its
  WireGuard key is a stranger until the grantor grants again, for `did:web`
  and `did:key` alike (before, a `did:web` grantee's new key was followed
  into the kernel on the next reconcile, which is a seat handed to whoever
  controls the domain). A record written by 0.2 for a `did:web` grantee
  carries no key: `reconcile` reports it as `failed` ("grant again") and
  admits nobody for it until the grantor does. A re-grant that supersedes
  a record carrying a different key removes that key from the interface in
  the same call, rather than leaving it admitted until the next reconcile,
  unless the key is pinned or another live grant still names it.
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
- `docs/decisions/0002-key-derived-overlay-addresses.md`: why an address is
  a function of a key, the rejected alternatives (a central allocator, host
  assignment, self-chosen addresses), and the limits (IPv6 overlay, key
  rotation moves the address, the doorman's walk at scale).

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
