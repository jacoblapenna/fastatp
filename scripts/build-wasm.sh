#!/usr/bin/env bash
# Build the fastatp-wasm browser bundle into fastatp/browser/static/.
#
# Runnable from any CWD. Requires wasm-pack and the wasm32-unknown-unknown
# target. CI runs this before wheel/sdist assembly so the artifact ships in
# both and plain `pip install` never needs a Rust toolchain.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRATE="$ROOT/rust/crates/fastatp-wasm"
OUT="$ROOT/fastatp/browser/static"

if [ ! -d "$CRATE" ]; then
    echo "build-wasm: $CRATE does not exist (fastatp-wasm crate not built yet); nothing to build" >&2
    exit 1
fi

if ! command -v wasm-pack >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    [ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
fi
if ! command -v wasm-pack >/dev/null 2>&1; then
    echo "build-wasm: wasm-pack not found (install rustup + 'cargo binstall wasm-pack')" >&2
    exit 1
fi

rm -rf "$OUT"
wasm-pack build "$CRATE" --target web --release --out-dir "$OUT" --out-name fastatp_wasm

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
