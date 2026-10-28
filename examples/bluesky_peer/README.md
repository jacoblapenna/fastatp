# bluesky_peer — an ordinary Bluesky account, sovereignly hosted

A FastATP peer as a readable, indexable member of the live atproto network:
crawled by a Relay, indexed by the AppView, rendered by bsky.app as an
ordinary account — with **zero custodial machinery**. No
`com.atproto.server.*`, no OAuth, no sessions, no shared secrets. The
relay's read path is unauthenticated by design; membership needs no auth
substrate, only a sync surface that actually conforms (Gates 14–15).

The demo sentence this example makes true:

> Every byte that reached Bluesky was signed on my own hardware, traversed
> only signed records, and no credential ever existed to leak.

Everything Bluesky-specific lives HERE, never in the package: the
`app.bsky.*` record shapes are user-authored raw dicts ([records.py](records.py)),
and the relay you announce to is always your explicit choice
([request_crawl.py](request_crawl.py) has no default — the anti-registry
rule).

## Topology (reference deployment)

```
      static identity (any CDN / Pages — survives peer downtime)
        https://alice.example.com/.well-known/did.json
        https://alice.example.com/.well-known/atproto-did
        https://alice.example.com/<slug>/did.json     <- the continuity twin
        https://alice.example.com/<slug>/chain.json   <- FastATP peers verify this;
                                                         the network sees the DID doc
                       │ serviceEndpoint
                       ▼
      your peer (any always-on box, reachable via any https tunnel)
        peer.py — PeerServer
          ├─ did:web:alice.example.com          PUBLIC  (the persona; Bluesky sees this)
          └─ did:web:alice.example.com:<slug>   GATED   (internal agents; never announced)
                       ▲
      relay <──requestCrawl── you (one-shot, your chosen relay)
```

- **One subdomain = one atproto-visible identity**. atproto
  supports hostname-level did:web only; your internal agents are slug-DID
  twins — self-certifying, unlimited, and never pointed at a relay.
- The DID document's `serviceEndpoint` may be ANY https host: Cloudflare
  Tunnel serves a custom hostname, Tailscale Funnel a `*.ts.net` one.
  Identity is not tied to the tunnel vendor. WebSockets pass through both.
- The static artifacts mean **no DIDServer process is required**: `PeerServer`
  is the only process you run.

## Runbook

### 1. Mint the identity + static artifacts

```
python bootstrap_identity.py --domain alice.example.com \
    --pds https://YOUR-TUNNEL-HOSTNAME --state ./state --out ./static
```

Store the printed rotation pen COLD (it is shown exactly once; it is your
recovery authority). Host `./static/` as the SITE ROOT of
`https://alice.example.com/` (e.g. Cloudflare Pages) — it contains
`.well-known/` and the twin's `<slug>/` directory. The signing pen never
leaves `./state/`.

### 2. Wire the handle

The handle IS the domain. Publish ONE of (they must agree with the DID):

- DNS TXT: `_atproto.alice.example.com` → `did=did:web:alice.example.com`
- or serve the emitted `.well-known/atproto-did` file (step 1 already put it
  in `./static/`).

Both directions must agree or the account shows as `invalid handle`.

### 3. Run the peer behind the tunnel

```
python peer.py --domain alice.example.com --state ./state --port 8100
```

Point your tunnel's https hostname at `127.0.0.1:8100`. Verify locally with
Bluesky's own tooling (Go): `goat repo export <your-hostname>` and
`goat firehose --relay-host wss://YOUR-TUNNEL-HOSTNAME --verify-basic
--verify-mst` — the same validation a relay runs (our CI does this too).

### 4. Announce to a relay — your choice, once

```
python request_crawl.py --relay <the-relay-you-chose> --hostname YOUR-TUNNEL-HOSTNAME
```

An unauthenticated POST of your hostname. That is the entire "signup".

### 5. Post

One-shot, from this directory (stop `peer.py` first — same durable state,
single writer; restart it after):

```
python post.py --domain alice.example.com --state ./state \
    --profile "Alice" --text "hello from a sovereign peer" \
    [--image art.png --alt "description"]
```

Relays pick the commit up on their next crawl; posts made while the peer is
live emit on the firehose immediately. For the agent-composed version, run
the pipeline demo (`cd examples && python -m bluesky_peer.pipeline.demo`):
curator (gated slug DID) publishes signed selection records → poster
(subscribe capability) verifies each one, attaches the image as a real blob,
signs the `app.bsky.feed.post` with the persona pen in-process,
and the commit emits on the public firehose for the relay to index.

### 6. Verify federation (the AppView check)

- `https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle=alice.example.com`
  returns your DID;
- `.../xrpc/app.bsky.feed.getAuthorFeed?actor=<your-did>` shows the post;
- open `https://bsky.app/profile/alice.example.com` — an ordinary account.

## Operational envelope (stated honestly)

- Early-access federation rate-limits self-hosted hosts: **~10 crawled
  accounts per host, ~1500 events/hr, ~10k events/day.** A persona fits
  comfortably. **Never point agent swarms at a public relay** — internal
  repos are gated, slug-addressed, and unannounced by construction.
- The public relay ecosystem may require a contact step or allowlist for new
  PDS hostnames; if indexing stalls after `requestCrawl`, that is the first
  thing to check (then `#account` visibility — this peer emits it).
- Rotation: after `identity.rotate(...)`, re-run step 1 — the static
  artifacts (did.json + chain.json) must be republished.

## What is deliberately absent

No account creation endpoint, no session, no token, no OAuth, no password —
on either side of any request in this directory. Reads are open (public
repo) or capability-gated (presence IS authorization; absence IS
revocation). Writes are in-process pen signatures. The CI test
(`tests/test_bluesky_peer_pipeline.py`) captures every byte that crosses a
wire during the pipeline and asserts no private-key material appears in any
of them — the audit passes vacuously, which is the point: **no secret ever
existed to leak.**
