# Explainers

Explainers for fastatp.com: one idea each, with something to press. They
are the pages the docs and tutorials link to when a reader needs the "why"
behind something a guide only shows.

`website/build.py` carries what `npm run build` leaves in `dist/` to
`/resources/merkle-trees/` on the site and lists it on the Resources page.
Without a build the site is made without it; the site workflow builds it
and passes `--require-explainers`.

## Run it

```bash
cd website/explainers
npm ci           # the same pinned Svelte and Vite as tutorials/conductor
npm run dev      # the page, with hot reload
npm test         # the tree logic, checked against node's own SHA-256
npm run build    # a static page in dist/, with the article written into it
```

Add `?theme=light` or `?theme=dark` to the address to force a theme.

## How it is put together

- **The brand is not copied.** `vite.config.js` points `$brand` and
  `$theme` at `tutorials/conductor/src`, so the tokens, base styles, the
  goose and the theme switch are the conductor's own files. A component
  here never names a raw color.
- **`src/page.css`** is the reading column and the pieces every resource
  shares: the kicker, the table, the "lab" box with its buttons, the tally
  bars.
- **`src/lib/`** is logic with no page in it, tested with `node --test`.
- **The article is in the page as built.** `prerender.js` renders the
  page to HTML and writes it into `dist/index.html`, so it reads with no
  JavaScript and a crawler sees the text. The live pieces start in the
  browser (`live` in `MerkleTrees.svelte`).
- **One folder per resource** under `src/`. `App.svelte` shows the only one
  for now; a second resource makes it a list and a route.

## What is here

| Resource | Folder | What it explains |
| --- | --- | --- |
| The repo's tree | `src/merkle-trees/` | Why a repo's records keep their order while the tree's shape differs on every run, and what finding one record costs |

`src/lib/mst.js` is the atproto tree rule in JavaScript, following
`fastatp/repo/mst.py` step for step. If the library's rule ever changes,
this must change with it; `npm test` checks the hash, the record keys and
the layer rule, not the library itself.

## Candidates for later

Nothing here is decided; these are the places a reader of the first guide
is most likely to want a "why".

- What a CID is: the hash of a block's bytes, and why that makes a block
  impossible to swap.
- A record key from the clock: what is in a TID, and why two writers never
  make the same one.
- What a follower checks when a commit arrives, step by step.
- A delete is a commit: which blocks are rebuilt and which survive.
- Presence is permission: a capability record, and what removing it does.

## Before any of this is published

- The prose was drafted by Claude from a chat on 2026-11-20. Jacob checks
  the voice before it goes anywhere public.
- The two outside links in "Read more" were opened on 2026-11-27 and both
  resolve (the atproto repository spec, and the Merkle Search Trees paper
  on HAL).
