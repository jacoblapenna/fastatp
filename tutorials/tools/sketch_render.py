"""Render a guide's pictures the way the page shows them, to look at.

python sketch_render.py OUT_DIR sketches/*.svg     one PNG per picture, dark theme

The text-fit check (sketch_check.py) catches a line past the edge; whether
two things cross, or a label sits where it should, only a look can tell.
This wraps each SVG in the page's theme tokens and screenshots it with a
headless Chrome, if one is installed. Standard library only.
"""

import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOKENS = Path(__file__).resolve().parent.parent / "conductor" / "src" / "brand" / "tokens.css"


def main(out_dir: str, paths: list[str]) -> int:
    chrome = next(
        (c for c in ("google-chrome", "chromium", "chromium-browser") if shutil.which(c)), None
    )
    if chrome is None:
        print("no headless Chrome found; nothing rendered")
        return 1
    out = Path(out_dir).resolve()  # a file:// address needs the whole path
    out.mkdir(parents=True, exist_ok=True)
    tokens = TOKENS.read_text()
    failed = 0
    for path in paths:
        svg = Path(path)
        # as tall as the picture is at 620 wide, so none is cut off at the bottom
        box = re.search(r'viewBox="([^"]+)"', svg.read_text())
        _, _, w, h = (float(v) for v in box.group(1).split()) if box else (0, 0, 620, 440)
        height = math.ceil(620 * h / w) + 20
        png = out / f"{svg.stem}.png"
        png.unlink(missing_ok=True)
        page = out / f"{svg.stem}.html"
        page.write_text(
            f'<!doctype html><html data-theme="dark"><head><meta charset="utf-8"><style>{tokens}'
            "body{margin:0;background:var(--color-terminal-bg);padding:10px;width:620px}"
            "svg{width:620px;height:auto;display:block}</style></head>"
            f"<body>{svg.read_text()}</body></html>"
        )
        done = subprocess.run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--hide-scrollbars",
                f"--window-size=640,{height}",
                f"--screenshot={png}",
                page.as_uri(),
            ],
            check=False,
            capture_output=True,
        )
        page.unlink()
        if done.returncode != 0 or not png.exists():
            failed += 1
            print(
                f"{svg.name}: not rendered ({done.stderr.decode(errors='replace').strip()[-200:]})"
            )
            continue
        print(png)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
