#!/usr/bin/env python3
"""Check a release tag against the package version, and stamp rc builds.

Run by release.yml before anything is built:

    python3 scripts/release_tag_version.py "$GITHUB_REF_NAME"

``pyproject.toml`` on a release commit always carries the exact release
version, ``X.Y.Z``. Two tags are accepted for that commit:

- ``vX.Y.Z`` builds and publishes ``X.Y.Z`` (PyPI).
- ``vX.Y.ZrcN`` builds the same commit as ``X.Y.ZrcN`` (TestPyPI) by
  stamping the rc version into ``pyproject.toml`` and ``uv.lock`` for this
  build only, so ``uv sync --locked`` still passes and every artifact,
  including the wasm bundle's VERSION file, says ``X.Y.ZrcN``. Nothing is
  committed.

Anything else is refused, in particular a dev version (``X.Y.Z.devN``, what
``main`` carries between releases): dev builds never publish.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RELEASE = re.compile(r"\d+\.\d+\.\d+")
PYPROJECT_VERSION = re.compile(r'^version = "([^"]+)"$', re.M)


def _stamp(path: Path, pattern: str, old: str, new: str) -> None:
    text = path.read_text()
    stamped, count = re.subn(
        pattern, lambda m: m.group(0).replace(f'"{old}"', f'"{new}"'), text, flags=re.M
    )
    if count != 1:
        raise SystemExit(f"release-tag: expected one version line in {path}, found {count}")
    path.write_text(stamped)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].startswith("v"):
        print("usage: release_tag_version.py vX.Y.Z | vX.Y.ZrcN", file=sys.stderr)
        return 2
    tag = argv[1][1:]
    root = Path(__file__).resolve().parent.parent
    pyproject = root / "pyproject.toml"
    found = PYPROJECT_VERSION.findall(pyproject.read_text())
    if len(found) != 1:
        raise SystemExit(f"release-tag: expected one version line in pyproject.toml, found {found}")
    version = found[0]
    print(f"pyproject version: {version}   tag: v{tag}")

    if not RELEASE.fullmatch(version):
        raise SystemExit(
            f"release-tag: refusing: pyproject version {version} is not a release version "
            "(X.Y.Z). Dev versions never publish; tag a release PR's merge commit."
        )
    if tag == version:
        return 0
    rc = re.fullmatch(rf"{re.escape(version)}rc(\d+)", tag)
    if rc is None:
        raise SystemExit(f"release-tag: refusing: tag must be v{version} or v{version}rcN")

    _stamp(pyproject, r'^version = "[^"]+"$', version, tag)
    _stamp(
        root / "uv.lock",
        r'\[\[package\]\]\nname = "fastatp"\nversion = "[^"]+"\nsource = \{ editable = "\." \}',
        version,
        tag,
    )
    print(f"release-tag: building this commit as {tag} (stamped into pyproject.toml and uv.lock)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
