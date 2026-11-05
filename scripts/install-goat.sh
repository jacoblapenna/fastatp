#!/usr/bin/env bash
# Install Bluesky's `goat` CLI into DEST, pinned by version and SHA-256.
# tests/test_goat_oracle.py uses it as an external oracle: goat runs the
# network's own firehose validation against a FastATP PeerServer.
#
#   scripts/install-goat.sh DEST
#
# x86_64 Linux only (what CI runs). Bumping is by hand, never by bot: change
# the version, hash the release asset, and cross-check the hash against both
# the release's goat_<version>_checksums.txt and GitHub's asset digest.
set -euo pipefail

# https://github.com/bluesky-social/goat/releases
GOAT_VERSION=0.2.8
GOAT_SHA256=5b35f122f4e97787ac8c5ca1fc26d7afe5541f2999ecfa2eac725a7443798d08

DEST="${1:?usage: scripts/install-goat.sh DEST}"

if [ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]; then
    echo "install-goat: only x86_64 Linux is pinned here; install goat $GOAT_VERSION yourself" >&2
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

URL="https://github.com/bluesky-social/goat/releases/download/v$GOAT_VERSION/goat_Linux_x86_64.tar.gz"
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
    --retry 3 --output "$WORK/goat.tar.gz" "$URL"
if ! echo "$GOAT_SHA256  $WORK/goat.tar.gz" | sha256sum --check --strict --status; then
    echo "install-goat: SHA-256 mismatch for $URL" >&2
    echo "  expected $GOAT_SHA256" >&2
    echo "  got      $(sha256sum "$WORK/goat.tar.gz" | cut -d' ' -f1)" >&2
    exit 1
fi

tar -xzf "$WORK/goat.tar.gz" -C "$WORK" goat
mkdir -p "$DEST"
install -m 0755 "$WORK/goat" "$DEST/"

got="$("$DEST/goat" --version)"
if [ "$got" != "goat version $GOAT_VERSION" ]; then
    echo "install-goat: installed binary reports '$got', expected goat version $GOAT_VERSION" >&2
    exit 1
fi
echo "install-goat: installed $got into $DEST"
