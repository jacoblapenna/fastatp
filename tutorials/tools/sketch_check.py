"""Say which of a guide's pictures would not fit: text that runs past the edge.

python sketch_check.py sketches/*.svg       exit 1 and name each overrun

A sketch is a small SVG; the browser lays its text out, and nothing checks
that a line fits until someone looks. This estimates each text's width from
its font (monospace glyphs are about 0.62 em wide, sans about 0.5 em, bold
sans 0.55) and reports any that leaves the viewBox. It cannot see two texts
cross or a text cross a box; looking at the rendered picture is still the
last word. Standard library only.
"""

import re
import sys
import xml.etree.ElementTree as ET

SVG = "{http://www.w3.org/2000/svg}"


def width(text: str, font_size: float, family: str, weight: str) -> float:
    per = 0.62 if "monospace" in family else 0.55 if weight in ("600", "700", "bold") else 0.5
    return len(text) * font_size * per


def overruns(path: str) -> list[str]:
    root = ET.parse(path).getroot()
    box = [float(v) for v in root.get("viewBox", "0 0 0 0").split()]
    left, right = box[0] + 2, box[0] + box[2] - 2
    found = []
    for el in root.iter(f"{SVG}text"):
        text = "".join(el.itertext()).strip()
        if not text:
            continue
        size = float(re.sub(r"[a-z]+$", "", el.get("font-size", "16")))
        w = width(text, size, el.get("font-family", ""), el.get("font-weight", ""))
        x = float(el.get("x", "0"))
        anchor = el.get("text-anchor", "start")
        start = x - w / 2 if anchor == "middle" else x - w if anchor == "end" else x
        if start < left or start + w > right:
            found.append(
                f"{path}: {text[:48]!r} spans {start:.0f}..{start + w:.0f} of {box[2]:.0f}"
            )
    return found


if __name__ == "__main__":
    bad = [line for p in sys.argv[1:] for line in overruns(p)]
    print("\n".join(bad) or "every text fits")
    sys.exit(1 if bad else 0)
