# Website

fastatp.com, built from the repo's own markdown where it lives: no copies,
no docs folder.

```bash
uv run python website/build.py           # writes website/_build/
uv run python website/build.py --serve   # and serves it on :8000
```

- `build.py` finds the pages (`FIXED`, plus every `docs/*.md` under
  `/resources/`, every ADR and every guide),
  renders them with markdown-it-py, and rewrites links: to another page,
  to that page; to code, to GitHub; a picture, copied beside the page at
  its repo path. A link or `#anchor` that leads nowhere fails the build.
- Every page has its markdown twin beside it (`/philosophy/` and
  `/philosophy.md`), listed in `llms.txt`.
- The look is the conductor's brand: `tokens.css` and `base.css` are read
  from `tutorials/conductor/src/brand/`, never copied into this folder.
  `site.css` is the reading column. The light theme follows the reader's
  system, with no JavaScript.
- The output also carries `sitemap.xml` (dated from git), `robots.txt`,
  `CNAME`, `.nojekyll` and a `404.html`, ready for GitHub Pages.
- `tests/test_website.py` builds the site in the test suite, so a broken
  link fails a pull request. `.github/workflows/site.yml` publishes the
  build to GitHub Pages on every merge to `main`.
- `explainers/` is a Svelte page built with npm (its README says how).
  `build.py` carries its `dist/` to `/resources/merkle-trees/` when it has
  been built, and `--require-explainers` fails the build when it has not.
- The Decisions page lists every record in `docs/decisions/` under the
  README's own words.
- `site_api.py` writes the API reference: griffe reads each name in
  `fastatp.__all__` from the source, and the page is markdown that
  `build.py` renders like any other. A name in backticks in a docstring
  becomes a link when the site has that page.
