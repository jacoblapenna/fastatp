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

Call the people who will never run a server **hosted users**, as against
self-hosters. FastATP exists because its author hates shared secrets and
the authentication built on them, and still wants privacy; atproto today has
no privacy without shared secrets, and this design is the answer. It has to
work for hosted users and home-lab nerds alike, or it is a toy. Letting
hosted users in is one chain of consequences, and most of this document
follows from it:

1. A hosted user's peer runs in a browser, because a browser is what they
   have.
2. A browser cannot listen on a socket or join a WireGuard tunnel, so it
   cannot serve its own repo. Someone else must host that repo: the app's peer.
3. A host serving repos for pens it does not carry is a new thing. It must
   admit the pen that writes a repo without holding it (owner pull), admit
   a browser with no tunnel identity (the knock), let a lost pen be replaced
   from paper (recovery on the page), and never be able to forge what it
   hosts (every reader verifies).
4. Everything a host can see is the price of step 2. The builder decides how
   much of that price their product needs to charge.

Scale follows the same chain. A single peer hosting a few repos keeps the
doorman's address-to-identity map as an in-memory walk and one WireGuard
interface, and that is right at that size. A host serving millions runs
several instances, each with its own WireGuard interface (a kernel handles
thousands of peers, not millions), and keeps the commit lock for each repo
and a cache of "this address is this DID, granted in this repo" in its own
database, updated as repo owners write and delete capabilities in the repos
it hosts. The cache is for speed, not authority: it points the doorman at a
repo, and the doorman still admits by the capability currently in that
repo. That is what bringing your own database is for, and it is planned,
not shipped: FastATP knows where the walk stops scaling and says so in the
code rather than pretending otherwise. At that scale the mass of users
arrives through the browser knock anyway, which needs no global map.

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
- **Enrollment ceremony**: new device shows its public key (`pen_did`) as a
  QR; an existing device scans and writes the delegation record. Public key
  one way, record presence the other. Same interaction users know from
  passkey cross-device sign-in. The record is in the repo → device history
  is auditable provenance. When the existing device has no camera, the app
  relays the `pen_did` behind a short code and both devices show a hash of
  it as about six words for the user to compare; app code, never package.
- **Pens are never imported.** A key imported into WebCrypto as
  non-extractable passes every check the bindings make, so this is a rule,
  not a mechanism: importing a key puts one pen on two devices. A
  self-hoster brings their DID, not their pen; their browser enrolls as a
  device like any other.
- **Paper root** (rotation keys): 32 random bytes the bundle hands the app
  once, as 64 hex characters, never persisted by the package, never
  networked. How the app shows it (a 24-word phrase, a printed QR card) and
  how many rotation keys it mints are app presentation; the bundle takes
  hex back. The only zero-device recovery path, and the backstop that
  rotates away any pen compromise while the DID survives.
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
2. **Paper secret** (case 2) → rotate the DID document to a fresh pen
   (page-append); identity, repo, and relationships survive. On the page:
   a fresh browser mints a pen that is nobody yet; `rotation_did_from_secret`
   checks the typed secret against the chain's rotation key before anything
   is done with it; `rotate_pen` signs a new page naming the new pen and
   returns the longer chain, which the app posts to the shelf (a longer
   chain for the same slug; never an edit, never a rollback). The new pen
   pulls the repo as its owner — the host's rule reads the chain head, so
   it needs no record and has signed nothing yet — restores it, verifying
   the head against the *old* pen that signed it, prunes the lost devices'
   delegations, and pushes against the new head. The old pen can neither
   pull nor push. The secret touches wasm memory for one signature and is
   wiped. For case 1, the equivalent lives at the identity's home (PLC
   rotation keys, their own did:web chain).
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

- **The pen is a handle.** Every binding takes and returns a WebCrypto
  `CryptoKeyPair` (ECDSA P-256, `extractable: false`) and re-checks it on
  the way in: curve, type, the flag read back from the browser, and that
  the halves are a pair. Private-key bytes never enter wasm or JavaScript
  (the public half is exported to name the pen).
  `generate_pen()` mints one; `pen_did(pair)` names it;
  `connection_zero(domain, pair)` signs up with it;
  `BrowserPeer.create(did, pair)` starts a repo for any DID (a device repo
  is the pen's own `did:key`); `BrowserPeer.restore(car, pair, verify?)`
  rebuilds one from a stored or pulled CAR; with `verify` it checks the
  head against the pen named, and without it only content addressing is
  checked, so a CAR from an untrusted host must be restored with `verify`.
  Either way it never writes a lower rev than the restored head, even when
  the device clock is behind.
- **IMPORTANT — saving a pen is "keep me signed in".** The bindings store
  nothing. A pen lives in memory and is gone with the tab unless the app
  puts the handle in IndexedDB (structured clone keeps it non-extractable)
  and hands it back after a reload. That choice is the "keep me signed in"
  box and should be presented as one: ephemeral by default; a shared-machine
  session enrolled like any device with `expires_at` on its delegation as
  the backstop and deletion on sign-out; signup refusing to make the first
  device on a machine that is not the user's own. App policy, not library
  code.
- Browser peer pushes signed records via **push intake**:
  `Repo.prove()` slice POSTed, `Peer.verify()`d, dispatched.
- The **router** ("when content arrives signed by this pen, deliver to that
  pen's repo") is the intake path + the pen→repo capability binding. The
  routing table is signed data, not trusted server state. Ingestion is
  idempotent by CID; the router is a landlord — it can drop or delay, never
  forge or misfile undetectably.
- Reads are gated per repo by the **doorman**. Native peers: WireGuard
  cryptokey identity. The tunnel key is not a pen and has no DID: it is an
  X25519 key the machine holds beside its pens, it signs nothing, and the
  only thing that ties it to an identity is a capability record in the
  gated repo saying "this key is this DID". The record always carries the
  key: handed over out of band for a `did:key` peer, read from the DID
  document for a `did:web` peer, once, when the grant is written. A peer
  that rotates its key is a stranger until granted again; nothing follows
  a document on its own. The overlay address is derived from the key, so
  the record cannot lie about where the DID will arrive from
  (`docs/decisions/0002`). Browser peers: TLS + one knock —
  `sign_challenge(pair, nonce, repo_did)` signs a single-use host-issued
  nonce together with the DID of the repo being asked for, as the fixed
  message `fastatp-challenge-v1\n<repo DID>\n<nonce>` (a pen can never be
  handed commit bytes as a "challenge", and a signature for one repo cannot
  be spent on another). The gate resolves pen → DID through the live
  delegation chain and feeds the doorman; the honest weakening stands:
  "absence = every request rejected" rather than "absence = unreachable".
  The knock needs no repo loaded: it is about the repo asked for.
- **Owner pull** (`PeerServer.register(repo, owner_pull=...)`, off by
  default): a host serves a gated repo to its owner with no capability
  record — the pen that writes it, or the identity itself when a device
  speaks for it through a live delegation record in that repo. The owner is
  not a subscriber; a pen reading its own repo back from the host is the
  peer reaching its own storage. The pen still proves itself (WireGuard
  identity, or the browser's signed knock, with `iss` omitted for the pen
  or set to the identity for a device), and the proof must be this repo's
  own — a signature, or a binding or delegation in this repo's records —
  since any hosted repo can write a record naming any DID. Everyone else
  goes through the capability rule.
  The host answers "which pen writes this repo" either from the head commit
  (`True`) or from its own knowledge of the identity's chain head (a rule),
  which is what admits a freshly rotated pen before it has pushed.
- Hosted repos whose pen lives on another peer are **read-only on the
  host**. Today the host's `Repo` object still carries whatever key it was
  built with, and nothing in the process stops a write; what stops the host
  forging is that readers that verify against the identity's real pen
  reject it (`restore` with a verify key, a mirror, the next push's
  `verify_key`), and a reader that skips verification has trusted the host
  by choice. The planned external-signer work
  makes this mechanical: a repo can be opened **frozen** (no pen on this
  host, every write raises), and an `owner_pull` registration will require
  it, so a host serving a repo to its pen can never hold a pen for it.
- Multi-device sync: a device reads the user's other repos the same way
  any peer does, admitted by capability record, with the firehose as the
  sync protocol. Its *own* repo it pulls as the owner.
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

## Costs, stated plainly

Each of these is a cost of letting hosted users in, with what a builder does
about it. None is hidden, and most are a decision the builder makes about
the product they are building rather than a flaw to be engineered away.

1. **The host can read tier-1 data.** This is not a weakness; it is the
   price of serving someone who will not self-host, and it is the same price
   every app charges today. There is no app that reads none of a
   non-technical user's data, whatever its authentication rests on: even an
   end-to-end encrypted one reads who talks to whom and when, and that is
   tier-1 data here. The
   builder decides the price: a product that needs out-of-band
   notifications, search or moderation reads what those need, exactly as a
   landlord reads the mail it forwards; a builder who believes they can reach
   scale on self-hosters alone hosts nothing and is a highway. Tier 2 exists
   for the parts that must be host-blind regardless.
2. **Zero-device recovery needs the paper secret.** That is atproto's own
   rotation-key model, not something added here. A builder may hold the
   secret for users who want it, as Bluesky does, on the condition that it
   says so: holding it makes the app a custodian that can take the identity.
   A user who loses every device and the paper is a visible rebirth, which
   is a loud, per-user failure rather than a silent, archive-shaped one.
3. **The browser knock is different from WireGuard, not lesser.** A browser
   cannot listen on a socket or join a tunnel. That is a relic of an internet
   built for documents and shared secrets, a norm and not a law of nature,
   and FastATP does not get to repeal it. Within it, the knock is the most
   on-philosophy way in: a single-use nonce signed by a non-extractable pen,
   bound to the repo asked for, so no secret ever crosses the wire. It has
   precedent (passkeys work this way). What it gives up is unreachability:
   a refused caller gets refused requests instead of silence, so the
   builder rate-limits the knock at the edge, as it would any login page.
4. **You trust something to speak over distance.** A kernel, a secure
   enclave, a browser's key store. It is turtles all the way down, and
   pretending otherwise is the dishonest option. The choice here is which
   turtle: the user's own device keystore rather than a server-side key
   archive, because a device's compromise is one user's problem and an
   archive's compromise is everyone's. While the browser is the more
   dangerous place, it is also the smaller blast radius, the app
   builder can choose accordingly and host keys if they still want to.
   Stated as an opinion, because it is one.
5. **Timing on gated reads.** A refused read of a gated repo and a read of
   a DID the host does not serve answer identically and run the same checks
   in the same order, but the refusal also scans that repo's in-memory
   capabilities, which an absent DID has nothing to do. Accepted: for an
   owner pull the caller already holds the pen, so there is nothing left to
   enumerate; for a follow request the attacker can name any DID anyway, so
   the defence is the same rate limit on the knock as in 3, not equalized
   timing.

## Design decisions

- Chain depth and TTL policy: presence-based, any depth, deletion severs the
  subtree, no TTL machinery (optional `expires_at` is belt-and-suspenders);
  revocation "propagation" dissolved — there is nothing to propagate when
  absence is the revocation.
- The browser admission gate: signed-challenge `CallerResolver` feeding the
  doorman; honest weakening documented above. One knock for every read, its
  message labelled and bound to the repo asked for (`docs/decisions/0001`).
- Owner pull revises "every device is an admitted subscriber": the owner of
  a repo — its pen, or the identity through a delegated device — is not a
  subscriber and needs no capability record; opt-in per hosted repo
  (`docs/decisions/0001`).
- Pens as handles, each device writing its own repo, recovery on the page,
  pens never imported, and "saving a pen is keep me signed in":
  `docs/decisions/0001`.
- Overlay addresses are derived from WireGuard keys under one fixed IPv6
  prefix, never assigned or published, so no record can claim an address
  it does not hold the key for and nothing about addresses crosses the
  wire; IPv6-only on the overlay, any transport (`docs/decisions/0002`).
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
