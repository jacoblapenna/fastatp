"""Snapshot a FastATP ``state/`` directory into a consistent backup.

Both stores are WAL-mode SQLite: a plain ``cp``/``rsync`` from under a live
writer copies a stale or torn database. This script uses SQLite's online
backup API, which folds the WAL into one consistent file per database. That
fixes per-file corruption — but ``repo_head`` must agree with the block store
(and the store garbage-collects on every commit), so the whole snapshot is
only coherent when nothing is writing. **Stop the peer first** — the same
single-writer discipline as ``post.py``.

``--exclude-secrets`` omits ``signing.key`` (and ``rotation.key``, which
should not exist on the box at all) so the archive can live on ordinary
storage; keep the pens in your password manager instead. If secrets are
included, encrypt the destination before it leaves the machine.

Usage:
  python backup.py --state ../bluesky_peer/state --dest /backups/alice-2026-07-19
  python backup.py --state ../bluesky_peer/state --dest ... --exclude-secrets
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

SECRET_FILES = ("signing.key", "rotation.key")
PLAIN_FILES = ("identity.json", "chain.json", "repo_head")
SQLITE_FILES = ("blocks.sqlite", "blobs.sqlite")


def copy_secret(src: Path, dst: Path) -> None:
    # create 0600 atomically (os.open mode), same rationale as the state dir
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, src.read_bytes())
    finally:
        os.close(fd)


def snapshot_sqlite(src: Path, dst: Path) -> None:
    src_db = sqlite3.connect(src)
    dst_db = sqlite3.connect(dst)
    try:
        src_db.backup(dst_db)  # folds the WAL in; dst is one consistent file
    finally:
        dst_db.close()
        src_db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", required=True, help="the state dir to snapshot")
    ap.add_argument("--dest", required=True, help="destination dir (created; must be empty)")
    ap.add_argument(
        "--exclude-secrets",
        action="store_true",
        help="omit signing.key/rotation.key (keep the pens in a password manager)",
    )
    args = ap.parse_args()

    state = Path(args.state)
    dest = Path(args.dest)
    if not (state / "identity.json").exists():
        sys.exit(f"{state} has no identity.json — not a minted state dir; nothing to back up")
    if dest.exists() and any(dest.iterdir()):
        sys.exit(f"{dest} already exists and is not empty — refusing to mix snapshots")
    dest.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for name in PLAIN_FILES:
        if (state / name).exists():
            shutil.copy2(state / name, dest / name)
            copied.append(name)
    for name in SQLITE_FILES:
        if (state / name).exists():
            snapshot_sqlite(state / name, dest / name)
            copied.append(f"{name} (sqlite snapshot)")
    if not args.exclude_secrets:
        for name in SECRET_FILES:
            if (state / name).exists():
                copy_secret(state / name, dest / name)
                copied.append(f"{name} (SECRET — encrypt this archive)")

    if (state / "rotation.key").exists():
        print(
            "WARNING: rotation.key exists in the state dir. The rotation pen "
            "should live in a password manager, not on the serving box.",
            file=sys.stderr,
        )

    print(f"snapshot: {dest}")
    for name in copied:
        print(f"  {name}")
    if args.exclude_secrets:
        print("secrets excluded — restore.py will need signing.key from your password manager")
    else:
        print("contains signing.key — encrypt before this leaves the machine (age/gpg)")


if __name__ == "__main__":
    main()
