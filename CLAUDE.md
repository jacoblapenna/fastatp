# CLAUDE.md — FastATP

## What this is
Transport-agnostic Python library: AT Protocol identity + signed records for
agent<->agent and user<->agent workflows. **Not** Bluesky; deliberately avoids
social-web assumptions. Read `PHILOSOPHY.md` (canonical) and `README.md` before
doing anything.

## Philosophy (governing)

`PHILOSOPHY.md` is canonical and governs the package: FastATP is *signed mail
between sovereign peers, not RPC with a login*. Every feature and contribution
is measured against its tenets — no trusted third party, data-not-calls,
symmetric peers, ship-primitives-not-applications, the market emerges. A change
that conflicts with the philosophy is wrong until `PHILOSOPHY.md` is
deliberately revised by ADR — never worked around silently. In particular: do
not ship application schemas, do not add a required central service / token
issuer to a core path, do not make bearer-token auth or synchronous
connection-holding RPC the primary model, and keep the signing core socket-free
and workload-agnostic (not AI-specific).

## Reference material (authoritative for wire format)
Everything under `reference/` is vendored protocol truth. It is gitignored and
never committed — a fresh clone won't have it; clone the upstream repos into
`reference/` locally when you need them. Conform to it; don't guess.
- `reference/atproto-specs/` (clone of `bluesky-social/atproto-website`) — the spec
  prose: repository, data-model, lexicon, did, sync. Highest authority.
- `reference/atproto/lexicons/com/atproto/` — the **protocol** lexicons. This is your
  corpus: drive Pydantic model generation and round-trip tests from these.
- `reference/atproto/lexicons/app/bsky/` — Bluesky-the-app schemas. **Do NOT depend
  on or ship these.** Read them only as examples of the Lexicon language in use.
- `reference/interop-tests/` and `reference/atproto/interop-test-files/` — CC-0
  conformance vectors (CID, dag-cbor, varint, did, tid, MST, ...). The oracle for
  the canonical-record and repo conformance suites. The vector files are vendored
  in-repo at `tests/vectors/` (see
  its PROVENANCE.md), so the conformance suite runs on a fresh clone; tests that
  need a full reference clone skip cleanly when it's absent.
- `reference/atproto/packages/repo/` — the TypeScript MST reference implementation.
  Read it when the MST or CAR encoding gets fiddly.

## Lexicon layer (the user-facing point of the library)
Users define **any atproto-conforming lexicon** in Pydantic. Make that the easy path:
- Lexicon and Pydantic are different type systems. Provide a type palette so
  users never hand-roll format/constraint validators.
- `maxLength`/`minLength` are **UTF-8 byte** counts; `maxGraphemes`/`minGraphemes`
  are **grapheme cluster** counts. Neither is Python `len(str)`.
- Compound types (`cid-link`, `bytes`, `blob`) have specific data-model encodings —
  see the data-model spec; do not use JSON base64 in dag-cbor.
- Unions: `$type`-discriminated, `closed` flag, variants are object/record only.
- `Lexicon.to_lexicon_json()` must emit a valid `lexicon: 1` doc; this is what makes
  a Pydantic model genuinely conforming (resolvable/validatable by others), not just
  FastATP-internal.
- Scope = *any conforming* lexicon, not *any* Pydantic model. Validate on export and
  reject constructs with no Lexicon equivalent.

## Crypto rules (interop-critical, easy to get subtly wrong)
- Curves: **secp256k1 ("k256")** and **NIST P-256 ("p256")**. Support both.
- Signatures: ECDSA, **low-S normalized**, **compact 64-byte** form (R||S), NOT DER.
  Verification MUST reject high-S signatures.
- Public keys in DID docs: **Multikey / `did:key`** multibase (base58btc, `z` prefix)
  with the correct multicodec prefix per curve (compressed point).
- Use `cryptography`. Never hand-roll primitives. (There is no JWT record format and no `PyJWT` dependency — do not add one; record proofs are the one wire format.)

## Conventions
- Python >= 3.10, Pydantic v2, async-first (resolvers are `async`).
- **Single PEP 621 `pyproject.toml`** is the source of truth: project metadata, dependencies,
  and all tool config (`ruff`, `mypy`, `pytest`, coverage) live there. No `setup.py`; no
  per-tool config files unless a tool genuinely can't live in pyproject. Standard build
  backend (hatchling).
- **Prefer `uv` for the dev workflow**: `uv venv`, `uv pip install -e ".[dev]"`,
  `uv run pytest`, `uv run ruff`, `uv run mypy`. uv is a dev accelerator ONLY — it must not
  become a runtime dependency, and the shipped package must stay installable with plain
  `pip` (plain `pip install -e .` must work). If `uv` isn't on the machine, install it first,
  then proceed.
- `ruff` + `mypy` clean. Tests: `pytest` + `pytest-asyncio` + `pytest-cov`.
- **`.gitignore` already exists and ignores `reference/`** (plus caches/venv). Extend it as
  needed (`.venv/`, build artifacts, uv cache), but NEVER remove the `reference/` entry —
  those vendored repos must never be committed or pushed.
- Package layout follows the existing `fastatp/` module structure; keep new modules consistent with it.
- Public exports stay limited to the current `fastatp/__init__.py` list until an addition genuinely needs more.

## Decisions
Ambiguous design calls -> short ADR in `docs/decisions/`. Don't block on them.

## Code review (AI review is advisory, NEVER blocking)
The deterministic tests/vectors are the only gate. AI code review is a
reference: read findings, apply the ones you agree with in a follow-up commit,
and move on. No review finding, severity label, or review-tool failure is ever
a reason to halt, block, retry-loop, or stall the build. If you disagree with
a finding, don't act on it and don't stop — note the disagreement briefly and
proceed.

## Authority on conflict
`PHILOSOPHY.md` is the design and vision; atproto specs/vectors are the wire format and the
Lexicon schema language. When they conflict: keep the philosophy's intent
intact, and follow atproto for serialization and schema rules — but do NOT silently
pick a side. Leave a `# FASTATP-CONFLICT:` comment at the site (or a note in
`docs/decisions/`) describing the divergence and the choice made, so it can be reviewed
later. Naming and API shape follow the established package conventions; bytes on the wire still follow atproto.
