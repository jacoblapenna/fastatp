"""Render a guide's pictures the way the page shows them, to look at.

python sketch_render.py OUT_DIR sketches/*.svg     one PNG per picture, dark theme

The text-fit check (sketch_check.py) catches a line past the edge; whether
two things cross, or a label sits where it should, only a look can tell.
This wraps each SVG in the page's theme tokens and screenshots it with a
headless Chrome, if one is installed. Standard library only.
"""

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
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tokens = TOKENS.read_text()
    for path in paths:
        svg = Path(path)
        page = out / f"{svg.stem}.html"
        page.write_text(
            f'<!doctype html><html data-theme="dark"><head><meta charset="utf-8"><style>{tokens}'
            "body{margin:0;background:var(--color-terminal-bg);padding:10px;width:620px}"
            "svg{width:620px;height:auto;display:block}</style></head>"
            f"<body>{svg.read_text()}</body></html>"
        )
        subprocess.run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--hide-scrollbars",
                "--window-size=640,460",
                f"--screenshot={out / svg.stem}.png",
                page.as_uri(),
            ],
            check=False,
            capture_output=True,
        )
        page.unlink()
        print(out / f"{svg.stem}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
