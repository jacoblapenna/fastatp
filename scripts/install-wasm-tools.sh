#!/usr/bin/env bash
# Install the pinned browser-bundle build tools (wasm-pack, wasm-bindgen,
# wasm-opt) into DEST, verifying every download against the SHA-256 pinned in
# scripts/wasm-tools.env. Add DEST to PATH, then run scripts/build-wasm.sh.
#
#   scripts/install-wasm-tools.sh DEST
#
# x86_64 Linux only (what CI runs). Elsewhere, install the same versions by
# other means; build-wasm.sh checks the versions, not where they came from.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=SCRIPTDIR/wasm-tools.env
. "$ROOT/scripts/wasm-tools.env"

DEST="${1:?usage: scripts/install-wasm-tools.sh DEST}"

if [ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]; then
    echo "install-wasm-tools: only x86_64 Linux is pinned here; install wasm-pack" \
        "$WASM_PACK_VERSION, wasm-bindgen-cli $WASM_BINDGEN_VERSION and binaryen" \
        "version_$BINARYEN_VERSION yourself" >&2
    exit 1
fi

# the wasm-bindgen CLI must match the crate the bundle is compiled against
LOCKED="$(awk '$0 == "name = \"wasm-bindgen\"" { getline; gsub(/version = |"/, ""); print; exit }' \
    "$ROOT/rust/Cargo.lock")"
if [ "$LOCKED" != "$WASM_BINDGEN_VERSION" ]; then
    echo "install-wasm-tools: rust/Cargo.lock has wasm-bindgen $LOCKED but" \
        "scripts/wasm-tools.env pins the CLI at $WASM_BINDGEN_VERSION; update the pin" >&2
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# fetch URL SHA256 NAME: download to $WORK/NAME, fail unless the hash matches
fetch() {
    curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
        --retry 3 --output "$WORK/$3" "$1"
    if ! echo "$2  $WORK/$3" | sha256sum --check --strict --status; then
        echo "install-wasm-tools: SHA-256 mismatch for $1" >&2
        echo "  expected $2" >&2
        echo "  got      $(sha256sum "$WORK/$3" | cut -d' ' -f1)" >&2
        exit 1
    fi
}

fetch "https://github.com/wasm-bindgen/wasm-pack/releases/download/v$WASM_PACK_VERSION/wasm-pack-v$WASM_PACK_VERSION-x86_64-unknown-linux-musl.tar.gz" \
    "$WASM_PACK_SHA256" wasm-pack.tar.gz
fetch "https://github.com/wasm-bindgen/wasm-bindgen/releases/download/$WASM_BINDGEN_VERSION/wasm-bindgen-$WASM_BINDGEN_VERSION-x86_64-unknown-linux-musl.tar.gz" \
    "$WASM_BINDGEN_SHA256" wasm-bindgen.tar.gz
fetch "https://github.com/WebAssembly/binaryen/releases/download/version_$BINARYEN_VERSION/binaryen-version_$BINARYEN_VERSION-x86_64-linux.tar.gz" \
    "$BINARYEN_SHA256" binaryen.tar.gz

# extract only the binaries the build uses
tar -xzf "$WORK/wasm-pack.tar.gz" -C "$WORK" \
    "wasm-pack-v$WASM_PACK_VERSION-x86_64-unknown-linux-musl/wasm-pack"
tar -xzf "$WORK/wasm-bindgen.tar.gz" -C "$WORK" \
    "wasm-bindgen-$WASM_BINDGEN_VERSION-x86_64-unknown-linux-musl/wasm-bindgen"
tar -xzf "$WORK/binaryen.tar.gz" -C "$WORK" \
    "binaryen-version_$BINARYEN_VERSION/bin/wasm-opt"

mkdir -p "$DEST"
install -m 0755 "$WORK/wasm-pack-v$WASM_PACK_VERSION-x86_64-unknown-linux-musl/wasm-pack" "$DEST/"
install -m 0755 "$WORK/wasm-bindgen-$WASM_BINDGEN_VERSION-x86_64-unknown-linux-musl/wasm-bindgen" "$DEST/"
install -m 0755 "$WORK/binaryen-version_$BINARYEN_VERSION/bin/wasm-opt" "$DEST/"

echo "install-wasm-tools: installed into $DEST:"
"$DEST/wasm-pack" --version
"$DEST/wasm-bindgen" --version
"$DEST/wasm-opt" --version
