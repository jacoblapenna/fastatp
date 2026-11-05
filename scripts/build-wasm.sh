#!/usr/bin/env bash
# Build the fastatp-wasm browser bundle into fastatp/browser/static/.
#
# Runnable from any CWD. Requires the wasm32-unknown-unknown target and the
# exact tool versions pinned in scripts/wasm-tools.env on PATH (install them
# with scripts/install-wasm-tools.sh). CI runs this before wheel/sdist
# assembly so the artifact ships in both and plain `pip install` never needs
# a Rust toolchain.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRATE="$ROOT/rust/crates/fastatp-wasm"
OUT="$ROOT/fastatp/browser/static"
# shellcheck source=SCRIPTDIR/wasm-tools.env
. "$ROOT/scripts/wasm-tools.env"

if [ ! -d "$CRATE" ]; then
    echo "build-wasm: $CRATE does not exist (fastatp-wasm crate not built yet); nothing to build" >&2
    exit 1
fi

if ! command -v cargo >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    [ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
fi

# Every tool must be the pinned version. wasm-pack runs with --mode
# no-install, so it never downloads wasm-bindgen or wasm-opt itself; and
# without wasm-opt it would silently ship an unoptimized bundle, so a missing
# tool is an error here, not a skip.
require() { # require TOOL EXPECTED-VERSION-LINE
    local got
    if ! got="$("$1" --version 2>/dev/null)"; then
        got="not found"
    fi
    if [ "$got" != "$2" ]; then
        echo "build-wasm: need '$2' on PATH, found: $got" >&2
        echo "build-wasm: run scripts/install-wasm-tools.sh DIR and put DIR on PATH" >&2
        exit 1
    fi
}
require wasm-pack "wasm-pack $WASM_PACK_VERSION"
require wasm-bindgen "wasm-bindgen $WASM_BINDGEN_VERSION"
require wasm-opt "wasm-opt version $BINARYEN_VERSION (version_$BINARYEN_VERSION)"

rm -rf "$OUT"
# everything after -- goes to cargo: build exactly what rust/Cargo.lock says
wasm-pack build "$CRATE" --mode no-install --target web --release \
    --out-dir "$OUT" --out-name fastatp_wasm -- --locked

# wasm-pack scaffolding that must not ship inside the wheel
rm -f "$OUT/.gitignore" "$OUT/package.json" "$OUT/README.md" "$OUT"/LICENSE*

# stamp the bundle with the package version (lockstep is enforced at mount);
# tomllib is 3.11+ and the package supports 3.10, so fall back to a regex
VERSION="$(ROOT="$ROOT" python3 -c '
import os, pathlib, re
pyproject = pathlib.Path(os.environ["ROOT"]) / "pyproject.toml"
try:
    import tomllib
    version = tomllib.load(pyproject.open("rb"))["project"]["version"]
except ModuleNotFoundError:  # Python 3.10
    match = re.search(r"^version\s*=\s*\"([^\"]+)\"", pyproject.read_text(), re.M)
    if match is None:
        raise SystemExit("build-wasm: no project.version in pyproject.toml")
    version = match.group(1)
print(version)
')"
printf '%s\n' "$VERSION" > "$OUT/VERSION"
echo "build-wasm: built $OUT (version $VERSION)"
