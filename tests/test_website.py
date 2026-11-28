"""fastatp.com builds from the repo's own markdown, and a link that leads nowhere stops it."""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

BUILD = Path(__file__).resolve().parent.parent / "website" / "build.py"

# The source archive leaves website/ out, so from an sdist there is nothing to build.
pytestmark = pytest.mark.skipif(not BUILD.exists(), reason="website/ is the repo's, not an sdist's")


def _site() -> ModuleType:
    if "website_build" in sys.modules:
        return sys.modules["website_build"]
    spec = importlib.util.spec_from_file_location("website_build", BUILD)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["website_build"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list]:
    """One build for the module: it raises on any link or anchor that leads nowhere."""
    out = tmp_path_factory.mktemp("site")
    return out, _site().build(out)


def test_every_page_is_written_with_its_markdown_twin(built: tuple[Path, list]) -> None:
    out, pages = built
    site = _site()
    assert {
        "/",
        "/readme/",
        "/philosophy/",
        "/tutorials/",
        "/api/",
        "/resources/",
        "/decisions/",
    } <= {p.url for p in pages}
    for page in pages:
        assert (out / page.url.lstrip("/") / "index.html").is_file(), page.url
        assert (out / site.twin_url(page.url).lstrip("/")).is_file(), page.url


def test_every_page_has_its_own_title_and_a_description(built: tuple[Path, list]) -> None:
    _, pages = built
    site = _site()
    titles = [site.page_title(p) for p in pages]
    assert len(titles) == len(set(titles))
    assert all(p.description for p in pages), [p.url for p in pages if not p.description]
    # words on either side of a line break in the source stay two words
    hosted = next(p for p in pages if p.url == "/resources/hosted-peers/")
    assert "will not run their own" in hosted.description


def test_what_crawlers_and_agents_read_is_there(built: tuple[Path, list]) -> None:
    out, pages = built
    for name in ("sitemap.xml", "robots.txt", "llms.txt", "404.html", "CNAME"):
        assert (out / name).is_file(), name
    sitemap = (out / "sitemap.xml").read_text()
    llms = (out / "llms.txt").read_text()
    site = _site()
    for page in pages:
        assert f"<loc>{site.SITE}{page.url}</loc>" in sitemap
        assert f"{site.SITE}{site.twin_url(page.url)}" in llms


def test_every_guide_in_the_series_has_a_page(built: tuple[Path, list]) -> None:
    """A guide added to tutorials/README.md's table is on the site with no site change."""
    out, _ = built
    site = _site()
    front = (out / "tutorials/index.html").read_text()
    written = [g for g in site.guides() if g.name]
    assert written
    for guide in written:
        url = f"/tutorials/{site.slug(guide.name)}/"
        assert (out / url.lstrip("/") / "index.html").is_file(), guide.name
        assert f'href="{url}"' in front, guide.name


def test_no_page_needs_javascript_and_no_cue_reaches_one(built: tuple[Path, list]) -> None:
    out, _ = built
    site = _site()
    explainer = out / site.EXPLAINER_URL.strip("/")
    for page in out.rglob("*.html"):
        if explainer in page.parents:
            continue  # the one page with something to press; build.py checks its article
        text = page.read_text()
        scripts = re.findall(r"<script\b[^>]*>", text)
        assert all("application/ld+json" in s for s in scripts), page
        assert "<!-- @demo" not in text, page


def test_a_link_that_leads_nowhere_is_an_error(tmp_path: Path) -> None:
    site = _site()
    link = site.Linker("README.md", site.source_pages(), tmp_path)
    assert link("PHILOSOPHY.md") == "/philosophy/"
    assert link("fastatp/__init__.py").startswith(site.GITHUB)
    assert not link.errors
    link("docs/NO_SUCH_PAGE.md")
    assert link.errors == ["README.md: docs/NO_SUCH_PAGE.md leads nowhere"]


def test_every_public_name_has_a_reference_page(built: tuple[Path, list]) -> None:
    """The API reference is `fastatp.__all__`, no more and no less."""
    import fastatp

    out, pages = built
    site = _site()
    reference = {p.title: p for p in pages if p.url.startswith("/api/") and p.url != "/api/"}
    assert set(reference) == set(fastatp.__all__)
    index = (out / "api/index.html").read_text()
    for name, page in reference.items():
        assert f'href="{page.url}"' in index, name
        assert page.origin and (site.ROOT / page.origin.partition("#")[0]).is_file(), name


def test_a_docstring_is_text_and_links_to_the_names_it_mentions(built: tuple[Path, list]) -> None:
    out, _ = built
    peer = (out / "api/peer/index.html").read_text()
    assert '<a href="/api/peerserver/"><code>PeerServer</code></a>' in peer
    assert 'id="verify"' in peer
    assert "`did` + `signing_key`" in (out / "api/peer.md").read_text()


def test_the_decisions_page_lists_every_record(built: tuple[Path, list]) -> None:
    out, _ = built
    site = _site()
    front = (out / "decisions/index.html").read_text()
    records = sorted((site.ROOT / "docs/decisions").glob("[0-9]*.md"))
    assert records
    for record in records:
        assert f'href="/decisions/{site.slug(record.stem)}/"' in front, record.name
