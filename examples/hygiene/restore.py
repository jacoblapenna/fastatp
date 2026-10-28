"""Restore a ``backup.py`` snapshot into a state dir — refusing the footgun.

The footgun: ``Identity.bootstrap()`` decides "does this identity exist?" by
the presence of ``identity.json``. Point a peer at an incompletely restored
state dir and bootstrap does not error — it silently mints a brand-new
identity (new keys, new genesis, new twin) under your durable domain, which
every continuity verifier then sees as an unauthorized key swap. This script
therefore hard-refuses a backup without ``identity.json``, and refuses to
overwrite an existing identity without ``--force``.

After copying, it verifies the restore by actually loading the identity
(which fail-closes on a corrupt head or tampered chain) and prints the DID.
If the backup was taken with ``--exclude-secrets``, place ``signing.key``
from your password manager into the state dir when prompted.

Usage:
  python restore.py --backup /backups/alice-2026-07-19 --state ../bluesky_peer/state
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from fastatp.identity import Identity

SECRET_FILES = ("signing.key", "rotation.key")
PLAIN_FILES = ("identity.json", "chain.json", "repo_head", "blocks.sqlite", "blobs.sqlite")


def copy_secret(src: Path, dst: Path) -> None:
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, src.read_bytes())
    finally:
        os.close(fd)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backup", required=True, help="snapshot dir produced by backup.py")
    ap.add_argument("--state", required=True, help="state dir to restore into")
    ap.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing identity in --state (the current one is discarded)",
    )
    args = ap.parse_args()

    backup = Path(args.backup)
    state = Path(args.state)

    if not (backup / "identity.json").exists():
        sys.exit(
            f"{backup} has no identity.json — refusing. Restoring without it leaves a "
            "state dir that bootstrap treats as empty and silently RE-MINTS a new "
            "identity under your domain. Find a complete snapshot."
        )
    if (state / "identity.json").exists() and not args.force:
        sys.exit(f"{state} already holds an identity — pass --force to replace it")

    state.mkdir(parents=True, exist_ok=True)
    for name in PLAIN_FILES:
        if (backup / name).exists():
            shutil.copy2(backup / name, state / name)
            print(f"restored {name}")
    for name in SECRET_FILES:
        if (backup / name).exists():
            copy_secret(backup / name, state / name)
            print(f"restored {name} (0600)")
    # stale WAL/SHM sidecars from a previous occupant would shadow the
    # restored databases; the snapshot is self-contained, so drop them
    for stale in (*state.glob("*.sqlite-wal"), *state.glob("*.sqlite-shm")):
        stale.unlink()
        print(f"removed stale {stale.name}")

    if not (state / "signing.key").exists():
        sys.exit(
            "restore is on disk, but the backup carried no signing.key "
            "(--exclude-secrets). Place it from your password manager at "
            f"{state / 'signing.key'} (mode 0600), then rerun this script to verify."
        )

    # the real test: load it. _load fail-closes on a head that doesn't hash
    # or a tampered chain, so surviving this means the restore is coherent.
    identity = Identity.bootstrap(state_dir=state)
    print(f"verified: {identity.did} loads from {state}")


if __name__ == "__main__":
    main()
