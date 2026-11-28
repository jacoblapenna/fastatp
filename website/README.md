# Website

fastatp.com, built from the repo's own markdown where it lives: no copies,
no docs folder. Not tracked yet (in `.git/info/exclude`); see `todo/0018`.

```bash
uv run python website/build.py           # writes website/_build/
uv run python website/build.py --serve   # and serves it on :8000
```

- `build.py` finds the pages (`FIXED`, plus every `docs/*.md` and ADR),
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
