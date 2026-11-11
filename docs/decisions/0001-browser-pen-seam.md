# 0001: The browser pen seam

- Date: 2026-11-10
- Status: accepted
- Tenets served: no shared secrets (7); symmetric peers (3); ship
  primitives, not applications (4); transport-agnostic core (5)

This is the first public decision record. Earlier records lived in a
private staging repository and are not renumbered here; where one is
relied on it is quoted.

## Problem

The 0.2 browser bundle had one constructor, `connection_zero(domain)`. It
always minted a new pen, a new paper root and a new identity. Nothing could
store a pen, restore a peer after a page reload, or enroll a second device
on an existing identity. The hosted example showed the gap: it called
`connection_zero` a second time and threw that identity away just to get a
second pen. That was a scope cut (staging ADR 0017 §6 set version one to
"connection zero + push + subscribe"), not a decision.

Filling the gap raised four questions that this record answers: how a pen
crosses the wasm boundary; how a second device writes; how a browser gets
its own repo back from a host; and how recovery works on the page.

## Decisions

### 1. Pens cross the boundary as handles, never bytes

Staging ADR 0017 §3: "bytes in a JS-inspectable buffer are extractable by
definition, so a key held by the Rust code would silently break
HOSTED-PEERS.md's 'never leaves the device' claim." So every binding takes
and returns a WebCrypto `CryptoKeyPair` whose private key is ECDSA P-256
with `extractable: false`, and every entry point re-checks what it was
handed: the curve, the type, the `extractable` flag read back from the
browser, and that the two halves are one pair (a probe is signed and
verified). IndexedDB stores such a pair by structured clone and keeps it
non-extractable, which is why handles are what the app persists.

The bindings never import an outside private key. A key imported into
WebCrypto as non-extractable would pass the check, so the rule is stated
here and in the docs rather than enforced: apps must never import one,
because it puts one pen on two devices.

The exported surface: `generate_pen()`, `pen_did(key_pair)`,
`BrowserPeer.create(did, key_pair)`, `BrowserPeer.restore(car, key_pair,
verify?)`, `connection_zero(domain, key_pair?)`,
`sign_challenge(key_pair, nonce, repo_did)`,
`rotation_did_from_secret(hex)` and `rotate_pen(chain_json, secret_hex,
key_pair)`. `BrowserPeer.sign_challenge(nonce)` is removed (see 3).

### 2. Each device writes its own repo

Verifiers accept a repo's commits only from the pen its identity's chain
head names. Rather than teach every verifier to accept any pen with a live
delegation (a change across Python and Rust), a second device creates a
repo of its own: its DID is the device pen's `did:key`, made with
`BrowserPeer.create`. The main repo holds a record naming that device, and
the device repo may hold one pointing back. Joining across repos, and how
fine the device record is, is the app's lexicon. Verification is unchanged.

### 3. One knock

A browser signs with its one pen, and what it asks a host for is a repo.
So there is one way to knock: `sign_challenge(key_pair, nonce, repo_did)`
signs the host-issued nonce together with the DID of the repo being asked
for, as the fixed message `fastatp-challenge-v1\n<repo DID>\n<nonce>`. It
needs no repo loaded. The label means a pen can never be tricked into
signing commit bytes dressed up as a challenge; binding the repo DID means
a signature for one repo cannot be spent on another. The Rust core and the
Python gate build the same bytes, pinned by a shared vector.

The 0.2 method signed the bare nonce from a loaded peer. It is removed, and
the gate verifies the labelled message, which is why 0.3 is a minor
release.

### 4. Owner pull: a host serves a repo to its own pen

HOSTED-PEERS.md said "every device is an admitted subscriber of the user's
own hosted repo". That made the owner a reader of their own data, which
needed a capability record admitting the identity to its own repo: the
peer asking itself for permission. Revised: the owner is not a subscriber.

With `PeerServer.register(repo, owner_pull=...)` (off by default), the
doorman admits two callers to a gated repo with no capability record: the
pen that writes it, and the identity itself when a device speaks for it
through a live delegation record in that repo. Everyone else goes through
the capability rule as before. The host answers "which pen writes this
repo" from the head commit (`True`) or from its own knowledge of the chain
head (a rule), and the rule is what admits a freshly rotated pen before it
has pushed anything.

Rejected: teaching the gate to read the DID document; a dedicated pull
route beside `getRepo`; making the root repo public so recovery could read
it. The last was adopted for a day and dropped once owner pull existed.

Owner pull decides who reads. It does not stop the host writing: a hosted
`Repo` still carries whatever key it was built with, and what stops a host
forging is that every reader verifies against the identity's real pen. The
planned frozen repo (no pen on this host) will be required for an
`owner_pull` registration, so the package refuses the custody shape at
registration.

### 5. Recovery on the page

`rotation_did_from_secret` derives the rotation `did:key` from the typed
paper secret, for a typed-back check at signup and a "not your card" check
at recovery, with nothing leaving the page. `rotate_pen` builds a new chain
page naming the new pen, keeping the identity, its aliases and its rotation
keys, and signs it with the secret. The app posts the longer chain to the
shelf, which accepts a longer chain for the same slug and never an edit.
The new pen then pulls the repo as its owner, restores it (verifying the
head against the old pen, which signed it), prunes the lost devices'
delegations, and pushes. The old pen can neither pull nor push.

The secret is in wasm memory only for the derivation or the one signature,
exactly as `connection_zero` holds it when minting, and every buffer that
held it is wiped before returning. The bundle's interface for the secret is
64 hex characters; how an app shows it (a word phrase, a QR card) and how
many rotation keys it mints are app presentation.

### 6. Saving a pen is "keep me signed in"

The bindings never store a pen. A pen is ephemeral, gone with the tab,
unless the app chooses to persist its handle in IndexedDB. That choice is
exactly the "keep me signed in" box, and apps should treat it that way:
ephemeral by default; a shared-machine session enrolled like any device,
with `expires_at` on its delegation as the backstop and deletion on sign
out; signup refusing to make the first device on a machine that is not the
user's own. App policy, not library code.

### 7. Pairing

The QR code goes from the new device to an existing one: the new device
shows its `pen_did`, the existing device writes the delegation. When the
existing device has no camera, the app relays the `pen_did` behind a short
code and both devices show a hash of it as about six words for the user to
compare. The package ships no code for this.

## Consequences

- 0.3.0 is a minor release: `BrowserPeer.sign_challenge` is gone and the
  gate's signed bytes changed. The only known client was the example.
- Python gains one additive mechanism, owner pull, and one helper,
  `challenge_message`. Verification, the doorman's capability rule and the
  Rust core's wire format are unchanged.
- Restoring a repo on a device whose clock is behind the restored head no
  longer writes a lower rev; the Rust clock observes the head's rev. The
  Python side has not been checked for the same case.
- A gated repo's existence still shows through `getRepo`'s status code
  (404 for unhosted, 403 for refused). Pre-existing; not addressed here.
