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
- **Commit:** `2ed82a5ccf1b424aa5e2f6c5b461dc0ee133278b` (2026-07)
- **License:** dual MIT / Apache-2.0 (Bluesky Social PBC). The vectors are
  captured public PLC directory log data plus synthesized invalid variants.

To refresh: re-copy from a clone of the upstream repo at a newer commit and
update the commit hash above. Do NOT hand-edit vector expectations — if a
rename ever touches a byte-length/CID-sensitive vector, regenerate it
honestly from the source, never fudge it.

Corpus-scale fixtures that need a FULL reference clone (e.g. the
`com.atproto.*` lexicon round-trip, which reads every schema in
`bluesky-social/atproto`) are NOT vendored here and keep their runtime skip
guards — only the self-contained vector files live in-repo.
