"""Build fastatp.com from the repo's own markdown, read where it lives.

    python website/build.py            # writes website/_build/
    python website/build.py --serve    # builds, then serves it on :8000

Every page is a markdown file somewhere in the repo; nothing is copied into
a docs folder. Links are rewritten so a link to another page goes to that
page, a link to code goes to GitHub, and a picture is copied beside the page
at its repo path. A link that leads nowhere fails the build.
"""

from __future__ import annotations

import argparse
import html
import http.server
import json
import posixpath
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_api  # beside this file, not an installed package

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
SITE = "https://fastatp.com"
GITHUB = "https://github.com/jacoblapenna/fastatp"
GITHUB_FILE = (f"{GITHUB}/blob/main/", f"{GITHUB}/tree/main/")
RAW = "https://raw.githubusercontent.com/jacoblapenna/fastatp/main/"
BRAND_SRC = ROOT / "tutorials/conductor/src/brand"
BRAND_FILES = ("favicon.svg", "lockup-dark.svg", "lockup-light.svg")

PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]

# Top-level files and where they live on the site. docs/ and its ADRs are
# found by globbing, so a new doc becomes a page without touching this.
FIXED = {
    "README.md": "/readme/",
    "PHILOSOPHY.md": "/philosophy/",
    "CHANGELOG.md": "/changelog/",
    "CONTRIBUTING.md": "/contributing/",
    "SECURITY.md": "/security/",
    "CODE_OF_CONDUCT.md": "/code-of-conduct/",
}

NAV = (
    ("Tutorials", "/tutorials/"),
    ("README", "/readme/"),
    ("Philosophy", "/philosophy/"),
    ("API", "/api/"),
    ("Resources", "/resources/"),
    ("Decisions", "/decisions/"),
    ("Changelog", "/changelog/"),
)


@dataclass
class Page:
    url: str
    title: str
    description: str
    markdown: str  # the twin, served beside the page
    src: str | None = None  # repo path, None for a generated page
    origin: str | None = None  # for an API page: the source file and line it documents
    generated: bool = False  # written by the build, not a file in the repo
    body: str = ""
    ids: set[str] = field(default_factory=set)


NOT_FOUND = """<h1>Not found</h1>
<p>There is no page at this address. <a href="/">Start at the home page</a>.</p>
"""


class BuildError(Exception):
    pass


def slug(stem: str) -> str:
    return stem.lower().replace("_", "-")


def twin_url(url: str) -> str:
    return "/index.md" if url == "/" else url.rstrip("/") + ".md"


def source_pages() -> dict[str, str]:
    """Repo path -> site URL for every markdown file that is a page."""
    found = {path: url for path, url in FIXED.items() if (ROOT / path).exists()}
    for p in sorted((ROOT / "docs").glob("*.md")):
        found[f"docs/{p.name}"] = f"/resources/{slug(p.stem)}/"
    for p in sorted((ROOT / "docs/decisions").glob("*.md")):
        name = "" if p.stem == "README" else f"{slug(p.stem)}/"
        found[f"docs/decisions/{p.name}"] = f"/decisions/{name}"
    for p in sorted((ROOT / "tutorials").glob("*.md")):
        found[f"tutorials/{p.name}"] = f"/tutorials/{slug(p.stem)}/"
    # Pages the site writes itself, such as the tutorials' front page.
    for p in sorted((HERE / "pages").glob("*.md")):
        url = "/" if p.stem == "index" else f"/{slug(p.stem)}/"
        found[f"website/pages/{p.name}"] = url
    return found


# A row of the guide table in tutorials/README.md: | 3 | [HOOT.md](HOOT.md) | what | runs |
GUIDE_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(\w+)\s*\|\s*$")
GUIDE_LINK = re.compile(r"\[([A-Z_]+)\.md\]\(\1\.md\)")


@dataclass
class Guide:
    number: str
    name: str  # what lab.sh calls it, like HOOT; empty for one not written yet
    title: str
    shows: str


def guides() -> list[Guide]:
    """The series, in order, from the table in tutorials/README.md."""
    found = []
    for line in (ROOT / "tutorials/README.md").read_text().splitlines():
        row = GUIDE_ROW.match(line)
        if not row:
            continue
        number, cell, shows, _status = row.groups()
        link = GUIDE_LINK.search(cell)
        name = link.group(1) if link else ""
        if name:
            first = (ROOT / f"tutorials/{name}.md").read_text().splitlines()[0]
            title = first.removeprefix("# ")
        else:
            title = cell
        found.append(Guide(number, name, title, shows))
    if not found:
        raise BuildError("tutorials/README.md has no guide table")
    return found


def guide_list(series: list[Guide]) -> str:
    rows = []
    for g in series:
        if g.name:
            rows.append(
                f"{g.number}. [{g.title}](../../tutorials/{g.name}.md): {g.shows}.\n"
                f"   Watch it with `./lab.sh conductor {g.name}`."
            )
        else:
            rows.append(f"{g.number}. {g.title}, coming next: {g.shows}.")
    return "\n".join(rows)


def watch_note(name: str) -> str:
    return (
        '<aside class="note"><p>Watch this guide played on your own machine: clone '
        f"the repo, then run <code>./lab.sh conductor {name}</code> in "
        '<code>tutorials/lab</code>. <a href="/tutorials/#watch-one">How</a>.</p></aside>\n'
    )


def github_slug(text: str, seen: dict[str, int]) -> str:
    """The id GitHub gives a heading, so `#anchor` links keep working."""
    base = re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")
    n = seen.get(base, 0)
    seen[base] = n + 1
    return base if n == 0 else f"{base}-{n}"


def plain(inline: Token) -> str:
    out = []
    for child in inline.children or []:
        if child.type in ("text", "code_inline", "image"):
            out.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            out.append(" ")
    return "".join(out)


def clip(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


class Linker:
    """Rewrites one page's links and pictures, and remembers what to check."""

    def __init__(self, src: str, pages: dict[str, str], out: Path) -> None:
        self.src = src
        self.pages = pages
        self.out = out
        self.anchors: list[tuple[str, str]] = []  # (page url, id) to check
        self.errors: list[str] = []

    def __call__(self, href: str, picture: bool = False) -> str:
        if href.startswith("#"):
            self.anchors.append((self.pages[self.src], href[1:]))
            return href
        path = self.repo_path(href)
        if path is None:
            return href  # somewhere else on the web
        path, _, frag = path.partition("#")
        path = path.rstrip("/")
        target = ROOT / path
        if path not in self.pages and not target.exists():
            self.errors.append(f"{self.src}: {href} leads nowhere")
            return href
        page = self.pages.get(path) or self.pages.get(f"{path}/README.md")
        if page and not picture:
            if frag:
                self.anchors.append((page, frag))
            return page + (f"#{frag}" if frag else "")
        if picture:
            dest = self.out / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, dest)
            return f"/{path}"
        kind = "tree" if target.is_dir() else "blob"
        return f"{GITHUB}/{kind}/main/{path}" + (f"#{frag}" if frag else "")

    def repo_path(self, href: str) -> str | None:
        for prefix in (*GITHUB_FILE, RAW):
            if href.startswith(prefix):
                return href[len(prefix) :]
        if re.match(r"^[a-z][a-z0-9+.-]*:", href) or href.startswith("//"):
            return None
        path, _, frag = href.partition("#")
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(self.src), path))
        if joined.startswith(".."):
            self.errors.append(f"{self.src}: {href} climbs out of the repo")
        return joined + (f"#{frag}" if frag else "")


DEMO_CUE = re.compile(r"<!--\s*@demo\b.*?-->\n?", re.S)
ATTR = re.compile(r'\b(href|src|srcset)="([^"]*)"')
HTML_ID = re.compile(r'\bid="([^"]*)"')


def rewrite_html(chunk: str, link: Linker) -> str:
    def sub(m: re.Match[str]) -> str:
        name, value = m.group(1), m.group(2)
        return f'{name}="{link(html.unescape(value), picture=name != "href")}"'

    return ATTR.sub(sub, chunk)


def render(page: Page, pages: dict[str, str], out: Path) -> Linker:
    # a docstring is text, never markup: `<did>` in one must show, not vanish
    md = MarkdownIt("commonmark", {"html": not page.generated})
    md.enable(["table", "strikethrough"])
    assert page.src is not None
    link = Linker(page.src, pages, out)
    tokens = md.parse(page.markdown)
    seen: dict[str, int] = {}
    title = description = ""
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            text = plain(tokens[i + 1])
            ident = github_slug(text, seen)
            tok.attrSet("id", ident)
            page.ids.add(ident)
            if tok.tag == "h1" and not title:
                title = text
        elif tok.type == "html_block":
            # the conductor's cues are for the show, not the page
            tok.content = rewrite_html(DEMO_CUE.sub("", tok.content), link)
            page.ids.update(HTML_ID.findall(tok.content))
            if not title and "<h1" in tok.content:
                title = re.sub(r"<[^>]+>", " ", tok.content).strip()
                alt = re.search(r'alt="([^"]*)"', tok.content)
                title = title or (alt.group(1) if alt else "")
        elif tok.type == "inline":
            if not description and tokens[i - 1].type == "paragraph_open" and title:
                description = plain(tok)
            for child in tok.children or []:
                if child.type == "link_open":
                    child.attrSet("href", link(str(child.attrGet("href"))))
                elif child.type == "image":
                    child.attrSet("src", link(str(child.attrGet("src")), picture=True))
                elif child.type == "html_inline":
                    child.content = rewrite_html(DEMO_CUE.sub("", child.content), link)
                    page.ids.update(HTML_ID.findall(child.content))
    page.body = md.renderer.render(tokens, md.options, {})
    page.title = page.title or title or page.src
    page.description = page.description or clip(description)
    return link


# The resources' front page has no file: it lists docs/, so a note added
# there is on the site with no site change. Pages link to it by this path.
RESOURCES = "website/resources/README.md"


# The explainers are a Svelte page built by npm (website/explainers); the
# site carries what that build left in dist/, at this address.
EXPLAINER_DIST = HERE / "explainers" / "dist"
EXPLAINER_URL = "/resources/merkle-trees/"


@dataclass
class Explainer:
    url: str
    title: str
    description: str


def explainers(out: Path, required: bool) -> list[Explainer]:
    """Copy the built explainer into the site, if `npm run build` has made one."""
    index = EXPLAINER_DIST / "index.html"
    if not index.is_file():
        if required:
            raise BuildError(
                "no built explainer: run `npm ci && npm run build` in website/explainers"
            )
        return []
    text = index.read_text()
    title = re.search(r"<title>(.*?)</title>", text, re.S)
    described = re.search(r'<meta name="description" content="([^"]*)"', text)
    if not title or not described:
        raise BuildError("the explainer's index.html needs a <title> and a description")
    if "<article" not in text or "<h1" not in text:
        # prerender.js writes the article into the page; without it the page
        # is blank until its script runs, and blank to a crawler
        raise BuildError("the explainer was built without its article: use `npm run build`")
    shutil.copytree(EXPLAINER_DIST, out / EXPLAINER_URL.strip("/"))
    name = html.unescape(title.group(1)).split("|")[0].strip()
    return [Explainer(EXPLAINER_URL, name, html.unescape(described.group(1)))]


def resources_index(pages: list[Page], live: list[Explainer]) -> Page:
    notes = [p for p in pages if p.url.startswith("/resources/")]
    lines = ["# Resources", ""]
    lines += ["Background on FastATP and atproto: how it is designed, and how it"]
    lines += ["compares with the tools nearest to it.", ""]
    if live:
        lines += ["## Explainers", "", "One idea each, with something to press.", ""]
        lines += [f"- [{e.title}]({e.url}): {e.description}" for e in live]
        lines += ["", "## Design notes", ""]
    lines += [f"- [{p.title}]({p.url}): {p.description}" for p in notes]
    text = "\n".join(lines) + "\n"
    body = MarkdownIt("commonmark").render(text)
    return Page(
        "/resources/",
        "Resources",
        "Design notes on FastATP and atproto, and comparisons with nearby tools.",
        text,
        body=body,
    )


def api_pages(where: dict[str, str]) -> list[Page]:
    """The reference pages, added to `where` so every page can link to them."""
    reference, index = site_api.pages(ROOT)
    made = [
        Page(
            "/api/",
            "API reference",
            "Every name fastatp exports, with its signature and docstring.",
            index,
            src=f"{site_api.FOLDER}/README.md",
            generated=True,
        )
    ]
    for ref in reference:
        made.append(
            Page(
                f"/api/{slug(ref.name)}/",
                ref.name,
                clip(ref.summary) or f"{ref.name}, in the FastATP API reference.",
                ref.markdown,
                src=ref.src,
                origin=ref.origin,
                generated=True,
            )
        )
    urls = [p.url for p in made]
    if len(urls) != len(set(urls)):
        raise BuildError("two public names share an API page address")
    where.update({p.src: p.url for p in made if p.src})
    return made


def page_title(page: Page) -> str:
    if page.url == "/":
        return "FastATP: signed AT Protocol (atproto) records for Python peers"
    return f"{page.title} · FastATP"


def json_ld() -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "SoftwareSourceCode",
        "name": "FastATP",
        "description": PROJECT["description"],
        "url": SITE,
        "codeRepository": GITHUB,
        "programmingLanguage": "Python",
        "license": "https://spdx.org/licenses/MIT.html",
        "version": PROJECT["version"],
    }
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


def layout(page: Page) -> str:
    esc = html.escape
    nav = "".join(
        f'<a href="{url}"{" aria-current=page" if page.url.startswith(url) else ""}>{name}</a>'
        for name, url in NAV
    )
    origin = page.origin if page.generated else page.src
    source = f'<a href="{GITHUB}/blob/main/{origin}">Source on GitHub</a> · ' if origin else ""
    addresses = footer = ""
    if page.url != "/404/":
        addresses = (
            f'<link rel="canonical" href="{SITE}{page.url}">\n'
            f'<link rel="alternate" type="text/markdown" href="{twin_url(page.url)}">\n'
        )
        footer = f'{source}<a href="{twin_url(page.url)}">This page as markdown</a>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(page_title(page))}</title>
<meta name="description" content="{esc(page.description)}">
{addresses}<link rel="icon" href="/brand/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/tokens.css">
<link rel="stylesheet" href="/site.css">
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(page_title(page))}">
<meta property="og:description" content="{esc(page.description)}">
<meta property="og:url" content="{SITE}{page.url}">
{json_ld() if page.url == "/" else ""}
</head>
<body>
<header class="top">
<a class="home" href="/"><img src="/brand/favicon.svg" alt="" width="28" height="28">FastATP</a>
<nav>{nav}<a href="{GITHUB}">GitHub</a></nav>
</header>
<main class="page">
<article>
{page.body}</article>
<footer>{footer}</footer>
</main>
</body>
</html>
"""


def themed_tokens() -> str:
    """The conductor's tokens, plus the light theme for a reader whose
    system asks for it: the site follows the system with no JavaScript."""
    css = (BRAND_SRC / "tokens.css").read_text()
    light = re.search(r":root\[data-theme='light'\]\s*\{(.*?)\n\}", css, re.S)
    if not light:
        raise BuildError("tokens.css has no light theme block")
    return (
        css
        + "\n/* Written by website/build.py: the light theme when the system asks. */\n"
        + "@media (prefers-color-scheme: light) {\n  :root:not([data-theme='dark']) {"
        + light.group(1)
        + "\n  }\n}\n"
    )


def last_changed(src: str) -> str | None:
    done = subprocess.run(
        ["git", "log", "-1", "--format=%cs", "--", src],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip() or None


def sitemap(pages: list[Page], live: list[Explainer]) -> str:
    rows = [f"  <url><loc>{SITE}{e.url}</loc></url>" for e in live]
    for p in pages:
        origin = p.origin.partition("#")[0] if p.origin else None
        dated = origin if p.generated else p.src
        mod = last_changed(dated) if dated else None
        lastmod = f"<lastmod>{mod}</lastmod>" if mod else ""
        rows.append(f"  <url><loc>{SITE}{p.url}</loc>{lastmod}</url>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(rows)
        + "\n</urlset>\n"
    )


def llms_txt(pages: list[Page]) -> str:
    def row(p: Page) -> str:
        return f"- [{p.title}]({SITE}{twin_url(p.url)}): {p.description}"

    tutorials = [p for p in pages if p.url.startswith("/tutorials/")]
    main = [p for p in pages if p.url in ("/", "/readme/", "/philosophy/")]
    resources = [p for p in pages if p.url.startswith("/resources/")]
    decisions = [p for p in pages if p.url.startswith("/decisions/")]
    reference = [p for p in pages if p.url.startswith("/api/")]
    listed = main + decisions + tutorials + reference + resources
    rest = [p for p in pages if p not in listed]
    return "\n".join(
        [
            "# FastATP",
            "",
            f"> {PROJECT['description']}",
            "",
            "Each page on this site has a markdown copy: replace the trailing",
            "slash of its address with `.md` (the home page is `/index.md`).",
            f"The source is {GITHUB}.",
            "",
            "## Tutorials",
            "",
            *map(row, tutorials),
            "",
            "## Start here",
            "",
            *map(row, main),
            "",
            "## API reference",
            "",
            *map(row, reference),
            "",
            "## Resources",
            "",
            *map(row, resources),
            "",
            "## Decision records",
            "",
            *map(row, decisions),
            "",
            "## Optional",
            "",
            *map(row, rest),
            "",
        ]
    )


def write(out: Path, url_path: str, text: str) -> None:
    dest = out / url_path.lstrip("/")
    if url_path.endswith("/"):
        dest = dest / "index.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)


def decision_list() -> str:
    """The records themselves, listed under the decisions page's own words."""
    rows = []
    for p in sorted((ROOT / "docs/decisions").glob("[0-9]*.md")):
        title = p.read_text().splitlines()[0].removeprefix("# ")
        rows.append(f"- [{title}]({p.name})")
    return "\n## The records\n\n" + "\n".join(rows) + "\n"


def build(out: Path, require_explainers: bool = False) -> list[Page]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    where = source_pages()
    pages = [Page(url, "", "", (ROOT / src).read_text(), src=src) for src, url in where.items()]
    landing = next(p for p in pages if p.url == "/")
    landing.title, landing.description = "FastATP", PROJECT["description"]
    next(p for p in pages if p.url == "/readme/").title = "README"
    series = guides()
    front = next(p for p in pages if p.url == "/tutorials/")
    if "<!-- guides -->" not in front.markdown:
        raise BuildError("website/pages/tutorials.md has no <!-- guides --> marker")
    front.markdown = front.markdown.replace("<!-- guides -->", guide_list(series))
    watched = {f"tutorials/{g.name}.md": g.name for g in series if g.name}
    next(p for p in pages if p.url == "/decisions/").markdown += decision_list()
    pages += api_pages(where)
    where[RESOURCES] = "/resources/"

    errors: list[str] = []
    anchors: list[tuple[str, str]] = []
    for p in pages:
        link = render(p, where, out)
        errors += link.errors
        anchors += link.anchors
        # The tutorials are what the site leads with: each guide says how to
        # watch it.
        if p.src in watched:
            p.body = p.body.replace("</h1>\n", "</h1>\n" + watch_note(watched[p.src]), 1)
    live = explainers(out, require_explainers)
    pages.insert(1, resources_index(pages, live))

    ids = {p.url: p.ids for p in pages}
    errors += [
        f"#{frag} is not a heading on {url}"
        for url, frag in anchors
        if frag not in ids.get(url, set())
    ]
    if errors:
        raise BuildError("\n".join(sorted(set(errors))))

    for p in pages:
        write(out, p.url, layout(p))
        write(out, twin_url(p.url), p.markdown)
    not_found = Page("/404/", "Not found", "There is no page here.", "", body=NOT_FOUND)
    (out / "404.html").write_text(layout(not_found))

    brand = out / "brand"
    brand.mkdir(exist_ok=True)
    for name in BRAND_FILES:
        shutil.copyfile(ROOT / "brand" / name, brand / name)
    (out / "tokens.css").write_text(themed_tokens())
    (out / "site.css").write_text(
        (BRAND_SRC / "base.css").read_text() + (HERE / "site.css").read_text()
    )
    (out / "sitemap.xml").write_text(sitemap(pages, live))
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {SITE}/sitemap.xml\n")
    (out / "llms.txt").write_text(llms_txt(pages))
    (out / "CNAME").write_text("fastatp.com\n")
    (out / ".nojekyll").write_text("")
    return pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=HERE / "_build")
    parser.add_argument("--serve", action="store_true", help="serve the build on :8000")
    parser.add_argument(
        "--require-explainers",
        action="store_true",
        help="fail when website/explainers has not been built (the deploy passes this)",
    )
    args = parser.parse_args(argv)
    try:
        pages = build(args.out, args.require_explainers)
    except BuildError as e:
        print(f"build failed:\n{e}", file=sys.stderr)
        return 1
    print(f"built {len(pages)} pages into {args.out}")
    if args.serve:
        handler: Callable[..., http.server.SimpleHTTPRequestHandler] = partial(
            http.server.SimpleHTTPRequestHandler, directory=str(args.out)
        )
        print("serving on http://localhost:8000 (Ctrl-C stops it)")
        http.server.ThreadingHTTPServer(("127.0.0.1", 8000), handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
