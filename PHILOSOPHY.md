# FastATP — Philosophy

This document is **canonical**. It ships with the package, and it governs the
package. Features, APIs, and contributions are measured against it. When a
proposed change and this philosophy conflict, the change is wrong until this
document is deliberately, explicitly revised (an ADR in `docs/decisions/`), not
worked around. If FastATP becomes popular, this is the thing that keeps it from
drifting into the shape of whatever it's compared to.

## The one-sentence version

**FastATP is signed mail for machines, not remote procedure calls with a
login.** You drop a signed, content-addressed record — authored by your key,
addressed by DID, verifiable by anyone, forever — and any peer you have admitted
picks it up, acts on its own clock, and drops a signed reply. No authority
issues a token. No connection is held open. No message can be forged, and every
hop of provenance survives.

That reframe is the whole product. Everything below is why it matters and what
it forbids.

## Tenets

1. **No trusted third party.** Identity is a DID; authenticity is a per-record
   signature. There is no identity provider, no OAuth server, no token issuer
   whose compromise is everyone's compromise. Trust is peer-local: each peer
   unilaterally decides who it talks to. The blast radius of any breach is one
   peer.

2. **Data, not calls.** The unit of exchange is a signed, content-addressed
   record — never an RPC invocation. Records are durable, verifiable offline,
   deduplicated by CID, and compose across hops. This is *why* async,
   long-running, multi-party work is natural here instead of bolted on: there is
   no call to time out, only a record that either exists or doesn't.

3. **Symmetric peers.** Every participant — device, agent, service, human
   session — is a peer with a DID. No client/server asymmetry, no host/tool
   hierarchy. Either side can produce, consume, admit, or sever. Admission is
   mutual and the kill switch is bilateral: if either peer is compromised, the
   other cuts it off alone, live, with no coordination.

4. **Enforcement is physical, not policy.** The authorization *decision* is an
   auditable signed capability record; the *enforcement* is the WireGuard peer
   set. Absence from that set is cryptographic unreachability, not an unchecked
   box. A revoked peer cannot knock.

5. **Transport-agnostic core.** The signing core never opens a socket. Records
   ride the firehose, HTTP, MQTT, or a serial line — provenance travels with the
   bytes, not the channel. A record verified after three hops and a week offline
   is exactly as trustworthy as one pulled live.

6. **Workload-agnostic, not AI-centric.** This is identity and signed records
   for agents, workloads, and IoT. AI agents are *one* use case, not the frame.
   FastATP deliberately sits outside both the social web (it is not Bluesky) and
   the "LLM tool-calling" frame (it is not an AI SDK). A pressure sensor, a
   payroll job, and a language model are the same kind of peer here.

7. **Ship primitives, not applications.** The library ships the type system
   (the Lexicon palette) and the framework (PeerApp). Users define their own
   lexicons and build their own systems. FastATP never guesses your domain —
   no shipped `WorkOrder`, no `app.bsky.*`. The sole shipped app-ish namespace,
   `dev.fastatp.auth.*`, is authorization *infrastructure*, not an application.

8. **The market emerges; it is not shipped.** Directories, reputation,
   discovery, an "app store" of tools — all of it is built by users on top of
   the primitives. No registry is privileged; any directory is an untrusted
   phone book that gates nothing, because grants are signed by the parties, not
   by the directory. FastATP ships the mechanism and gets out of the way.

## Why this is more powerful than MCP and A2A

MCP and A2A are both, at heart, **RPC with a login**: you reach an endpoint,
present a credential some authority issued you, invoke a method, and depend on
that authority and that connection. They are excellent at what they are. But
they inherit RPC's ceiling:

- A **trusted third party** issues the credentials — so its compromise is
  systemic, and every participant must federate with it.
- A **message is not evidence.** Once a call returns, nothing about it is
  independently verifiable. You cannot prove, later, to someone else, who asked
  for what. Provenance dies at the connection boundary.
- **Long-running and multi-party work is retrofitted** — via polling, SSE,
  webhooks — onto a model whose native shape is "call and wait."
- The frame is **AI agents talking to tools.** The rest of the computing world —
  sensors, services, workloads — is out of scope.

FastATP removes the authority, makes every message self-verifying evidence,
treats long-running and multi-party as the default rather than the exception,
and applies to any workload. That is not an incremental improvement on MCP/A2A;
it is a different substrate that happens to also cover their use cases.

### Comparison

| Dimension | MCP | A2A | **FastATP** |
|---|---|---|---|
| Mental model | Client calls a server's tool | Agent calls a remote agent | **Peer drops signed mail; peers act on their own clock** |
| Interaction | Synchronous RPC | Task RPC (long-running via SSE / push) | **Async durable signed records** |
| Identity | Server URL + OAuth | Agent Card at a URL + declared auth | **DID (did:web, did:key)** |
| Trust root | OAuth / token issuer | Web auth (OAuth, API keys) in the card | **None — cryptographic keys, per-record signatures** |
| Message verifiable after the fact? | No | No (messages not independently signed) | **Yes — by anyone, offline, across hops** |
| Topology | Host–client–server | Client-agent ↔ remote-agent | **Symmetric peers** |
| Authorization / revocation | Server-side token scopes | App logic + web auth | **Signed capabilities + WireGuard admission; absence = unreachable; bilateral kill switch** |
| Long-running work | Awkward | Supported (tasks, SSE, webhooks) | **Native — no connection held** |
| Discovery | Static config | Agent Cards + registries | **User-built, untrusted directory; no privileged registry** |
| Domain | LLM tools / context | AI agents | **Any workload — agents, services, IoT** |
| Transport | stdio, HTTP+SSE | HTTP + JSON-RPC + SSE | **Transport-agnostic (firehose / HTTP / MQTT / serial)** |

### Where MCP or A2A is the better choice (be honest)

Credibility with skeptical experts comes from not overclaiming. Reach for MCP or
A2A instead of FastATP when:

- You want **one model to use local tools right now**, in one session. MCP is
  simpler and FastATP is overkill.
- You are building **AI-agent interoperability inside existing web-auth infra**
  and want a turnkey, widely-supported protocol today. A2A is more batteries-
  included for that narrow case right now.

FastATP wins decisively when you need **no trusted third party**, **cryptographic
provenance that composes across hops**, **durable async / long-running work**,
**a bilateral kill switch**, or **non-AI workloads** — any one of which the
others cannot give you, and which together describe where autonomous machine-to-
machine systems are actually heading.

## How the market gets built (composition)

The market is not a feature FastATP ships — it is what emerges when people
compose four independent primitives: **a peer, a DID, a signed record, and an
optional network gate.** From those four building blocks:

- **Public peer** — no gate; anyone who can reach it may read its firehose /
  `getRepo`. **Private peer** — the same server behind WireGuard / a tailnet /
  a firewall; only admitted DIDs reach it. **Mixed** — gate one route and not
  another (public for discovery/read, admitted for the workload firehose).
- **Authenticity is constant** across all of these: every record is signed, so
  a public peer's records are exactly as unforgeable as a private one's. The
  gate controls *who can reach/read*, never *whether you can trust what you
  read*. Confidentiality/admission and authenticity are independent knobs.
- **The gate itself is swappable.** The native WireGuard reconciler is the
  zero-dependency, peer-sovereign enforcement plane; a tailnet, a firewall, or
  nothing at all are equally valid deployments — records stay self-verifying
  under every one of them. Be honest about the trade: a tailnet's control plane
  is a **reachability authority** (a trusted third party for who-reaches-what).
  Inside one org that authority is legitimate — the org owns its network;
  across orgs it is exactly the dependency the native plane exists to avoid.
  An edge/deployment choice, never the substrate.

### Records are never encrypted; confidentiality is reachability, not ciphertext

FastATP does not encrypt records, and the substrate never will by default — the
same stance atproto takes, for the same reasons: a no-history, signed,
replicable record is the wrong place for ciphertext (no forward secrecy with
static keys, metadata still leaks — who, when, how big — and key rotation is
miserable without history). Confidentiality is handled two ways, both without
touching record contents:

- **Reachability** — WireGuard / a tailnet encrypts everything in transit and
  controls who can reach a firehose. This is *why* WireGuard is in the design: so
  records never have to be encrypted.
- **Isolation** — a separate repo/DID per recipient (the private-response
  pattern in `docs/PEERAPP.md`): privacy by identity + admission, not by
  encrypting a shared stream.

Encrypting a payload field is an explicit, discouraged escape hatch; reaching for
it is a `FASTATP-CONFLICT` to be recorded, not a default anyone chooses.

### The bulletin board

A directory is just a **public peer whose only workload is "accept and host
signed listings."** Private tools post their signed listing records to it; the
board re-hosts them. Because a listing is signed by the *posting tool's* key and
is content-addressed, the board is a pure **relay** — it cannot forge or alter a
listing, and a consumer verifies each one against the *tool's* DID, never the
board's. The board can list, rank, and point; it can never grant or impersonate.
That is the untrusted phone book made concrete: a swarm of these is the "app
store," and not one of them is a trusted third party.

Be honest about the boundary of this claim: content-addressed signing defeats
**forgery** — no board can put words in a tool's mouth — but it does not, by
itself, give **freshness** (is this listing still current, or a replayed stale
one?) or **sybil/spam resistance** (nothing stops a flood of signed junk
listings). Those are real problems the market must solve on top — via listing
TTLs, revocations, reputation, staking, curation — exactly the emergent layer
FastATP deliberately does not ship. The substrate guarantees authenticity; it
does not pretend to guarantee relevance.

FastATP ships none of this — no `Listing` lexicon, no board peer. A user builds
it on PeerApp in an afternoon. **This is what "the market emerges" means.**

### Publicly discoverable, selectively admitting

A tool can be **found by anyone and admit almost no one.** Discovery is public
(the board, or its DID document); admission is the tool's own sovereign
decision. The tool runs **whatever admission negotiation its developer wants** at
the edge — a signed proof-of-need, a payment, human approval, an allow-list, *or
even OAuth* — and then calls `admit(did)` on the peers it accepts.

This does **not** contradict the "no trusted third party" tenet, and the
distinction matters: that tenet governs the *substrate*. FastATP never requires
an issuer, and no peer depends on another peer's auth choices. A tool consulting
OAuth before it decides whom to `admit()` is just that one tool's private
business logic — an edge adapter, not the substrate. Its OAuth server gates
nothing for anyone else and can impersonate no one; remove it and every other
peer is unaffected. Sovereign admission means each peer may consult any oracle it
likes, *precisely because* that choice never leaks into the shared substrate.

The result is total composability: public front-desk DID -> private worker DID;
public catalog -> private execution; one tool with per-listener tiers; found
publicly, admitted selectively. All from the same four building blocks.

### First contact: the board is the rendezvous, and contact is symmetric

A fully private service has no tunnel to any requester yet, so no first request
can reach it — and it need not expose any public endpoint to fix that. The
board resolves first contact for both sides, symmetrically:

- **Services list what they offer** (capabilities, admission requirements,
  contact instructions), signed by their stable DID.
- **Requesters list what they seek** — a *want ad* — carrying their own signed
  contact info (their WG public key and reachability).
- A private service **lurks**: its only move is outbound (subscribe to the
  public board). It reads want ads, engages the ones it chooses by posting a
  signed engagement record answering the ad (its channel's key + endpoint), and
  admits that requester. The requester, seeing the engagement, admits back. The
  tunnel forms; the conversation goes dark. **The contact stayed public; the
  content never was.**
- The discoverability burden falls on whoever wants to be contacted. Anonymous
  participation costs only a keypair: a `did:key` requester needs no hosted
  infrastructure at all — a signed want ad and one outbound handshake.

Admission is mutual and self-governed at every step: either party can decline
to begin, and either can terminate. The request architecture and the response
architecture are the same architecture — a signed record plus connection info,
delivered where the other party can already reach.

### Transparency is a dial the application composes

The substrate makes *who-contacts-whom* naturally public (signed records on a
board) and *what-they-say* naturally private (admitted tunnels). Where an
application sets the dial between those poles is a design choice — and choosing
transparency deliberately turns the metadata that a "don't encrypt records"
substrate exposes into an **honesty mechanism**.

A dating application makes the incentives visceral. Compose it with a public
contact graph: profiles are signed listings; contact requests are public
engagement records; conversations happen only after mutual admission, in
private. Then:

- **Shotgunning becomes public arithmetic.** One DID answering four hundred
  listings is countable by anyone. Bad behavior isn't moderated away; it is
  priced in by visibility.
- **Unreachability beats blocking.** "Can this person even contact me" is
  admission, not a filter — an unwanted request doesn't land in a folder, it
  *cryptographically cannot arrive*.
- **Reputation accrues to persistence.** History attaches to the DID. An app
  builder can make that an easy filter — *"don't admit requesters whose DID (or
  whose attestations) are younger than three months"* — so a fresh identity is
  itself a loud signal.
- The market converges toward honest connection **among identities with
  something to lose** — which is the honest form of the claim. DIDs are free,
  so a burned reputation can be reset by minting a new one; the substrate makes
  resets *visible* (zero history), it cannot make them *impossible* without an
  external anchor (verification, payment, proof-of-personhood — an app-level
  choice, never the substrate's).

Different applications set the dial differently: the dating app elects a fully
public contact graph; an enterprise procurement market might keep engagements
private via direct intake; a telemetry mesh needs no board at all. Same four
building blocks — the transparency tier, like the admission policy, is the
application builder's to compose.

## What this means for contributions

A contribution is **on-philosophy** when it strengthens one of the tenets and
violates none. Concretely:

**Welcome:**
- Anything that widens the *type system* (Lexicon palette, conformance,
  round-trip fidelity) or the *framework* (PeerApp ergonomics) generically.
- Anything that strengthens peer sovereignty, provenance, offline
  verifiability, or transport independence.
- New transports, new resolvers, new stores — as long as the core stays
  socket-free and no single one becomes privileged.

**Rejected (unless this document is first revised by ADR):**
- Shipping **application schemas** (a canonical `WorkOrder`, chat lexicons,
  anything domain-specific). Users define those. Cf. the `app.bsky.*` exclusion.
- Introducing a **required central service, registry, or token issuer** — any
  trusted third party — into a core path.
- **Bearer-token / OAuth auth** as a core authentication mechanism. Signatures
  and capabilities are the model; conventional auth may only ever be an optional
  edge adapter, never the substrate.
- **AI-specific assumptions** baked into the core (that a peer is a model, that
  a payload is a prompt/tool-call). The core is workload-agnostic.
- Making **synchronous, connection-holding RPC** the primary interaction path.
  RPC surfaces (e.g. XRPC) may exist as secondary compatibility layers only; the
  async signed-record path is primary.
- Opening a **socket in the signing core**, or otherwise coupling provenance to
  a specific transport.

When in doubt, ask: *does this make FastATP more like signed mail between
sovereign peers, or more like RPC with a login?* The former is the direction;
the latter is the thing we are deliberately not.
</content>
