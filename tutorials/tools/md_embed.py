"""Keep a guide's embedded source files equal to the files themselves.

python md_embed.py --check GETTING_STARTED.md     exit 1 and say which block drifted
python md_embed.py --write GETTING_STARTED.md     replace each block with the file

A section headed ``### `name.py`: ...`` whose first fenced ```python block
quotes ``lab/name.py`` is that file, shown in full. The guide is read as
prose; the lab is what runs. This keeps them one thing, so a change to the
code is a change to the page, and `uv run pytest` fails when they differ.

Standard library only.
"""

import re
import sys
from pathlib import Path

HEADING = re.compile(r"^### `([\w.]+)`")


def embedded_blocks(text: str) -> list[tuple[str, int, int]]:
    """(file name, first body line, line after the body) for each embedded file."""
    lines = text.splitlines()
    found = []
    i = 0
    while i < len(lines):
        m = HEADING.match(lines[i])
        if m:
            name = m.group(1)
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                if HEADING.match(lines[j]) or lines[j].startswith("## "):
                    break
                j += 1
            if j < len(lines) and lines[j].startswith("```python"):
                k = j + 1
                while k < len(lines) and not lines[k].startswith("```"):
                    k += 1
                found.append((name, j + 1, k))
                i = k
        i += 1
    return found


def sync(guide: Path, lab: Path, write: bool) -> list[str]:
    text = guide.read_text(encoding="utf-8")
    lines = text.splitlines()
    drifted = []
    for name, start, end in reversed(embedded_blocks(text)):
        source = lab / name
        if not source.exists():
            continue
        wanted = source.read_text(encoding="utf-8").rstrip("\n").splitlines()
        if lines[start:end] != wanted:
            drifted.append(name)
            if write:
                lines[start:end] = wanted
    if write and drifted:
        guide.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return drifted


if __name__ == "__main__":
    mode, target = sys.argv[1], Path(sys.argv[2])
    drifted = sync(target, target.parent / "lab", write=(mode == "--write"))
    if mode == "--check" and drifted:
        print(
            f"{target.name}: embedded files differ from lab/: {', '.join(drifted)}", file=sys.stderr
        )
        sys.exit(1)
    print(
        f"{target.name}: {'updated' if mode == '--write' else 'in sync'}: {', '.join(drifted) or 'nothing changed'}"
    )
