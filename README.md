# FastATP

**Signed mail for machines, not remote procedure calls with a login.**

You drop a signed, content-addressed record — authored by your key, addressed by
DID, verifiable by anyone, forever — and any peer you have admitted picks it up,
acts on its own clock, and drops a signed reply. No authority issues a token. No
connection is held open. No message can be forged, and every hop of provenance
survives.

The difference from MCP/A2A in one line: **a call returns a value and vanishes;
a FastATP record is durable evidence that exists or doesn't.** There is nothing
to time out — only a signed record that anyone, anywhere, later, offline, can
verify came from exactly who it claims. That reframe is the whole product.

FastATP implements the identity and signing primitives of the AT Protocol for
use **outside the social web** — it is unaffiliated with Bluesky and avoids
social-web assumptions. A pressure sensor, a payroll job, and a language model
are the same kind of peer here: identity and signed records for **agents,
workloads, and IoT**, not an AI SDK. The signing core is transport-agnostic — it
turns typed records into signed bytes and back, and never opens a socket itself.

> **[`PHILOSOPHY.md`](https://github.com/jacoblapenna/fastatp/blob/main/PHILOSOPHY.md)
> is the canonical statement of what this is and what it forbids.** This README
> is its front door.

## How it compares

MCP and A2A are, at heart, **RPC with a login**: reach an endpoint, present a
credential some authority issued you, invoke a method, depend on that authority
and that connection. They are excellent at that. FastATP removes the authority,
makes every message self-verifying evidence, treats long-running and multi-party
work as the default, and applies to any workload.

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

### When MCP or A2A is the better choice

Credibility comes from not overclaiming. Reach for them instead when:

- You want **one model to use local tools right now**, in one session — MCP is
  simpler and FastATP is overkill.
- You are building **AI-agent interop inside existing web-auth infrastructure**
  and want a turnkey, widely-supported protocol today — A2A is more
  batteries-included for that narrow case.

FastATP wins decisively when you need **no trusted third party**, **cryptographic
provenance that composes across hops**, **durable async / long-running work**, a
**bilateral kill switch**, or **non-AI workloads** — any one of which the others
cannot give you.

### FastATP and the atproto SDK

The [`atproto` SDK](https://github.com/MarshalX/atproto)
([PyPI](https://pypi.org/project/atproto/), MIT, by MarshalX) is the Python
ecosystem's standard for AT Protocol — production-grade, battle-tested
against the live network, and the right choice for building on Bluesky or
talking to existing PDS hosts. FastATP and the SDK are **complementary
halves of one wire**: the SDK is a superb *client of hosts*; FastATP is a
way to *be* a host — a sovereign peer. An SDK application can read a
FastATP-hosted repo today (see
[`examples/sensorthings_interop`](https://github.com/jacoblapenna/fastatp/tree/main/examples/sensorthings_interop),
where an independent SDK-based package flattens a FastATP CAR export unmodified),
and a FastATP peer serves SDK users like any other host.

| Dimension | `atproto` SDK | **FastATP** |
| --- | --- | --- |
| What it is | Client library for AT Protocol services | **Library for being an AT Protocol peer** |
| Primary use case | Build on Bluesky; talk to PDS hosts; consume the network | **Host your own signed repos; agent↔agent and user↔agent workflows** |
| Auth model | Sessions / OAuth against a host (as the network's services expect) | **No login surface: keys sign, capability records authorize, absence is revocation** |
| Lexicon approach | Generated models from the official corpus — batteries included | **User-defined: author any conforming lexicon in Pydantic; export valid lexicon JSON** |
| Firehose role | Consume (production-hardened client) | **Emit AND consume, with full verification (signatures + MST inversion)** |
| Repo role | Read via host APIs | **Sign, host, prove, serve: MST repo, commits, CAR, `prove()` record proofs** |
| Hosting | n/a (clients have hosts) | **Peer-as-a-library: firehose + sync routes on your own box** |
| Choose it when… | You're building an app or bot on the existing network — it's mature, complete, and maintained by the ecosystem's standard-bearer | **You need to be your own host: no trusted third party, secure SaaS without a login wall, sovereign agents** |

The sharpest way to see the difference: with FastATP, **a secure
multi-tenant app needs no login wall** — users sign locally, hosts never
hold a secret, presence of a signed capability record is authorization and
its absence is revocation. And to be precise about auth: OAuth is never *in*
FastATP, but a developer can build OAuth (or any auth UX) **on top of** it
at the edge — [`examples/bluesky_signup`](https://github.com/jacoblapenna/fastatp/tree/main/examples/bluesky_signup)
does exactly that, with the OAuth library as the builder's dependency, never
FastATP's.

## Install

```bash
pip install fastatp
```

FastATP speaks **one wire format: atproto's**. Records live in a signed,
present-state repository; a record travels as a *proof slice* (signed commit +
Merkle path + record) that anyone can verify with nothing but a DID resolver —
over a firehose, HTTP, MQTT, or a serial line.

## Five-minute prove / verify

A device signs a telemetry record; anyone verifies it later with only a DID
resolver — no live connection to the device, no shared secret.

```python
import asyncio
from datetime import datetime, timezone

from fastatp import Peer, Lexicon, build_did_document
from fastatp.crypto import generate_keypair


class PressureReading(Lexicon):
    nsid = "com.example.telemetry.pressureReading"
    pressure_psi: int
    timestamp: datetime


async def main() -> None:
    key = generate_keypair("k256")
    did = "did:web:example.com:device-1"
    doc = build_did_document(did=did, signing_key=key.public_key())

    producer = Peer(did=did, signing_key=key)
    repo = producer.create_repo()
    repo.put_record(
        PressureReading.nsid,
        "boot",
        PressureReading(pressure_psi=1450, timestamp=datetime.now(timezone.utc)),
    )
    proof = repo.prove(PressureReading.nsid, "boot")  # bytes; ride any transport

    # In production the verifier resolves DIDs over HTTPS (web_resolver).
    async def resolver(d: str):
        return doc if d == did else None

    record = await Peer(resolver=resolver).verify(proof, expected_signer=did)
    print(record.pressure_psi)  # 1450 — proven to come from device-1's key


asyncio.run(main())
```

## Dispatch, reply, admit — the `PeerApp` framework

`PeerApp` is a FastAPI-shaped layer over the primitives: route any lexicon to a
handler, reply with signed records, and admit/evict peers with a bilateral kill
switch. It ships **mechanism, never application schemas** — you define your own
request/reply lexicons.

```python
from fastatp import PeerApp

app = PeerApp(server)  # a PeerServer for this peer's DID


@app.on(InspectionRequest)  # your lexicon, in your namespace
async def handle(req, ctx):
    # work happens on your clock — there is no call waiting on the other side
    result = InspectionResult(context_id=req.context_id, finding="within spec")
    app.emit(InspectionResult.nsid, generate_tid(), result)  # a durable signed reply


# a private, one-shot reply on an ephemeral channel only the requester can read:
#     await app.reply_private(ctx, result, ttl=120)
# consumer side, same call site as the public path:
#     reply = await agent.call(tool_did, req, reply_type=InspectionResult, private=True)
```

The requester is a sensor, a service, or an agent — the same peer either way.
See [`examples/workorder.py`](https://github.com/jacoblapenna/fastatp/blob/main/examples/workorder.py)
(public request/reply over a WireGuard-admitted firehose) and
[`examples/board_rendezvous.py`](https://github.com/jacoblapenna/fastatp/blob/main/examples/board_rendezvous.py)
(a documentless requester and a private service meet at a public board and
exchange a private reply, end to end).

## What's in the box

- **Identity** — `did:web` and `did:key`, DID documents, Multikey, low-S compact
  ECDSA (secp256k1 / P-256). Optional **self-certifying `did:web` continuity**
  (`Identity.bootstrap`, `ContinuityResolver`): a hash-anchored twin + rotation-
  signed chain gives plc-grade first-contact assurance with **no registry**, so
  identity survives a lapsed or hijacked domain.
- **Lexicon layer** — define any atproto-conforming schema as a Pydantic model
  from a type palette; `to_lexicon_json()` emits a valid `lexicon: 1` document,
  and the bridge round-trips every `com.atproto.*` schema.
- **Canonical records & repos** — atproto-identical DAG-CBOR + CIDs (checked
  against the interop vectors); `Repo` keeps a signed Merkle Search Tree of the
  current state (per-commit GC, no history accretion) with CAR export/import.
- **Firehose** — `PeerServer` hosts one or many repos (routed by DID) behind a
  `com.atproto.sync.subscribeRepos`-shaped WebSocket with cursor resume;
  `RepoMirror` verifies every commit against the producer's DID.
- **Authorization** — signed **capability** records decide *why* a peer is
  allowed; a **WireGuard** peer set enforces it physically (absence =
  cryptographic unreachability). The reconciler keeps them in lock-step — a
  decentralized replacement for a coordination server, with a bilateral kill
  switch. Records are never encrypted: confidentiality is reachability +
  isolation, not ciphertext.
- **Private-response channels** — ephemeral `did:key` repos + signed attestations
  for one-shot private replies, with a per-repo doorman enforcing isolation.
- **Durable storage** — an optional stdlib-`sqlite3` block store so an identity's
  repo survives process death.

## Philosophy

Read [`PHILOSOPHY.md`](https://github.com/jacoblapenna/fastatp/blob/main/PHILOSOPHY.md)
— it is canonical and governs the package. In one question: *does this make
FastATP more like signed mail between sovereign peers, or more like RPC with a
login?* The former is the direction; the latter is the thing it is deliberately
not.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest         # conformance vectors are vendored in tests/vectors/
uv run ruff check fastatp tests && uv run mypy fastatp
```

Clone to develop; a `pip install` never needs a Rust toolchain (the browser
peer ships pre-built). Running the full suite — browser/WASM, WireGuard, and
cross-implementation tests — is covered in
[`docs/TESTING.md`](https://github.com/jacoblapenna/fastatp/blob/main/docs/TESTING.md).

See [`CONTRIBUTING.md`](https://github.com/jacoblapenna/fastatp/blob/main/CONTRIBUTING.md)
before opening a pull request, and
[`SECURITY.md`](https://github.com/jacoblapenna/fastatp/blob/main/SECURITY.md)
to report a vulnerability privately.

### Releases

Releases are built in CI from a version tag: the workflow builds the browser
bundle, runs the full suite, builds the sdist and wheel, verifies their
contents, and publishes through PyPI trusted publishing. There are no API
tokens and no shared secrets anywhere in the pipeline — the same rule the
library lives by. Release candidates go to TestPyPI first; each publish waits
for maintainer approval. Changes are recorded in
[`CHANGELOG.md`](https://github.com/jacoblapenna/fastatp/blob/main/CHANGELOG.md).

## License

MIT. Copyright Jacob Lapenna.
