## What this changes

<!-- One paragraph. Present tense. -->

## Which tenet it serves

<!-- Required. Name the tenet(s) from PHILOSOPHY.md and say how. A change that
     conflicts with a tenet needs a decision record in docs/decisions/ that
     revises the tenet first. -->

## Wire format

- [ ] No wire-format or crypto behaviour changed, **or**
- [ ] It changed, and the relevant atproto spec section / interop vector is cited here:

## Checks

- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run mypy fastatp`
- [ ] `uv run pytest` is green locally
- [ ] No application schemas, no required central service, no bearer-token auth path added
- [ ] Examples use generic identifiers only (`example.com`, `com.example.*`)
- [ ] CHANGELOG.md updated under *Unreleased*
