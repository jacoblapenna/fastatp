# Hosted sovereign peers — design note

This note describes how FastATP hosts sovereign peers for users who will not
run their own infrastructure. It covers the Rust workspace + WASM packaging,
presence-based pen delegation, the browser admission gate, and shelf intake
with an anti-registry rule. It builds on FastATP's core machinery: push
intake, the doorman, grant-by-public-key, private channels, and did:web
continuity.

## Problem

An average user will not register a domain, host a DID document, run a peer,
or manage a raw signing key. Every mainstream answer to that (Bluesky's PDS
included) collapses into custody: a server holds the signing key and signs on
the user's behalf, so a password/token over the wire *is* the user. The goal:
full sovereignty — no secret, plaintext or ciphertext, ever crossing the wire —
with a UX of "tap Face ID / scan a QR."

## The separation that makes it possible

A "login system" fuses three things FastATP keeps apart:

1. **The pen** — the private signing key. The only true secret. Never leaves
   the device it was minted on, in any form.
2. **The phone book entry** — the DID document. Public. Hosting it grants no
   power: under the twin/chain, a host cannot forge a rotation; it
   can only go stale or dark (availability, not impersonation).
3. **The mailbox** — the repo. Signed, content-addressed. Hosting it is
   commodity work: a host cannot alter or forge records, only withhold them.

Items 2 and 3 are public/self-verifying, so the *builder* hosts them for
everyone. Only item 1 needs a per-user answer, and the answer is: many pens,
not one.

## Multi-pen identity (no key ever crosses the wire)

Rejected alternative, recorded for honesty: a single durable pen wrapped under
passkey-PRF secrets and stored server-side (Bitwarden-style). It works and
gives zero-device recovery, but the wrapped-blob archive is a correlated
failure surface — one wrapping/PRF zero-day compromises every user, and
ciphertext at rest is harvest-now-decrypt-later fodder. That is the "whose
compromise is everyone's compromise" shape one layer down. Dropped.

Chosen model:

- **Each device mints its own pen**, locally, non-extractable (WebCrypto
  `extractable: false`; browser peers default to **p256** since WebCrypto has
  no secp256k1). The pen never exists off-device in any form.
- **Device pens are bound by delegation records** (`dev.fastatp.auth.delegation`),
  NOT DID-document verification methods and NOT the `attestation` record (which
  is a WG channel-location pointer and does not cover pen binding). The durable
  DID's genesis pen delegates device pen #1; any live pen delegates pen N+1.
  Delegation proofs do NOT travel with record proofs: a pen is valid iff its
  delegation chain is **present right now** in the delegator's repo — absence
  IS revocation, fail closed — and counterparty visibility is composed by the
  builder via repo visibility, e.g. nested pens with a public delegation repo.
  - Enrollment never touches the DID doc or the rotation chain → the paper
    root stays offline for the entire device lifecycle.
  - The device constellation is **private by default** — disclosed
    per-counterparty when a pen signs at them, never published in the doc.
- **Enrollment ceremony**: new device shows its public key as a QR; an
  existing device scans and signs the attestation. Public key one way,
  signature the other. Same interaction users know from passkey cross-device
  sign-in. The attestation is a repo record → device history is auditable
  provenance.
- **Paper root** (rotation keys): BIP-39 phrase, shown once at
  signup, never persisted, never networked. The only zero-device recovery
  path, and the backstop that rotates away any pen compromise while the DID
  survives.
- **Revocation is per-pen**: losing a device severs one capability; the other
  devices are untouched.
  - "Log out everywhere else" = current device signs revocations for all
    other pens. Self-service.
  - "Log out everywhere including here" = full revocation; return requires
    the paper-root ceremony. A deliberate dead-man's switch: nothing
    electronic can resurrect the identity without the paper.

## Recovery ladder

Because pens attest pens laterally, the main/genesis pen matters only at
genesis (case 2) or first contact (case 1); afterward it is not needed for
anything — so "lose the main pen, lose all app data" is false under this
design. The ladder, cheapest rung first:

1. **Any surviving device** → attest a new pen sideways. Nothing was lost.
2. **Paper rotation phrase** (case 2) → rotate the DID document to a fresh
   pen (page-append); identity, repo, and relationships survive.
   For case 1, the equivalent lives at the identity's home (PLC rotation
   keys, their own did:web chain).
3. **Builder recovery policy (optional): sub-promotion.** Promoting a device
   pen to identity control inverts the hierarchy — a stolen device could take
   over the identity — so if offered it must be friction-gated: quarantine
   delay + notification to all other enrolled pens, classic
   account-recovery shape. Legitimately app-level policy, never substrate.
4. **Nothing survives** → visible rebirth (new DID, zero history). The
   substrate makes this loud, never silent.

## Two onboarding cases (bring a DID, or mint one here)

First contact is anchored by a "main" DID; per-device pens hang off it. Two
ways a user arrives:

**Case 1 — existing atproto identity ("paste DID here").** The user brings a
DID already hosted elsewhere (their own did:web, or a Bluesky `did:plc`).
First contact is a **delegation record in their existing repo**, not a local
signature: custodially hosted users (all of `bsky.social`) have never held
their own signing key, so the flow is DNS-TXT-shaped — the app supplies a
delegation record naming the app sub-DID's public key; the user posts it
through their own host; the app verifies it as an ordinary record proof
against their DID document. Their auth-to-their-own-host never enters the
substrate. Riders:

- Case 1 inherits the custody trust of the identity's home: a custodial host
  could forge the delegation. Sovereignty is total from the device pens down;
  the root is as sovereign as the user's existing arrangement. Apps should
  disclose this.
- `did:plc` roots require the sanctioned read-only `plc_resolver` plug-in
  — resolution only, never lifecycle writes.

**Case 2 — new DID, minted client-side, shelved by the app** (e.g.
`did:web:my-app.com:user-slug` — note did:web takes the domain unreversed;
reversed domains are NSID convention). Mechanics below ("connection zero").
The backup warning must point at the **paper rotation phrase, not the signing
pen**: pens stay non-extractable everywhere, main included; the phrase is the
single backup artifact.

## Connection zero ("host this repo I give you", never "make me an account")

Signup runs client-side (browser WASM peer) before the builder is involved:

1. Mint paper root → show phrase once. This is mandatory in the v1 reference
   flow: a rotation-less genesis can never append a chain page, so deferring
   the ceremony has no sound mechanism, and an app that skips it owns the
   consequence.
2. Mint first device pen (non-extractable).
3. Build and self-sign the genesis DID document; the twin slug is
   derived from the genesis hash — the identity's *name* is computed
   client-side, not assigned.
4. Create the genesis repo commit locally, including the first pen's
   capability record.

Then one POST to the builder: genesis doc + chain page one + repo CAR. The
builder shelves the doc (DIDServer), hosts the repo gated to the pen
(PeerServer + doorman), and binds pen → repo (router). At no instant does the
builder hold a secret or sign anything. This is shelf rental, not account
creation. The builder's signup gate (payment, email, rate limit) is its own
sovereign admission policy — edge business logic, never substrate.

## Runtime shape (all shipped)

- Browser peer pushes signed records via **push intake**:
  `Repo.prove()` slice POSTed, `Peer.verify()`d, dispatched.
- The **router** ("when content arrives signed by this pen, deliver to that
  pen's repo") is the intake path + the pen→repo capability binding. The
  routing table is signed data, not trusted server state. Ingestion is
  idempotent by CID; the router is a landlord — it can drop or delay, never
  forge or misfile undetectably.
- Reads are gated per repo by the **doorman**. Native peers: WireGuard
  cryptokey identity. Browser peers: TLS + signed-challenge admission at the
  WSS layer — a single-use connection-bound nonce signed by the pen, resolved
  pen → DID through the live delegation chain, feeding the doorman check; the
  honest weakening stands: "absence = every request rejected" rather than
  "absence = unreachable".
- Multi-device sync is free: every device is an admitted subscriber of the
  user's own hosted repo; the firehose is the sync protocol.
- Pairwise privacy = the isolation pattern: a conversation is
  a dedicated repo whose firehose admits exactly the counterpart.

## Two confidentiality tiers — apps must declare which

- **Tier 1, host-visible**: gated firehoses, plaintext records. Confidential
  against the world; readable by the host. Right for telemetry, work orders,
  most "private user data." The builder is demoted from authority to
  landlord: can read and can lock the door; can never write in your hand,
  and exit is a CAR pull with DID continuity.
- **Tier 2, host-blind**: payload-field encryption between user-held keys —
  PHILOSOPHY.md's explicit, discouraged escape hatch, used legitimately
  (record the FASTATP-CONFLICT). Host relays ciphertext it cannot read.
  Honest limits: metadata (who/when/size) still visible to the host; forward
  secrecy requires an app-level ratchet (per-message records carrying ratchet
  ciphertext) — the substrate permits it and deliberately does not ship it.

## Privacy of the identity graph

- Public DID doc: head keys only. Device pens: gated attestations, selective
  disclosure. "This DID has these sub-DIDs" is NOT public.
- **Caveat**: `did:web:builderapp.com:slug` writes the app affiliation into
  the identity's name forever. For app-affiliation privacy, shelve the DID
  doc on a **neutral shelf** (generic identity domain, commodity host,
  mirrors) — did:web continuity makes shelves interchangeable and none
  privileged, so this is not a registry. The app relationship then exists only in gated
  capabilities and exchanged records.
- Honest boundary: the builder always knows its own users; counterparties
  learn what records tell them. The privacy claim is against strangers and
  the public — and there it is total.

## What crosses the wire, ever

WebAuthn-style assertions and record signatures (not replayable secrets),
public DID documents and chains, signed records, CAR slices. No password, no
bearer token, no raw key, no wrapped key. A fully compromised host or wire
observer obtains nothing that lets it *be* anyone.

## Honest weaknesses (ranked)

1. **Host-visible metadata** (both tiers) and tier-1 contents: the builder is
   a reachability/metadata authority for its user population — legitimate the
   way PHILOSOPHY.md's tailnet clause is, and mitigated by cheap exit.
2. **Zero-device recovery requires the paper root** — lose every device and
   the phrase, and the identity is a visible rebirth. Deliberate trade: a
   per-user, loud, recoverable failure instead of a systemic, silent,
   archive-shaped one.
3. **Browser gate is weaker than WireGuard**: TLS + signature admission
   instead of cryptographic unreachability.
4. Platform trust in device keystores/browser storage (Secure Enclave, OPFS
   eviction policies) — the expected-case dependency that replaces the
   sync-fabric/blob trust the rejected design carried.

## Design decisions

- Chain depth and TTL policy: presence-based, any depth, deletion severs the
  subtree, no TTL machinery (optional `expires_at` is belt-and-suspenders);
  revocation "propagation" dissolved — there is nothing to propagate when
  absence is the revocation.
- The browser admission gate: signed-challenge `CallerResolver` feeding the
  doorman; honest weakening documented above.
- Neutral-shelf conventions: an anti-registry rule (no default shelf domain,
  ever) plus the opt-in shelf-intake route that makes running a shelf commodity.
- Root-ceremony-deferred lifecycle: cut; the ceremony is mandatory in the v1
  reference flow (a rotation-less genesis can never append a page).
- p256-by-default for browser pens: forced, not chosen — WebCrypto has no
  secp256k1; non-extractable pens require WebCrypto.
- Case-1 delegation record shape: the same `dev.fastatp.auth.delegation`,
  deposited by the user into their own repo via their own host; presence-checked
  via `sync.getRecord`; deposit-side OAuth is builder edge composition, never a
  package dependency.
- Sub-promotion recovery: identity control moves only via a rotation-signed
  chain page; a record never confers identity control; the substrate ships no
  recovery policy — the key defines the auth.
