# How FastATP differs from the MarshalX `atproto` Python SDK

**Compared against:** `atproto` 0.0.69+1 (SHA
`d2e421432623cfd838e4b706d2d287d5a70fc211`) plus `libipld` 3.4.1. The
`reference/` specs and vectors are the only wire oracle; the SDK is production
prior art — a difference is a finding to investigate, never automatically a
defect.

The SDK is excellent at what it is for (a client of hosts); FastATP is for
something different (being a peer). Both statements survive contact with the
code. This document is blunt about where FastATP has gaps and equally blunt
where its design is ahead.

The comparison rests on module-pair reads, behavioral probes with identical
inputs through both implementations, and commit-history archaeology on every
difference. Each finding falls into one category: **ADOPT** (the SDK is right
or production-hardened, and FastATP matches it, with an attributed test),
**ADAPT** (FastATP reuses the SDK's test case, attributed), **DIVERGE**
(FastATP's behavior stays because a named tenet or primitive requires it), or
**OUT-OF-SCOPE**.

---

## Executive summary

- **ADOPT (matched, with attributed tests):** a full operational
  hardening of `WebSocketFrameSource` from their firehose incident history
  (reconnect/backoff, automatic cursor advance, inactivity timeout, 5 MiB
  frames, three-way error triage); `WebResolver.invalidate()`; gap-shaped
  verification failures now route to checkpoint recovery; `$type` stamping
  on catalog-generated object models (fixed a real wire-invalidity);
  `strict=True` lexicon-doc parsing; read-side legacy-2019 verification
  methods.
- **ADAPT:** lexicon-evolution and extended-record suites, header-only
  CAR + trailing-garbage vectors, CID/multikey known-answer pairs,
  legacy-key vectors, and syntax pins for edges no interop vector covers
  (letter-first TIDs, leap seconds, datetime length cap, AT-URI fragments).
  All in `tests/cross_sdk/test_harvested.py`, credited per case.
- **DIVERGEs (each tenet-named below):** header-driven DID caching,
  always-on validation, strict dag-cbor decode, verified CAR ingest, no
  did:plc, no sessions/JWT, user-authored lexicons vs corpus codegen.
- **Wire agreement:** our pure-Python canonical dag-cbor encoder and their
  Rust `libipld` produce **byte-identical** output across the shared
  (data-model-legal) subset, including a torture document; both pass the
  same signature fixtures; every current interop syntax vector agrees in
  both implementations. (One uncovered edge diverges: bare year-0000
  datetimes — see §2.)
- **Benchmark:** pure Python is 12–36× slower than Rust; absolute costs are
  negligible at FastATP's scale. Decision: **acceptable; revisit-at** (below).

---

## 1. dag-cbor / CID / CAR vs `atproto_core`+`libipld`

Their layer is ~120 lines of decode-only wrapper over Rust libipld — no
DAG-CBOR encode API in `atproto_core`, no CAR encoder, no CID hashing: a
client that consumes PDS output. FastATP authors repos, so the asymmetry is
structural. Within the shared surface, every observed difference is libipld
being a lenient generic-IPLD consumer where FastATP is a strict
atproto-data-model verifier. **Zero ADOPTs; ours stays everywhere:**

| Finding | Their behavior | Ours | Category |
| --- | --- | --- | --- |
| Floats | f64 accepted both directions | rejected (data model bars floats) | DIVERGE — byte-exact vector-tested canonical bytes (tenet: the bytes are the proof) |
| Non-minimal int heads on decode | accepted (canonical-form laundering: decode→re-encode changes bytes, breaking CIDs) | rejected | DIVERGE — strict decode is what lets a peer verify foreign bytes |
| bytes vs CID links | libipld sniffs every bytes value on encode; tag-42 decodes to raw bytes; round-trips can flip kinds and change bytes | distinct `CID` objects; byte-faithful | DIVERGE — load-bearing for re-encoding and blob refs. Known cross-impl hazard: SDK apps see links as raw bytes |
| CIDv0 | accepted (decode + inside tag-42) | rejected (vector: "old CIDv0 not supported") | DIVERGE — vector-backed |
| CAR block hashing | **none** — a substituted block is returned keyed by the wrong CID | every block verified against its CID on decode | DIVERGE — no-trusted-third-party ingest; security-relevant |
| CAR raw (0x55) blocks | unsupported ("only DAG-CBOR") | supported | DIVERGE — our blob layer needs raw blocks |
| CAR trailing garbage | tolerated as EOF (their own test vector carries a stray trailing byte and passes only because of this) | rejected (`truncated varint`) | DIVERGE + ADAPT (both forms now pinned) |
| Truncated trailing item in multi-decode | silently dropped | returned as remainder to the caller | DIVERGE — silent loss is wrong for sync streams |
| Encoder parity | — | byte-identical on the shared subset (torture doc verified) | confirmation, pinned in `tests/cross_sdk/` |

Blame highlights: their CAR layer never hashed blocks (present from the
original pure-Python CAR, `c7843c7` #17, kept through the libipld migration
`aa4ee37` #127); the bytes/link conflation arrived with libipld v2
(`2b6b85e` #315), which also grew `ref: Union[str, bytes, IpldLink]` in
their blob model to absorb it.

## 2. crypto / TID / syntax validators vs `atproto_crypto`+`atproto_core`

**Headline: verification semantics are behaviorally identical.** did:key
round-trips agree byte-exactly in both directions; high-S, DER, and every
malformed-signature probe fails closed in both; both pass the same
`signature-fixtures.json` (their low-S enforcement landed `7cf829e` #232).
The SDK is verify-only (no keygen/signing — their signing lives in JWT
service-auth, OUT-OF-SCOPE).

Differences, each pinned by an adapted test where vectors don't cover it:

- **TID:** their validator rejects valid TIDs starting `a`–`j` (an `ord &
  0x40` heuristic from `f062df4` #451 trips on all letters; unnoticed
  because no vector has a letter-first valid TID — ours now does). They
  have **no TID generator at all**: servers mint TIDs in their worldview —
  the RPC-client posture in miniature. DIVERGE + pins.
- **Datetime:**
  - *Year 0000:* they reject bare `0000-…Z`; the Lexicon spec page itself
    lists `0000-01-01T00:00:00.000Z` as a preferred valid example, and the
    TS reference accepts years 0000–9999, erroring only when a datetime
    "normalizes to a negative year" (why `0000-01-01T00:00:00+01:00` IS in
    the invalid vectors; both implementations reject it). Their root cause
    is structural: their final check delegates to Python
    `datetime.fromisoformat`, and `datetime.MINYEAR` is 1. No vector covers
    the bare form; vendored vector files are byte-identical to upstream in
    BOTH packages. DIVERGE + pin — ours matches spec + reference.
  - *Leap seconds:* the reference's regex allows `:60` but its semantic
    `new Date()` parse rejects it (the code comment says the parse "verifies
    semantics like leap seconds"). SDK and reference agree, and FastATP
    agrees: `validate_datetime` rejects `:60`, pinned to match.
  - *64-char cap:* they don't enforce it; the reference hard-rejects >64.
    Real divergence, but reference-impl behavior only — the cap appears
    nowhere in spec text. Frame any report as "the reference enforces",
    never "the spec requires". DIVERGE + pin.
- **AT-URI:** their strict regex rejects JSON-pointer fragments and
  percent-encoded authorities the reference allows. DIVERGE + pins.
- **Opt-in validation:** all their string-format validation is off unless a
  caller passes `strict_string_format=True`. Ours is always-on. DIVERGE —
  signed mail requires validity at signing time (tenet: records are the
  trust boundary), and a *client* of a relay can reasonably be lenient; a
  sovereign verifier cannot.
- **ADOPT:** read-side support for legacy
  `EcdsaSecp256k1/r1VerificationKey2019` verification methods (bare
  base58btc, uncompressed point) — the did spec sanctions reading them
  during the transition; FastATP otherwise fails with `CryptoError` on
  otherwise-valid older did:web docs. Write side unchanged: Multikey only.
  Known-answer vectors adapted from their `test_did.py`.
- Their error taxonomy leaks raw `ValueError` from libipld/cryptography in
  places ours wraps as `CryptoError`; noted, nothing to adopt.

## 3. identity / resolution vs `atproto_identity`

- **Caching:** they cache header-blind (fixed 1h stale / 24h max,
  unbounded dict); ours honors the document author's `Cache-Control`
  (capped, LRU-bounded, monotonic clock) and coalesces concurrent
  resolutions (they issue N parallel GETs). DIVERGE — author sovereignty —
  with one **ADOPT (landed):** an explicit `invalidate(did)` seam, which
  `#identity` events now call through the dispatcher; waiting out a TTL
  after a rotation signal would verify against a rotated-away key.
- **Handle resolution:** DNS TXT + `.well-known` live in a separate SDK
  package with `dnspython` as a hard dependency — and **no bidirectional
  verification** (the DID-doc `alsoKnownAs` check is left to callers).
  FastATP keeps handles out of core (tenet 5, socket-free core); the Gate
  16 runbook documents both directions and the bidirectional requirement.
- **did:web:** they enforce atproto's hostname-only profile; ours is a
  documented superset (path-form twins, `# FASTATP-CONFLICT` at
  `did_web_url`). External confirmation of the twin-slug segmentation rule:
  slug twins are unresolvable by atproto SDKs, exactly as intended for
  never-announced internal identities.
- **did:plc, stated honestly:** their plc resolver is
  `GET https://plc.directory/{did}` — the directory is axiomatically
  trusted (no operation-log verification, no rotation-key checks). DIVERGE,
  tenet 1 (no trusted third party on core paths). FastATP's continuity
  chain is the self-hosted analog of the PLC log, and we verify ours.
  Supporting did:plc honestly would still bottom out in "the directory
  won't equivocate," which no client code fixes. If an example ever needs
  to *read* Bluesky identities, a plc fetch belongs in the example, labeled
  as trusting plc.directory.
- Their identity-resolution HTTP timeout is 3s vs our 10s default; ours is
  per-call configurable — noted, unchanged.

## 4. firehose consumer vs `atproto_firehose` (the operational prize)

Their consumer runs against the production relay at volume; its commit
history is incident archaeology, and it paid for the lessons we ADOPTed
into `WebSocketFrameSource` and the dispatcher (all landed, all tested in
`tests/test_firehose_hardening.py`):

| Lesson | Their incident | Our change |
| --- | --- | --- |
| Reconnect with capped exponential backoff + jitter, reset on success | present since #31, refined #97/#376 | reconnect loop in `WebSocketFrameSource.__aiter__` |
| Resume from a FRESH cursor | `071a6fb` #149: reconnects replayed from the stale constructed cursor; their fix is a manual `update_params` warning | automated: `FrameSource.advance()`, driven by the dispatcher from `mirror.seq` after each verified frame |
| Inactivity timeout | `8b89a24` #520: alive-but-silent server froze feeds until restart; WS pings did not catch it | `recv_timeout` (default 300s) → reconnect |
| 5 MiB max frame | `63537e4` #57 / issue #53: 1 MiB library default dropped real commits, masqueraded as CBOR errors | `max_size=5 MiB` default |
| Decode failure ⇒ skip; transport ⇒ reconnect; NEVER conflate | #54 → #57 → `3436c99` #97: reconnect-on-decode-error = infinite loop on the same frame | triage preserved; error classes explicit |
| Error frames are protocol signals, not bad frames | their `FirehoseError` surfaces to the caller | `FirehoseErrorFrame` exception + dispatcher recovery via the checkpoint seam |
| Stop must win over backoff | their `e5c6a3b`/`4721373` | `_closed` flag checked inside the loop |

Beyond their lessons, one FastATP-specific ADOPT the comparison exposed:
**verification failures deserve recovery, not skipping.** A commit gap
(`since`/`prevData` mismatch) previously warn-looped forever; it now raises
`CommitGap` and routes to the `checkpoint()` seam — a recovery path the SDK
consumer doesn't have because it doesn't verify.

**The headline asymmetry, stated fairly (DIVERGE, by design on both
sides):** their consumer is a *parser* — no signature checks, no MST
verification, no DID resolution — because the atproto division of labor
puts verification in the relay. `RepoMirror` is a *verifier* — signature
against a freshly resolved document, inductive MST re-derivation AND
sync-v1.1 operation inversion, gap continuity — because a sovereign peer
has no trusted relay to lean on. Also aligned in our favor: their async
client awaits handlers inline (a slow handler stalls recv and triggers
ConsumerTooSlow; their #157 added error callbacks after handler crashes
killed clients); our dispatcher decouples handlers behind bounded queues by
design. The SDK repo contains **zero firehose tests**; the
incident-derived tests now in our suite are the tests their history implies.

## 5. lexicon layer vs `atproto_lexicon` + `atproto_codegen`

The philosophies: they **generate** ~1k models from the official corpus
offline (string forward-refs, whole corpus required at codegen time); we
build models **at runtime** from any conforming document and export valid
lexicon JSON back (`class_to_lexicon_json` — no SDK equivalent). DIVERGE,
tenet 4: we ship the type system, users define the lexicons. Inside that
frame, the mechanics comparison found more spec-conformance on our side
than expected — and two real ADOPTs:

- **ADOPT (landed):** catalog-built object models now stamp their canonical
  ref as a defaulted `$type` (their generator's `py_type` pattern, same
  field name for cross-ecosystem familiarity). Probed wire-invalidity: a
  directly constructed union member previously serialized WITHOUT `$type`.
- **ADOPT (landed):** `strict=True` on lexicon-doc parsing — a document
  with `"maxLength": "300"` is malformed, not coercible. Verified safe
  against the full corpus round-trip.
- Their nonconformances (documented, NOT adopted, each probed): `maxLength`
  enforced as code points (spec says UTF-8 bytes); `maxGraphemes` parsed
  then never enforced; `const` mapped to `frozen` with no value check;
  required∩nullable collapsed to optional (their own TODO admits it); open
  unions behave closed at field level (their FIXME); doc parser accepts
  invalid ids, missing record keys, unknown formats, and a `number` type
  that doesn't exist in the data model.
- Their client posture is deliberate leniency (opt-in validation, DotDict
  degradation — never crash on foreign firehose data). Reasonable for a
  client; a sovereign verifier keeps always-on validation (tenet: the
  record is the trust boundary).
- **ADAPT:** their lexicon-evolution suite (new-optional-field
  survives round trips; removed-required errors) and real-world extended
  /custom record cases (`lol: kek`), adapted in `tests/cross_sdk/`.

## 6. Benchmark (dag-cbor / CAR), with decision

Raspberry Pi 5, best-of-5, `uv run` CPython 3.13; libipld 3.4.1 (Rust):

| Workload | FastATP (pure Python) | libipld (Rust) | Ratio |
| --- | --- | --- | --- |
| encode ~296 B record | 25.8 µs | 1.3 µs | 20× |
| decode ~296 B record | 28.4 µs | 1.4 µs | 20× |
| encode ~2.4 KB MST node | 208 µs | 10.4 µs | 20× |
| decode ~2.4 KB MST node | 335 µs | 9.4 µs | 36× |
| decode 300-record repo CAR (70 KB) | 3.6 ms | 0.29 ms | 12× |

The harness also asserts `libipld.encode(libipld.decode(ours)) == ours` —
byte-exact cross-implementation agreement, an interop datapoint in itself.

**Decision: acceptable at design scale.** A peer's envelope is personal +
agent workloads (the public-relay budget is ~1500 evt/hr; a 70 KB repo
checkpoint costs 3.6 ms). Python-side throughput is ~280 repo-CARs/sec —
orders of magnitude above need. **Revisit-at:** a relay-scale consumer or
>10⁴ records/sec workload; the natural home for acceleration is the
existing Rust core (`crates/fastatp-core`), never a third-party C extension
on the Python core path (pure-Python plain-pip install is an invariant).

## 7. Attributed cross-implementation test ledger

All in `tests/cross_sdk/test_harvested.py` (+ `tests/test_firehose_hardening.py`
for incident-derived cases), each attributed `MarshalX/atproto@d2e4214
<path>`:

| Source | What was taken |
| --- | --- |
| `tests/test_atproto_core/test_car.py` | header-only CAR vector, split into valid (trailing byte trimmed) + strict-rejection forms |
| `tests/test_atproto_core/test_cid.py` | CID bytes↔str known-answer pair + cross-instance hash equality |
| `tests/test_atproto_crypto/test_did.py` | legacy-2019 → did:key known answers (k256+p256); foreign multikey round-trips |
| `models/tests/test_changed_lexicon_compatability.py` | lexicon-evolution suite shape |
| `models/tests/test_extended_post_record.py` | extended-record extras round-trip |
| firehose incident history (#31 #57 #97 #149 #157 #376 #520) | the hardening tests (no SDK test existed to copy — written from their incidents) |
| syntax edges their validator disagrees on | letter-first TIDs, leap second, 64-char cap, AT-URI fragment/percent pins |

Their interop-vector usage is a strict subset of ours (no data-model, MST,
CAR, CID, or firehose fixtures; stale copies of the syntax files). Also
noted with a smile: their `test_verify.py` pads base64 with a blind `'=='`
— the same idiom code review caught in a FastATP example, which is already
regression-tested against.

## 8. Divergence → tenet table

| Divergence (ours stays) | Tenet / primitive that requires it |
| --- | --- |
| Strict dag-cbor decode (floats, minimal ints, CIDv1-only, kind-faithful links) | The bytes are the proof: a verifier must re-derive exactly what was signed |
| CAR block hash verification + raw-block support | No trusted third party: ingest verifies, blobs need raw codec |
| Always-on validation (records + string formats) | Records are the trust boundary in signed mail; validity at signing time |
| Header-driven DID caching (+ coalescing) | The DID author controls freshness of their own document |
| No did:plc on core paths | Tenet 1: no central directory; the continuity chain is the verified, self-hosted analog |
| No sessions/JWT/OAuth anywhere in the package | Tenet 7: no shared secrets; auth UX composes on top at the edge |
| User-authored lexicons + JSON export (vs corpus codegen) | Tenet 4: ship the type system, not the schemas; the market defines records |
| Verifying firehose consumer (signature + inversion + gaps) | Symmetric peers have no relay to outsource verification to |
| TID generation in-library | A peer mints its own revs/rkeys; there is no server to do it |

## 9. sensorthings interop

`examples/sensorthings_interop/`: FastATP-authored `dev.sensorthings.*`
records (user dicts; we ship no schemas), signed by a twin-only slug-DID
pen, exported with `export_car()` — flattened correctly by the UNMODIFIED
`atproto-sensorthings` package (Meitheal / Stephan Hügel, built on this
same SDK): geoPoint E7 integers → WGS84 degrees, AT-URI joins across
records, scaled integers descaled. Two independent implementations
composing over nothing but the wire format; CI test skips cleanly when the
package is absent.
