# Running the tests

The test suite lives in the repository (a `git clone` has it; a `pip install`
does not). Every test **skips cleanly** when a capability it needs is absent, so
the baseline is always green:

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
```

That baseline runs the entire pure-Python suite — identity, crypto, the strict
DAG-CBOR/CID codec, the full wire-conformance vectors (vendored in
`tests/vectors/`, so they never skip), repos, the firehose, authorization, the
PeerApp framework — **plus the cross-implementation differential codec check**
(`libipld` ships in the `dev` extra).

A skipped test is not a hole in the library; it means an *optional external
capability* is not installed on this machine. Each one is the only proof of a
real capability, so the goal on a dev box (and in CI) is to install the
prerequisites and run them all. Here is what each needs.

## Optional capabilities and how to enable them

| Test(s) | Proves | Prerequisite |
| --- | --- | --- |
| `test_hosted_peer_e2e`, `test_wheel_smoke`, `test_browser_assets` | the shipped browser (WASM) peer works | Build the bundle: `scripts/build-wasm.sh` (needs Rust, the `wasm32-unknown-unknown` target, and the exact wasm-pack, wasm-bindgen and wasm-opt versions pinned in `scripts/wasm-tools.env`; on x86_64 Linux, `scripts/install-wasm-tools.sh DIR` installs them hash-checked into `DIR`). The bundle is a build artifact, git-ignored; it ships inside the wheel. |
| `cross_sdk/test_harvested` | our DAG-CBOR/CID bytes match the reference Rust codec | `libipld` — already in `.[dev]`. |
| `test_wireguard_rootless_netns` | capability grant/revoke actually gates a real tunnel, unprivileged | `wireguard-tools` (`sudo apt install wireguard-tools`). Runs rootless. On Ubuntu 24.04+, unprivileged user namespaces may be restricted: `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` (set it back to 1 after). |
| `test_wireguard_netns` | the same, end-to-end through a kernel WireGuard interface | `wireguard-tools` **and** run under `sudo` (creates network namespaces): `sudo env "PATH=$PATH" .venv/bin/python -m pytest tests/test_wireguard_netns.py -v`. Runs in CI in the WireGuard job. |
| `test_goat_oracle` | Bluesky's own `goat` firehose validator accepts our frames | On x86_64 Linux, `scripts/install-goat.sh DIR` installs the pinned, hash-checked release binary into `DIR`; put `DIR` on `PATH`. Elsewhere, install the same version from `bluesky-social/goat` releases. The test finds `goat` on `PATH` or at `~/go/bin/goat`. Runs in CI in the `interop oracles` job. |
| `test_sensorthings_interop` | an independent third-party implementation can flatten a FastATP CAR | `pip install -e ".[interop]"` — **note:** this pulls in the MarshalX `atproto` SDK (the SDK FastATP deliberately does not depend on) and its pins. Prefer a separate venv if you don't want the SDK in your dev environment. |

Build the WASM bundle and install `wireguard-tools` + Go, and the full suite
runs with nothing skipped (the kernel-netns case still needs `sudo`).

## In CI: every test must run somewhere

Each CI job skips some tests on purpose: the Python version matrix has no
browser bundle, goat or root, and the specialist jobs (browser peer bundle,
interop oracles, WireGuard) run exactly those. Locally, a skip is fine. In
CI, a test that skips in **every** job never runs at all, so the
`all tests ran` job forbids it:

- every pytest job saves a JUnit report;
- `all tests ran` merges them with `scripts/check_all_tests_ran.py` and fails
  if any test never passed in any job, if a module skipped at import never
  ran anywhere else, or if a test file appears in no report.

When you add a test that needs something new (a tool, root, a service
stand-in), give it a CI job or step that provides it, or the guard will fail
the PR and name the test.

This is a CI-only check; nothing changes locally (`uv run pytest` as usual).
The reports are temporary build artifacts of each CI run, never committed.
Running the script over a single local run would flag every test your
machine can't run, which is expected: CI's other jobs cover those.

## Coverage

Coverage is configured (`[tool.coverage.run]`: branch coverage over `fastatp/`)
but not wired into the default run. Measure it explicitly:

```bash
uv run pytest --cov=fastatp --cov-report=term-missing
```

The number is only meaningful with the optional capabilities installed — the
WASM and WireGuard tests exercise real code paths (`fastatp/browser/`, the live
`wg` interface in `fastatp/authz/wireguard.py`) that otherwise show as missed.
