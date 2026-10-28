# PeerApp — the framework people build tools, agents, and workloads with

PeerApp is a framework layer on top of FastATP's shipped primitives; it does
not change any of them. It sits on top of the core API and conforms to the
atproto vendored material (wire format).

## Scope — read this first, it is the whole point

We are **not building a market.** We are building the Python package that
developers take as a dependency to build the tools, agents, and workloads that
could *eventually constitute* a market. The market is emergent; FastATP ships
the primitives and one ergonomic framework, and demonstrates the pattern in an
example. Nothing market-specific is shipped in the package.

The line, stated as a rule the framework must not cross:

- **FastATP ships the type system and the framework, never application
  schemas.** This is the same rule that forbids shipping `app.bsky.*`
  ("a language example only") and that defines the library's scope as "any
  conforming lexicon, not any Pydantic model" (CLAUDE.md).
- Therefore **there is no `dev.fastatp.market.*` namespace, no shipped
  `WorkOrder`/`Status`/`Result`.** Those are application semantics — every
  workload's request/response shape differs — and belong in the *developer's*
  namespace (`com.acme.*`), authored with FastATP's existing Lexicon palette.
  If the package ever declares that a request record has an `input` field, it
  has guessed the developer's domain. Don't.
- The one shipped app-ish namespace, `dev.fastatp.auth.*` (Capability /
  Revocation), is the exception because it is **infrastructure** — the authz
  plane itself. The workload pattern is a *use case*, not
  infrastructure, so it is not shipped.

What ships is **`PeerApp`**: a FastAPI-shaped framework that dispatches *any*
user-defined `Lexicon` to a handler, and gives handlers ergonomic access to the
already-shipped machinery (verify, repo write/broadcast, admit/evict). It is
generic over the record type end to end.

## The pattern PeerApp enables (context, not a shipped artifact)

A developer builds an **agent** and a **tool** as peers. The agent signs a
request record (its own lexicon) into its repo; the tool, subscribed to the
agent's firehose, sees it, does the work, and signs a response record (its own
lexicon) into *its* repo; the agent, subscribed to the tool, correlates the
response. No long-poll, no timeout-and-drop; the medium is durable signed
records. This is the async, signed, provenance-carrying substrate that replaces
MCP for long-running multi-party work — but PeerApp only provides the
*mechanism* (dispatch + admission + emit); the developer provides the records
and the meaning.

Why the mechanism is worth shipping as a framework rather than leaving people
to hand-roll it on `Peer.verify()` + `RepoMirror`:

- **No shared trust root.** Admission is mutual and peer-local; blast radius of
  any compromise is one peer.
- **Bilateral, unilateral kill switch.** Either peer severs the other alone,
  live, via `wg set` — absence is cryptographic unreachability. (Already true
  of the reconciler; PeerApp just exposes it as `admit`/`evict`.)
- **Idempotency is free** by content addressing: a record's CID is its
  identity, so replay after reconnect dedupes with zero coordination — but only
  if handlers are written idempotent per CID, which the framework should make
  the obvious path.

## The resolved architecture decision (constrains the framework)

**The DID/repo is the unit and the only enforceable listener boundary.
Collections organize records *within* one identity; they are NOT a
listener-isolation or security boundary.** Forced by the shipped code:

1. `Repo(did, signing_key)` and `PeerServer(peer)` bind **1 DID = 1 repo =
   1 firehose** (`fastatp/server/peer_server.py`, `fastatp/repo/repo.py`).
2. The firehose is **whole-repo broadcast**: `PeerServer._emit()` pushes every
   `#commit` to every subscriber; `_subscribe()` neither authenticates the
   subscriber nor filters by collection. Everyone admitted receives every
   record in the repo.
3. Verification is **whole-commit**: `RepoMirror` re-derives the signed MST root
   from *all* of a commit's ops; filtering ops to hide a collection breaks the
   proof. (This is why atproto's `subscribeRepos` is whole-repo.)
4. WireGuard admission (`WireGuardReconciler.grant`) is **per-DID network
   reachability** — the kill switch's granularity.

Consequences the framework must honor:

- **Different visibility for different listeners => different DIDs/repos, not
  different collections.** A capability surface some peers may reach and others
  may not is its own DID (`did:web:acme.com:tool-fx`), repo, key, and
  `PeerServer`. `PeerApp` is therefore built around **one DID per app
  instance**; you run several `PeerApp`s for several surfaces.
- **DIDs are cheap; run many peers in one process.** Under `did:web` a DID is a
  slug; `DIDServer` serves `/{slug}/did.json`. One RPi process hosts a fleet of
  `PeerApp`s, each independently grantable/killable. **Identity boundary !=
  process boundary.**
- **Do NOT make collections a subscription/security boundary.** Per-collection
  firehose filtering needs per-collection proofs — a separate project,
  explicitly deferred. `PeerApp` must not offer an API that implies
  per-collection admission works today.
- **The ergonomic cost is real and acknowledged.** A tool with N visibility
  tiers runs N `PeerApp`s (N DIDs/repos/firehoses/reconcilers), cross-admitted.
  "DIDs are cheap" keeps this *possible*, not *free* — fleet management and
  inter-app routing are genuine overhead. A future `PeerAppGroup` convenience
  (co-managing several surfaces + their cross-admission in one process) is a
  candidate, explicitly **deferred**; v1 ships single-DID `PeerApp` and the
  developer wires multiples by hand. Do not paper over the cost with an API that
  pretends one DID can serve differentiated listeners.

## The `PeerApp` API to build

Generic over the developer's own `Lexicon` subclasses. Handlers do not return
HTTP; they write records into the app's repo (which broadcasts on the
firehose).

```python
# The developer defines their OWN records (their namespace, not ours):
class QuoteRequest(Lexicon):
    nsid = "com.acme.fx.quoteRequest"
    context_id: Tid
    pair: str
    notional: int

class Quote(Lexicon):
    nsid = "com.acme.fx.quote"
    context_id: Tid
    price: str

# The framework we ship:
app = PeerApp(peer, server, reconciler)     # one DID's peer + PeerServer + reconciler

@app.on(QuoteRequest)                        # dispatch by $type — any Lexicon subclass
async def price_it(req: QuoteRequest, ctx: Envelope) -> None:
    # ctx: issuer_did, record_cid, received_at — the record is already
    # verified (commit signature, MST membership, rotation-key ban, authorize
    # hook) and parsed. The handler just does work.
    app.emit("com.acme.fx.quote", generate_tid(),
             Quote(context_id=req.context_id, price=quote(req)))
```

Required surface — all generic, nothing domain-specific:

- `@app.on(LexiconClass)` — register a handler keyed on that class's `$type`.
  The dispatch table is **per-app**, not the process-global `_REGISTRY` (which
  stays a `$type`->class resolver); two apps in one process must not collide.
- `Envelope` — verified context handed to every handler: `issuer_did`,
  `record_cid`, `context_id` (if present on the record — read generically, not
  assumed), `received_at`, and the raw record.
- `app.emit(collection, rkey, record)` — write a record into this app's repo
  and broadcast it. This is the *only* "response" primitive; the developer
  builds request/reply, status updates, fan-out, or anything else out of it.
  **Do NOT ship opinionated `status()`/`result()` helpers** — those presuppose
  a record shape and cross the scope line.
- `app.admit(did, **grant_opts)` / `app.evict(did)` — thin wrappers over
  `WireGuardReconciler.grant` / `.revoke` (`fastatp/authz/reconciler.py:113`).
  This is the "let a new listener in" helper. Granularity is per-DID/per-repo
  (see the architecture decision): admit lets a DID subscribe to this app's
  whole firehose. Bidirectional admission (agent admits tool, tool admits
  agent) is two `admit` calls on two apps.
- ingestion (**pull**): `PeerApp` runs a `RepoMirror` per admitted producer and
  dispatches each new verified record to its `$type` handler. Build on
  `Peer.verify()` semantics and `RepoMirror` (`fastatp/repo/sync.py`); do not
  reimplement verification.

- `app.call(peer, request_record, *, reply_type, correlation="context_id",
  timeout=None)` — **ships in v1** (core, not optional). The ergonomic
  request→reply path that makes PeerApp adoptable: it emits `request_record` and
  awaits the first record of `reply_type` whose `correlation` field matches the
  request's. It is a convenience over `emit` + a mirror, and it is **not
  RPC-with-a-login** — it holds no connection open, awaits a durable signed
  record, and the meaning of a missing reply comes from the *tool's published
  liveness contract* (its discovery lexicon: "what to expect, what to do if no
  stream arrives"), not from any protocol timeout. Three things the
  implementation must get exactly right:
    - **Subscribe before emit.** Establish the reply subscription (mirror on the
      peer's firehose) *before* emitting the request, or a fast reply races
      ahead of the subscription and is missed — the same subscribe-before-write
      ordering the firehose tests already exercise.
    - **`timeout` is a *local* give-up, not a protocol deadline.** It exists so a
      caller never awaits forever; on expiry it raises locally (the caller
      stopped waiting), it does not cancel or fail anything on the tool. Default
      `None` = wait per the tool's published contract. Framing this as a local
      timeout, never a served deadline, is what keeps it on-philosophy.
    - **Reply visibility.** On the public firehose path the reply broadcasts to
      *every* admitted subscriber of the tool (whole-repo firehose). For a
      private one-shot reply, the tool answers on an ephemeral channel (see "The
      private-response pattern"); `app.call` then follows the attestation. In v1
      the workorder example uses the public path (mutual admission), which is
      correct for that topology.

## Handler execution & concurrency

The topology (one `RepoMirror` per admitted producer -> dispatch) is fixed;
this section is how invocations run *in time*. Model it on FastAPI: there is one
handler per `$type`, many producers, so many **concurrent invocations of that one
handler** — the multiplicity is calls, not handlers (a `@app.on(X)` handler is to
records what a FastAPI route is to HTTP requests).

Rules:

- **Decouple ingestion from execution.** A handler must NOT run inline in the
  mirror's read loop. If it does, a slow handler stalls that firehose read, the
  producer's bounded per-subscriber queue fills, and the producer evicts you
  (`ConsumerTooSlow`, `fastatp/server/peer_server.py`) — a slow handler becomes a
  *dropped subscription*. The mirror reads + verifies + enqueues fast; handlers
  run on a separate task pool (the same reason FastAPI runs work off the loop).
- **Concurrent across producers.** A slow handler for producer A must never stall
  producer B's records. This is the default and matches FastAPI's per-request
  concurrency.
- **Per-producer ordering is opt-in.** Records from one producer arrive in signed
  commit order; some workloads depend on it (record 2 builds on record 1), most
  do not (independent requests, like HTTP). Default: fully concurrent. Opt-in:
  serial per producer to preserve commit order. Never serialize *across*
  producers.
- **Bounded work queue with an explicit full-policy.** A hung handler must not
  grow memory without bound. When the queue fills, the choice (pause reads and
  risk eviction, or shed load) is a knob, not an accident — and `log()` what is
  dropped rather than silently truncating.
- **Handlers must be idempotent per record CID.** Decoupling + cursor-resume can
  redeliver a record after a reconnect; the CID detects the duplicate, but
  suppressing it is the app's job — see "Idempotency is a dial" below.

Two residual design calls remain open: whether per-producer ordering is a
decorator flag or an app default, and the queue-full policy's default.

### Idempotency is a dial the app sets — the CID detects duplicates, it does not suppress them

A CID is a content hash: the same record always has the same CID, computed by
anyone with no coordination, so a redelivered record (cursor-resume replays
everything after your last cursor; the checkpoint `#sync` can re-send current
state) carries the **same** CID. That makes the CID a perfect duplicate
*detector* — but detecting is not suppressing: to skip reprocessing you must
*remember* seen CIDs, which is state (in-memory = lost on restart; durable = a
dedup log you maintain). The CID gives the key, not the mechanism.

The levels, cheapest first — most workloads never need durable state:

- **Idempotent operation** — the work is safe to repeat (a read, "set to X"). No
  tracking at all.
- **Output keyed by request CID** — write the result at `rkey = request.cid()`;
  the present-state repo overwrites, so at most one result exists per request
  however many times you reprocess — dedup for free, no seen-set. Caveat: this
  dedupes the recorded *output*, not the external side effects of doing the work
  (a card charge still fires twice).
- **Seen-CID set** — in-memory (fast, lost on restart) or durable (survives,
  costs state); only when the operation is genuinely effectful and repeats are
  costly.
- **Full accounts / ledger** — the SaaS end of the dial.

FastATP ships the two primitives that make every level buildable — the **CID**
(a reliable detector) and the **present-state repo** (the output-keyed level for
free) — and **never a mandated dedup store** (that would be stateful, opinionated,
and wrong for half the workloads). Framework default: it re-dispatches on replay
and the handler owns its idempotency. An optional in-memory seen-set may ship as
a convenience, labelled "not durable, not exactly-once" — never as a guarantee.

## Conveniences & composition patterns

Ergonomic sugar over the primitives above. Each stays optional and must not
smuggle in an application schema or a required third party. See `PHILOSOPHY.md`
"How the market gets built" for the model these serve.

```python
# --- admit_issuer: reply-path bootstrapping -------------------------------
@tool_app.on(InspectionRequest, admit_issuer=True)
async def handle(req: InspectionRequest, ctx: Envelope) -> None:
    # admit_issuer=True auto-calls admit(ctx.issuer_did) when this handler runs,
    # so the sender can subscribe to THIS peer's firehose and hear the reply.
    # Without it a first-time sender isn't admitted to your firehose and your
    # response broadcasts where they can't reach it. Defaults False — admission
    # is a sovereign act, so opting in is explicit. It grants subscribe on your
    # WHOLE firehose (the DID/repo is the boundary); for reply-only exposure,
    # reply from a dedicated response DID/repo instead.
    ...

# --- advertise + publish_schemas: opt-in public discovery -----------------
@tool_app.on(InspectionRequest, advertise=True)    # this lexicon is public-facing
async def handle_public(...): ...

@tool_app.on(InternalReindex)                       # advertise defaults False -> NOT published
async def handle_private(...): ...

published = tool_app.publish_schemas()
# Writes a schema record (com.atproto.lexicon.schema-shaped) into this peer's
# repo for every advertise=True handler — and ONLY those. A tool routinely
# supports lexicons it does NOT want to advertise (internal/admin/privileged),
# so publication is per-lexicon opt-in, never "publish everything I handle".
# Returns the schema docs so you can also post them to a bulletin board.

# --- sovereign admission negotiation (edge business logic) ----------------
# A private worker, publicly discoverable, that decides its own admission policy.
# The front desk is a PUBLIC (ungated) peer; the worker firehose is admitted-only.
@frontdesk_app.on(AdmissionRequest)
async def consider(ask: AdmissionRequest, ctx: Envelope) -> None:
    if my_policy_accepts(ask, ctx.issuer_did):      # proof-of-need, payment, OAuth,
        worker_app.admit(ctx.issuer_did)            # human approval — the tool's choice.
    # admit() adds the WireGuard peer; the requester can now reach the private
    # worker firehose. admit()/evict() wrap reconciler.grant/revoke. Whatever
    # policy runs here is this tool's edge business logic, never the substrate —
    # an OAuth check here gates nothing for any other peer (PHILOSOPHY.md).
```

`admit_issuer` and `advertise` are decorator flags on `@app.on`; `publish_schemas`
is an app method built on the shipped `Lexicon.to_lexicon_json()`. The bulletin
board is **example/user code, not library code** — a peer whose one handler
accepts signed listing records and re-hosts them (a pure relay; the listing's own
signature carries authority, not the board's). The framework ships `on` /
`emit` / `admit` / `advertise` / `publish_schemas`; the board, the `Listing`
lexicon, and the admission policy are the developer's.

## The private-response pattern (ephemeral channels, no encryption)

Private replies without encryption and without accounts. The firehose is a
shared broadcast, so a service that answers many producers from one repo leaks
each response to all admitted subscribers. Two honest answers:

- **You want durable, per-user private storage** -> you are building an
  account SaaS (login + long-term hosting on your infra). FastATP is then
  plumbing; this pattern is not for you.
- **You want a private one-shot response** -> the ephemeral-channel playbook
  below. This is the FastATP-native path.

Playbook (all built by the developer; FastATP ships the primitives + optional
conveniences):

1. The service publishes a **public contract** (its own lexicon, on the bulletin
   board / its DID document): "responses arrive on an ephemeral `did:key` repo,
   reachable as described in a signed attestation, lasting TTL T; here is how to
   verify it."
2. A producer sends its request to the service's firehose.
3. The handler spins up an **ephemeral channel** — a fresh `did:key` + repo +
   hosted firehose — and **admits the issuer to that channel**
   (`channel.admit(ctx.issuer_did)` on the NEW channel — distinct from the
   `admit_issuer=True` flag, which admits to the *current* app). It does the work
   and posts the response there.
4. The service posts a **chain-of-custody attestation** to its PUBLIC repo,
   **signed by its stable main DID**, carrying: the request's `context_id`, the
   ephemeral DID, the ephemeral firehose's **reachability**, and the **TTL**. This
   signed binding is the entire anti-spoof mechanism — trust flows
   main DID -> attestation -> ephemeral DID -> response. Nobody can stand up a
   repo and claim to be the service's channel, because the binding is signed by
   the main key.
5. The producer reads the attestation, verifies it against the (discovered,
   reputation-checked) main DID, connects to the ephemeral firehose (already
   admitted), reads its private response, and knows from the published TTL when
   the channel vanishes.

Two hinges that must be exact:

- **`did:key` has no service document.** So the attestation MUST carry the
  ephemeral channel's reachability and TTL — a `did:key` alone tells the producer
  nothing about where to connect. The public signed record is discovery +
  attestation + location + lifetime in one.
- **Ephemeral repos are only cheap with multi-repo hosting** (one server, many
  repos routed by DID). One process per ephemeral repo does not scale.

Candidate conveniences (ship as needed — none are application schemas):
`app.spawn_ephemeral_channel(issuer_did, ttl=…)`, `app.attest(channel,
context_id)`, and consumer-side `await peer.follow_attested(attestation)`.
Enablers to build underneath: multi-repo hosting and whole-repo TTL/expiry.

**Status: shipping.** These mechanics are implemented. The developer surface is
two calls — the developer never touches `did:key`, WireGuard, or attestations:

```python
# Producer — one call spins the channel, admits the issuer by key, writes the
# reply into it, and publishes the signed attestation:
@app.on(InspectionRequest)
async def handle(req, ctx):
    await app.reply_private(ctx, InspectionResult(...), ttl=120)

# Consumer — same call site as the public path:
reply = await agent.call(tool_did, request, reply_type=Result, private=True,
                         self_attestation=my_coords)
```

Seams underneath: `app.open_private_channel(issuer_did, ...)`, `app.attest(
channel, context_id, to=board_url)`, `app.follow_attested(attestation)`; the
shipped `Attestation` record; the doorman enforces the isolation
(a second subscriber to the channel is refused). End-to-end symmetric board
rendezvous: `examples/board_rendezvous.py`. The design rests on several
interlocking pieces:

WireGuard admission needs each end's WG **public** key, and
`WireGuardReconciler.grant` gets it by resolving the grantee's DID *document*.
An ephemeral `did:key` has no document, so a tunnel to/from it can't be set up
by resolution — both ends' keys travel out-of-band instead: the **requester's**
key in the **request** (so the channel can `admit` it), the **channel's** key +
endpoint in the **attestation** (signed by the service's main DID — anti-spoof
+ reachability in one record). That requires `grant` to accept a public key
directly. Cheap ephemeral repos then require **multi-repo hosting + whole-repo
TTL**, and the mechanism + one-line API (`reply_private`, `call(private=True)`)
sit on top.

Note a private reply is ALSO achievable with a **dedicated `did:web`
response repo per caller** (resolution-based grant, no new code) — heavier but
available. The ephemeral `did:key` flavor is the lightweight, self-cleaning
alternative. The workorder example uses the public reply path only and does not
depend on any of this.

**First contact (board rendezvous — resolves the fully-private case).** A fully
private service can't receive a first request over pull (no tunnel to any
requester yet), and it should NOT need a public intake endpoint of its own
(that would give it a probeable public surface). The resolution is symmetric
contact via the bulletin board (PHILOSOPHY.md "First contact"): the *requester*
posts a signed **want ad** on the public board carrying its WG public key +
reachability; the private service — whose only move is *outbound* (subscribe to
the board) — sees it, answers with a signed **engagement record** (its channel's
key + endpoint, same attestation shape as the private-response pattern), and
both sides admit each other by key. The request architecture is the response
architecture.
Consequences for the framework:

- **Push intake is a board-pattern enabler, not a per-service requirement.** The
  board (the one deliberately-public peer) accepts posts from strangers: `did:web`
  posters can be mirrored; ephemeral `did:key` posters push a signed **proof
  slice** (`Repo.prove()` bytes -> `Peer.verify()`, which already short-circuits
  did:key resolution with zero lookups). Private services never expose an intake.
- A fully-ephemeral requester therefore needs **no hosted infrastructure**:
  keypair, throwaway in-memory repo, one pushed proof, one outbound WG handshake
  (WG learns its endpoint from the handshake — no stable address needed), one
  subscribe.

## Multi-repo hosting: the apartment building

One `PeerServer` hosting many repos, add/removed live at runtime (a registry
routed by DID), is the shape ephemeral channels force: a channel's `did:key`
does not exist until the request arrives, so no static configuration can
describe it. It is also plain atproto — a PDS hosts thousands of repos in one
service, `subscribeRepos` frames each carry their `did`, `getRepo` takes
`?did=`. FastATP's one-repo server was the degenerate case; the wire format
needs zero changes.

Why not server-per-repo? Not philosophy — **operations**. Per-channel listeners
mean birthing and killing network endpoints (ports, firewall/NAT rules,
endpoint discovery in every attestation) at *request rate*: a Kubernetes-shaped
infra project as the default path for "reply privately", which kills both
"easy for anyone" and the RPi fleet story. In the shared building, spawning a
channel is a dictionary insert.

Two guarantees silently lean on "one server = one repo" today and must be
deliberately re-attached to the **repo**, or they evaporate:

- **Permission — the apartment building.** Today every repo is its own house
  with its own locked front door (the WG tunnel); getting through the door IS
  the permission. Multi-repo hosting moves all repos into one building with a single front
  door — and a guest key opens the *building*, not just your apartment. A peer
  admitted to channel A can reach the server and ask for repo B's stream;
  nothing decided to allow that, it happens because reachability stopped
  discriminating rooms. The fix is a **doorman**: on any firehose/`getRepo`
  request, check the caller against the *target repo's* live capabilities and
  refuse non-holders. The doorman doesn't take your word for who you are — the
  tunnel you arrived through IS your ID (WireGuard cryptokey routing binds each
  admitted peer to a unique source tunnel IP, unspoofable in-tunnel; map it to
  the DID). Network enforcement still gates the building; the room check is
  app-layer code grounded in the same WG keys — new security-critical code,
  hence a required gate test (admitted-to-A requesting B is refused), because
  no existing test thinks to ask.
- **Position — the page numbers.** The firehose numbers events (`seq`) and a
  cursor means "I got up to page 47." One server = one repo = one book, so page
  numbers mean position. Interleave ten repos through one shared counter and
  "page 47" is gibberish: a follower of repo A alone sees holes where other
  repos' events were, and dead ephemeral repos tear their pages out of the
  shared numbering forever. Fix: **every repo keeps its own page numbers** —
  per-repo `seq`, cursors, and backfill windows.

Same lesson both times: multi-repo hosting splits *server* from *repo*, and every guarantee
that secretly leaned on their identity must be re-attached to the repo on
purpose.

Deployment remains composable — two independent axes: **isolation model**
(shared building + doorman vs. server-per-repo) x **enforcement plane** (native
WG reconciler vs. tailnet vs. firewall vs. open). Multi-repo hosting makes the shared
building the cheap zero-dependency default; server-per-repo stays available for
whoever would rather buy orchestration than run a doorman — e.g. one repo per
port with tailnet ACLs gating each, keeping enforcement purely network-level.
Note the trade: a tailnet's control plane is a *reachability* authority (a
trusted third party for who-reaches-what — legitimate inside an org, exactly
the dependency the native WG plane avoids across orgs), and capability<->ACL
drift is manual unless someone writes a tailnet enforcement adapter against the
injectable `WireGuardInterface` protocol — a welcome, on-philosophy
contribution.

## Execution semantics = routing topology (framework stays neutral)

The framework must NOT bake in at-most-once vs. race. Topology carries it, and
that's the developer's choice: grant one tool DID (one tunnel = exactly-once,
by construction) or grant N (fan-out / consensus, take first-or-best reply).
`PeerApp` just makes `admit`/`evict` cheap so tool selection is as ephemeral as
the developer wants.

## What already exists vs. what to build

**Reuse as-is (do not modify):** `Peer.verify()` (`fastatp/peer.py`),
`PeerServer` (`fastatp/server/peer_server.py`), `RepoMirror`
(`fastatp/repo/sync.py`), `WireGuardReconciler.grant/revoke/reconcile`
(`fastatp/authz/reconciler.py`), `Capability`/`Revocation`
(`fastatp/authz/capability.py`), `did:web` + `DIDServer`, the full Lexicon
palette (`fastatp/lex/`).

**Build (this is the entire deliverable):**

1. `PeerApp` — per-producer `RepoMirror` fan-in, per-app `$type` dispatch,
   `Envelope`, `emit`, `call`, `admit`/`evict`. Generic over any `Lexicon`.
   Multiple instances per process. This is a framework module (propose
   `fastatp/app.py` or `fastatp/peerapp/`), exported from `fastatp.__init__`.
2. Tests: dispatch to the right handler by `$type`; unknown `$type` ignored;
   `emit` broadcasts and round-trips; `call` correlates a reply (subscribe-
   before-emit; local `timeout` raises without affecting the peer);
   `admit`/`evict` toggle reachability (reuse the SimTunnel harness from
   `test_integration_authz.py`); idempotent redelivery (same record CID ->
   handler fires once); multi-app-per-process isolation.
3. `examples/workorder.py` — a **demonstration**, defining its own request /
   status / result lexicons in a `com.example.*` namespace (mirroring how
   `examples/two_peers.py` defines `com.example.agent.*`). This is where the
   "workload market" pattern is shown; it is example code, not library code.

## First slice — `examples/workorder.py` acceptance

Developer-defined lexicons in `com.example.*`, real DIDServers over localhost,
real signed records both ways, all over `PeerApp`:

1. An agent-app and a tool-app, each its own DID/repo/`PeerServer`, `admit` each
   other (real `Capability` records + SimTunnel/WG admission).
2. Agent `emit`s a request record addressed to the tool.
3. Tool-app's handler fires, `emit`s a "received" status record, does the work,
   `emit`s a result record.
4. Agent-app's handlers receive status then result and correlate by the app's
   correlation field.
5. **Liveness**: assert status precedes result — silence vs. work is
   distinguishable.
6. **Kill switch**: agent `evict`s the tool; a subsequent request never
   arrives, and the `Revocation` is visible on the agent's firehose as audit.
7. **Idempotency**: replay the same request (reconnect with an old cursor);
   the handler fires once.

Suite/lint/types green, coverage held. Some framework design calls remain open
(module layout; per-producer ordering flag vs. default; queue-full policy
default). `app.call` ships in v1.

## Deferred (do not build; do not let the framework assume it)

- **Per-collection firehose filtering / per-subscriber views.** Needs
  per-collection proofs to stay inductively verifiable. Until then collections
  are intra-identity organization only; "different listeners per record in one
  repo" is answered by "use another DID/repo."
- **Push intake** (accept a pushed, self-verifying proof slice at a public
  route). Pull is enough for v1 and for every *private* service forever — a
  private service's only move is outbound. Push intake is needed exactly once
  in the topology: at the **bulletin board** (the deliberately-public rendezvous
  peer), so documentless `did:key` strangers can post want ads (see "First
  contact"). Build it with the board pattern (likely alongside Gates 7-9), not
  before, and never as something every PeerApp exposes by default.
- **Any market machinery** — registries/directories ("app store"), reputation
  scoring, discovery. All of it is built *by users on top of* PeerApp, or is a
  future separate package. Ship the framework; let the market emerge.
