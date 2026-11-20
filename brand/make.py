"""Everything the brand is drawn from, written out to where it is used.

python brand/make.py            say what is out of date, exit 1 if anything is
python brand/make.py --write    bring it all up to date

The sources are the conductor's theme, `tutorials/conductor/src/brand/tokens.css`
(every color, dark and light), and `brand.js` beside it (the goose and the
skein). Nothing else chooses a color or a shape; `brand/wordmark.json` holds
the letters of the name as outlines, for the lockup. From those this writes:

- `brand/lockup-*.svg`, `brand/mark-*.svg` and `brand/goose-*.svg`, the logo
  files, one per theme;
- `brand/favicon.svg` and the conductor's copy of it;
- the palette table in `brand/README.md`;
- each tutorial sketch's own ground and fallback colors, so a picture reads
  as an image on any page, light or dark, where it cannot see the theme.

It also holds the palette to its contrast rules. Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "brand"
THEME = ROOT / "tutorials" / "conductor" / "src" / "brand"
SKETCHES = ROOT / "tutorials" / "sketches"
PAGE_FAVICON = ROOT / "tutorials" / "conductor" / "public" / "favicon.svg"

GROUNDS = ("bg", "surface", "surface-raised", "terminal-bg")
# the least contrast each kind of ink must keep on every ground
NEED = {
    "text": 7.0,
    "text-soft": 7.0,
    "text-muted": 4.5,
    "accent-text": 4.5,
    "verified": 4.5,
    "sealed": 4.5,
    "plaintext": 4.5,
    "danger": 4.5,
}
MEANS = {
    "bg": "the page",
    "surface": "cards and panels",
    "surface-raised": "boxes on a card, buttons",
    "terminal-bg": "terminals and the ground under a sketch",
    "border": "lines between things",
    "text": "running text and headings",
    "text-soft": "secondary text",
    "text-muted": "labels and small print",
    "mark": "the goose's orange, the same on every page; never text",
    "primary": "the brand as a fill: the main button, borders and rings",
    "on-primary": "text on the brand color",
    "accent-text": "the brand as words: kickers, prompts, labels; deeper on a light page",
    "highlight": "what a key concept points at (the brand color, a ring)",
    "verified": "a signature checked out",
    "sealed": "inside WireGuard: sizes and timing only",
    "plaintext": "readable by anyone on the path",
    "danger": "refused, unreachable, failed",
}
BLOCK = re.compile(r"[ \t]*<!-- theme:start.*?<!-- theme:end -->\n", re.S)
VAR = re.compile(r"var\(--color-([a-z-]+)(?:,(?:[^()]|\([^()]*\))*)?\)")


def tokens() -> dict[str, dict[str, str]]:
    """The theme's colors, as {'dark': {name: value}, 'light': {...}}."""
    dark_css, light_css = (THEME / "tokens.css").read_text().split(":root[data-theme='light']")
    pair = re.compile(r"--color-([a-z-]+):\s*([^;]+);")
    dark = dict(pair.findall(dark_css))
    return {"dark": dark, "light": {**dark, **dict(pair.findall(light_css))}}


def shapes() -> tuple[str, list[list[float]], float]:
    """The goose's path, where the skein's birds sit, and how big they are."""
    js = (THEME / "brand.js").read_text()
    goose = re.search(r"export const goose =\s*'([^']+)'", js)
    skein = re.search(r"export const skein = (\[\[.*?\]\]);", js)
    scale = re.search(r"export const skeinScale = ([0-9.]+);", js)
    assert goose and skein and scale, "brand.js no longer exports goose, skein and skeinScale"
    return goose.group(1), json.loads(skein.group(1)), float(scale.group(1))


def luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def contrast_failures() -> list[str]:
    """Every ink on every ground, in both themes: the pairs that miss the bar."""
    found = []
    for theme, t in tokens().items():
        for ink, need in NEED.items():
            for ground in GROUNDS:
                got = contrast(t[ink], t[ground])
                if got < need:
                    found.append(f"{theme}: {ink} on {ground} is {got:.2f}, needs {need}")
        on = contrast(t["on-primary"], t["primary"])
        if on < 4.5:
            found.append(f"{theme}: on-primary on primary is {on:.2f}, needs 4.5")
    return found


def mark(theme: str) -> str:
    goose, skein, s = shapes()
    t = tokens()[theme]
    birds = "\n".join(
        f'  <path fill="{t["mark"] if i == 0 else t["text"]}" '
        f'transform="translate({x - 50 * s:g} {y - 49 * s:g}) scale({s:g})" d="{goose}" />'
        for i, (x, y) in enumerate(skein)
    )
    return (
        f"<!-- the FastATP mark for a {theme} page: five geese in a V, the lead in the"
        " mark's orange. Written by brand/make.py -->\n"
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 8 100 84" role="img" '
        f'aria-label="FastATP">\n{birds}\n</svg>\n'
    )


def lockup(theme: str) -> str:
    """The mark beside the name, the name as outlines so no font is needed.

    `wordmark.json` holds the letters of FastATP in Space Grotesk Bold, drawn
    once from the font with the caps standing between y=0 and its capHeight.
    """
    goose, skein, s = shapes()
    t = tokens()[theme]
    word = json.loads((HERE / "wordmark.json").read_text())
    k = 40 / word["capHeight"]  # the caps stand 40 tall beside a mark 84 tall
    width = 100 + 20 + word["width"] * k
    birds = "\n".join(
        f'  <path fill="{t["mark"] if i == 0 else t["text"]}" '
        f'transform="translate({x - 50 * s:g} {y - 8 - 49 * s:g}) scale({s:g})" d="{goose}" />'
        for i, (x, y) in enumerate(skein)
    )
    return (
        f"<!-- the FastATP mark and name for a {theme} page. Written by brand/make.py -->\n"
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} 84" role="img" '
        f'aria-label="FastATP">\n{birds}\n'
        f'  <g transform="translate(120 22) scale({k:.5f})">\n'
        f'    <path fill="{t["text"]}" d="{word["fast"]}" />\n'
        f'    <path fill="{t["mark"]}" d="{word["atp"]}" />\n'
        "  </g>\n</svg>\n"
    )


def one_goose(theme: str) -> str:
    goose, _, _ = shapes()
    return (
        f"<!-- one FastATP goose for a {theme} page, for where the mark would be too small."
        " Written by brand/make.py -->\n"
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 18 100 62" role="img" '
        f'aria-label="FastATP">\n  <path fill="{tokens()[theme]["mark"]}" d="{goose}" />\n'
        "</svg>\n"
    )


def favicon() -> str:
    goose, _, _ = shapes()
    return (
        "<!-- the FastATP goose as a tab icon, in the mark's orange on any tab."
        " Written by brand/make.py -->\n"
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">\n'
        f'  <path fill="{tokens()["dark"]["mark"]}" d="{goose}" />\n</svg>\n'
    )


def sketch(text: str) -> str:
    """A sketch with its own ground and its fallback colors taken from the theme.

    On the conductor's page the picture is part of the page and uses the
    theme's `--color-*`. As an image (GitHub, an editor's preview) it cannot
    see those, so each use falls back to `--fb-*`, which a style block in the
    picture sets for light and dark, and then to the light value itself.
    """
    t = tokens()
    text = BLOCK.sub("", text)

    def use(name: str) -> str:
        return f"var(--color-{name}, var(--fb-{name}, {t['light'][name]}))"

    text = VAR.sub(lambda m: use(m.group(1)), text)
    names = sorted({*VAR.findall(text), "terminal-bg"})
    box = re.search(r'viewBox="([^"]+)"', text)
    assert box, "a sketch needs a viewBox"
    x, y, w, h = (float(v) for v in box.group(1).split())

    def fallbacks(theme: str) -> str:
        return " ".join(f"--fb-{n}: {t[theme][n]};" for n in names)

    block = (
        "  <!-- theme:start (written by brand/make.py from tokens.css; do not edit) -->\n"
        "  <style>\n"
        f"    :root {{ {fallbacks('light')} }}\n"
        f"    @media (prefers-color-scheme: dark) {{ :root {{ {fallbacks('dark')} }} }}\n"
        "  </style>\n"
        f'  <rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="8" '
        f'fill="{use("terminal-bg")}" />\n'
        "  <!-- theme:end -->\n"
    )
    opening = re.search(r"<svg[^>]*>\n", text)
    assert opening, "a sketch opens with an <svg> tag on its own line"
    return text[: opening.end()] + block + text[opening.end() :]


def palette_table() -> str:
    t = tokens()
    rows = ["| Token | Dark | Light | What it is for |", "| --- | --- | --- | --- |"]
    rows += [
        f"| `--color-{n}` | `{t['dark'][n]}` | `{t['light'][n]}` | {MEANS[n]} |" for n in MEANS
    ]
    return "\n".join(rows)


def readme() -> str:
    text = (HERE / "README.md").read_text()
    start, end = "<!-- palette:start -->\n", "<!-- palette:end -->"
    assert start in text and end in text, "brand/README.md lost its palette markers"
    head, rest = text.split(start)
    return head + start + palette_table() + "\n" + end + rest.split(end)[1]


def outputs() -> dict[Path, str]:
    """Every file this owns, and what it should hold."""
    out = {
        HERE / "favicon.svg": favicon(),
        PAGE_FAVICON: favicon(),
        HERE / "README.md": readme(),
    }
    for theme in ("dark", "light"):
        out[HERE / f"lockup-{theme}.svg"] = lockup(theme)
        out[HERE / f"mark-{theme}.svg"] = mark(theme)
        out[HERE / f"goose-{theme}.svg"] = one_goose(theme)
    for path in sorted(SKETCHES.glob("*.svg")):
        out[path] = sketch(path.read_text())
    return out


def stale() -> list[str]:
    """The files that are not what the theme says they should be."""
    return [
        str(path.relative_to(ROOT))
        for path, text in outputs().items()
        if not path.exists() or path.read_text() != text
    ]


def main(argv: list[str]) -> int:
    bad = contrast_failures()
    if bad:
        print("the palette breaks its contrast rules:\n  " + "\n  ".join(bad))
        return 1
    if "--write" in argv:
        for path, text in outputs().items():
            if not path.exists() or path.read_text() != text:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                print("wrote", path.relative_to(ROOT))
        return 0
    old = stale()
    print("\n".join(f"out of date: {p}" for p in old) or "the brand is up to date")
    return 1 if old else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
