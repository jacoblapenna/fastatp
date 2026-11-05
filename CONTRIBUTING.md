# Contributing to FastATP

Thanks for considering a contribution. This page is short because the real
rules live in two files you should read first.

## Read order

1. [`PHILOSOPHY.md`](PHILOSOPHY.md) — canonical. It says what FastATP is and
   what it refuses to be. Every change is measured against it.
2. [`README.md`](README.md) — the front door and the API at a glance.
3. [`AGENTS.md`](AGENTS.md) — if you work with an AI coding agent, seed it
   with this file before it writes a line. It carries the guardian mandate:
   the agent is expected to push back on off-philosophy requests, including
   yours.
4. [`docs/decisions/`](docs/decisions/) — the architecture decision records.

## The one rule

**Every pull request states which tenet it serves.** The PR template asks
for it and it is not optional. FastATP is *signed mail between sovereign
peers, not RPC with a login*. The tenets, in short:

- No trusted third party: no required central service, registry, token
  issuer, or default hostname on any core path.
- Data, not calls: the unit of exchange is a signed, content-addressed
  record.
- Symmetric peers: every peer signs what it sends and verifies what it
  receives.
- Ship primitives, not applications: no application schemas in the package.

A change that conflicts with a tenet is wrong until the tenet is
deliberately revised by a decision record in `docs/decisions/`. It is never
worked around silently in code. If you want something that seems
off-philosophy, open a *philosophy question* issue and argue the case in the
open; there is usually an on-philosophy shape for the same goal.

## Wire format

Bytes on the wire follow the AT Protocol specifications and the interop
vectors, which are vendored under `tests/vectors/`. A change to serialization,
hashing, signing, or schema handling cites the spec section or vector that
makes it correct. Don't guess; conform. Crypto never gets hand-rolled: the
`cryptography` package is the only primitive source.

`reference/` is for local clones of the upstream specs and SDKs. It is
gitignored and must stay that way. Never commit it.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy fastatp
```

`uv` is a convenience for development only. The package must always install
with plain `pip install -e .`, and CI checks that it does.
[`docs/TESTING.md`](docs/TESTING.md) explains the optional tests (browser
bundle, WireGuard, cross-implementation) and how to enable them.

Conventions: Python 3.11+, Pydantic v2, async-first resolvers, ruff and mypy
clean, one `pyproject.toml` as the source of truth. Examples and tests use
generic identifiers only (`example.com`, `example.org`, `com.example.*`).

## What gates a merge

The deterministic test suite and the conformance vectors are the only gate.
Any AI-assisted review that runs on a pull request is advisory: read it,
apply what you agree with, and say briefly why you disagree with the rest.
No review finding, on its own, blocks a merge.

## Decision records

Ambiguous design calls get a short record in `docs/decisions/`: the problem,
the decision, the consequences. Keep them short, and don't block work on
them.

## Changelog and versions

Add a line under *Unreleased* in [`CHANGELOG.md`](CHANGELOG.md) for anything
a user would notice, under the heading that says what kind of change it is.
While FastATP is 0.x, the heading decides the next version:

- **Breaking** (public API changed or removed, or a support floor raised):
  the next release is a minor bump, 0.2.x to 0.3.0.
- **Added**, **Fixed**, **Security** and the like, with nothing broken: the
  next release is a patch bump, 0.2.0 to 0.2.1.

Feature PRs never set a release version. `main` says exactly the release
version only while nothing has changed since that release; after that it
carries the next version as a dev release, so a git install never claims to
be a release it isn't:

1. **Release: one PR.** It sets the exact version (for example `0.2.0`, in
   `pyproject.toml` and the Rust crates, then the locks), turns *Unreleased*
   into that version's section, and does nothing else. After it merges, the
   maintainer tags the merge commit `v0.2.0rc1`, which CI builds as
   `0.2.0rc1` for TestPyPI. Once that checks out, the same commit is tagged
   `v0.2.0` for PyPI. If the rc turns up a problem, fix it in a normal PR
   that leaves the version at `0.2.0` (it isn't released yet, so no dev
   bump), then tag the new merge commit `v0.2.0rc2`. Tags are never moved.
2. **The next PR moves `main` to dev, in its own first commit.** Whatever
   the first change after a release is, if the version in `pyproject.toml`
   has already been released (its `vX.Y.Z` tag exists), that PR's first
   commit bumps it to the next patch's dev release, `0.2.1.dev0`
   (crates and locks too), and nothing else. There is no PR whose only
   change is the dev bump: until something changes, `main` really is the
   release. (A dev release sorts before its release, so `0.2.0.dev0` would
   mean "before 0.2.0".)
3. **A breaking PR raises the dev version** to the next minor, `0.3.0.dev0`,
   if it isn't there already, in the same kind of dedicated first commit.
4. **The next release PR drops the `.devN`.**

Dev versions never publish: `release.yml` refuses any tag that isn't
`vX.Y.Z` or `vX.Y.ZrcN` on a commit whose version is exactly `X.Y.Z`.

The version lives in `pyproject.toml`; the Rust crates in `rust/crates/*`
carry the same number (Cargo spells a dev version `0.2.1-dev.0`).

## Reporting

- Bugs and features: GitHub issues, using the templates.
- Security: see [`SECURITY.md`](SECURITY.md). Please don't open a public
  issue for a vulnerability.
- Conduct: see [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
