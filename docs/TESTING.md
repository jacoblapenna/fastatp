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
| `test_hosted_peer_e2e`, `test_wheel_smoke`, `test_browser_assets` | the shipped browser (WASM) peer works | Build the bundle: `scripts/build-wasm.sh` (needs Rust + `wasm-pack` + the `wasm32-unknown-unknown` target). The bundle is a build artifact, git-ignored; it ships inside the wheel. |
| `cross_sdk/test_harvested` | our DAG-CBOR/CID bytes match the reference Rust codec | `libipld` — already in `.[dev]`. |
| `test_wireguard_rootless_netns` | capability grant/revoke actually gates a real tunnel, unprivileged | `wireguard-tools` (`sudo apt install wireguard-tools`). Runs rootless. |
| `test_wireguard_netns` | the same, end-to-end through a kernel WireGuard interface | `wireguard-tools` **and** run under `sudo` (creates network namespaces). |
| `test_goat_oracle` | Bluesky's own `goat` firehose validator accepts our frames | Install Go, then `go install github.com/bluesky-social/indigo/cmd/goat@latest`. The test finds `goat` on `PATH` or at `~/go/bin/goat`. |
| `test_sensorthings_interop` | an independent third-party implementation can flatten a FastATP CAR | `pip install -e ".[interop]"` — **note:** this pulls in the MarshalX `atproto` SDK (the SDK FastATP deliberately does not depend on) and its pins. Prefer a separate venv if you don't want the SDK in your dev environment. |

Build the WASM bundle and install `wireguard-tools` + Go, and the full suite
runs with nothing skipped (the kernel-netns case still needs `sudo`).

## Coverage

Coverage is configured (`[tool.coverage.run]`: branch coverage over `fastatp/`)
but not wired into the default run. Measure it explicitly:

```bash
uv run pytest --cov=fastatp --cov-report=term-missing
```

The number is only meaningful with the optional capabilities installed — the
WASM and WireGuard tests exercise real code paths (`fastatp/browser/`, the live
`wg` interface in `fastatp/authz/wireguard.py`) that otherwise show as missed.
