# Conformance vectors — provenance

These are the AT Protocol interop conformance vectors, vendored into the
repository so a fresh clone runs the wire-conformance suite instead of
skipping it. They are the public repo's verifiability.

- **Source:** <https://github.com/bluesky-social/atproto-interop-tests>
- **Commit:** `056e5741bb330757205d6b16db5266fffcae937b` (2026-07-01)
- **License:** CC-0 (public domain dedication) — see `LICENSE-CC0`. The
  upstream README explicitly invites copying these files into downstream
  test suites.

Subset vendored (only the vector files the suite reads): `syntax/`, `mst/`,
`crypto/`, `data-model/`, `firehose/`, `lexicon/`.

## `plc/` — did:plc audit-log conformance vectors

- **Source:** <https://github.com/did-method-plc/did-method-plc>
  (`interop_tests/audit_log/`; upstream notes their canonical home is
  <https://github.com/did-method-plc/go-didplc> `testdata/`)
- **Commit:** `29f1a70ee9f6f80a25946cac103b2f745e371334` (2026-10-27)
- **License:** dual MIT / Apache-2.0 (Bluesky Social PBC). The vectors are
  captured public PLC directory log data plus synthesized invalid variants.

To refresh: re-copy from a clone of the upstream repo at a newer commit and
update the commit hash above. Do NOT hand-edit vector expectations — if a
rename ever touches a byte-length/CID-sensitive vector, regenerate it
honestly from the source, never fudge it.

## `lexicons/com/atproto/` — the AT Protocol's own lexicon corpus

- **Source:** <https://github.com/bluesky-social/atproto>
  (`lexicons/com/atproto/`, copied verbatim with its folder layout)
- **Commit:** `ba75afad9fab6d1f5c207b1b5d12a7ecfc5b379d` (2026-11-04). The
  newest upstream change to `lexicons/com/atproto/` at that point was
  `9537a659a` ("Label record Lexicon", 2026-11-04).
- **License:** dual MIT / Apache-2.0 (Bluesky Social PBC); see
  `lexicons/LICENSE.txt`, `LICENSE-MIT.txt` and `LICENSE-APACHE.txt`.

Read by `tests/test_lexicon_doc.py` (every schema round-trips through
`LexiconDoc` unchanged) and `tests/test_lexicon_bridge.py` (Pydantic codegen
over the whole corpus). These tests are required: a missing corpus fails
them instead of skipping. Only the protocol's `com.atproto.*` schemas are
vendored; Bluesky's application lexicons (`app.bsky.*`) are deliberately
not, and the package never ships any of these (test data only).

To refresh: copy `lexicons/com/atproto/` from a clean clone at a newer
commit over this directory (delete first, so removed schemas go too),
update the commit above, and run the suite. A new lexicon construct that
fails to round-trip is a real Lexicon-layer gap; fix the library, never the
vector.
