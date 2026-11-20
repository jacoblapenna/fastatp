# The FastATP brand

This folder is the record of how FastATP looks: its colors, its mark, and the
rules for using them. The demo, the tutorials' pictures and the main README
all draw from it.

## Where the truth lives

Two files choose every color and shape, and a third holds the letters of
the name:

- `tutorials/conductor/src/brand/tokens.css` holds every color, for the dark
  and the light theme.
- `tutorials/conductor/src/brand/brand.js` holds the goose (one path) and the
  skein (where the five birds sit).
- `brand/wordmark.json` holds `FastATP` as outlines, drawn once from the
  font, for the lockup.

`python brand/make.py --write` writes everything else from those: the
logo files here, the favicon, the palette table below, and the ground and
fallback colors inside each sketch under `tutorials/sketches/`. Run it after
changing any of them. `python brand/make.py` only reports what is out of
date, and `tests/test_brand.py` fails when anything is.

## The goose

atproto favors a goose: confident, against the grain, opinionated. FastATP's
mark is a skein of five geese seen from below, flying up the page in a V. The
V points up to make an A, for the AT protocol. The bird at the front is in
the mark's orange, `--color-mark`, which is the same on a dark page and a
light one, because a mark is one color or it is not a mark; the four behind
it are in the text color.

| File | What it is | Use it |
| --- | --- | --- |
| `lockup-dark.svg`, `lockup-light.svg` | The skein beside the name, `ATP` in orange | The title of a README or a page. The letters are outlines, so it looks the same with no font installed. |
| `mark-dark.svg`, `mark-light.svg` | The skein of five | Wherever there is room: a README title, a landing page, a chapter menu. About 48 pixels tall or more. |
| `goose-dark.svg`, `goose-light.svg` | One goose, in orange | Beside the name in a header, or anywhere the skein would be smaller than 48 pixels. |
| `favicon.svg` | One goose in the mark's orange, the same on a light tab and a dark one | Browser tabs. |

"Dark" and "light" name the page the file sits on: the lead bird is the
same orange on both, and only the birds behind it change with the text. Keep clear space around the mark of at least one bird's wingspan.
Don't recolor the birds, rotate the V, or set the mark on a photo.

The name is written `FastATP` in running text. The wordmark is `FastATP` in
Space Grotesk Bold with the `ATP` in orange. Its letters are kept as outlines
in `wordmark.json`, drawn once from the font (fontTools for the shapes,
HarfBuzz for the spacing), so no page needs the font to show it.

## The palette: Gander

Taken from the goose: neck black, chinstrap white and beak orange. Charcoal
and white do nearly all the work, and orange is the one hue that is ours.

<!-- palette:start -->
| Token | Dark | Light | What it is for |
| --- | --- | --- | --- |
| `--color-bg` | `#101214` | `#f4f5f6` | the page |
| `--color-surface` | `#171a1d` | `#ffffff` | cards and panels |
| `--color-surface-raised` | `#20242a` | `#eceef0` | boxes on a card, buttons |
| `--color-terminal-bg` | `#0a0c0e` | `#f0f1f3` | terminals and the ground under a sketch |
| `--color-border` | `#333a42` | `#cdd2d8` | lines between things |
| `--color-text` | `#eceef0` | `#15181b` | running text and headings |
| `--color-text-soft` | `#c9ced4` | `#2f353b` | secondary text |
| `--color-text-muted` | `#939ba5` | `#58616b` | labels and small print |
| `--color-mark` | `#ff9a3d` | `#ff9a3d` | the goose's orange, the same on every page; never text |
| `--color-primary` | `#ff9a3d` | `#ff9a3d` | the brand as a fill: the main button, borders and rings |
| `--color-on-primary` | `#1a1005` | `#1a1005` | text on the brand color |
| `--color-accent-text` | `#ff9a3d` | `#a84300` | the brand as words: kickers, prompts, labels; deeper on a light page |
| `--color-highlight` | `#ff9a3d` | `#ff9a3d` | what a key concept points at (the brand color, a ring) |
| `--color-verified` | `#8fd16a` | `#256f1c` | a signature checked out |
| `--color-sealed` | `#5fd4c4` | `#0b6f66` | inside WireGuard: sizes and timing only |
| `--color-plaintext` | `#f2d349` | `#735c00` | readable by anyone on the path |
| `--color-danger` | `#ff7a85` | `#b3243f` | refused, unreachable, failed |
<!-- palette:end -->

## The rules

1. **Running text comes from three tokens only:** `text`, `text-soft` and
   `text-muted`.
2. **Every ink reads on every ground, in both themes.** The grounds are
   `bg`, `surface`, `surface-raised` and `terminal-bg`. `text` and
   `text-soft` keep a contrast of 7:1 on all four; `text-muted`,
   `accent-text` and the four meaning colors keep 4.5:1. `make.py` refuses a
   palette that breaks this.
3. **Orange is the brand, and it is one orange on every page.** `mark` is
   the goose, `primary` the main button, borders and rings, `highlight` what
   a key concept points at; all three are the same value on a dark page and
   a light one, because a mark is one color or it is not a mark. They are
   fills and never small text: where the accent is words, it is
   `accent-text`, which deepens on a light page so it reads. Orange never
   reports a state.
4. **The meaning colors only mean their state:** green for verified, teal
   for sealed, yellow for plaintext, red for refused. They never decorate.
5. **Every picture brings its own ground.** A sketch shown as an image
   cannot see the page's theme, so it carries a ground plate and its own
   light and dark colors. `make.py` writes both; a sketch's author only
   writes `var(--color-name)` and never a raw color.

## Badges

The row of badges under the main README's title is drawn by shields.io. The
label half is `surface-raised` from the dark theme (`20242a`) and the value
half is the light theme's `accent-text` (`a84300`), the deeper orange,
because a badge's value is small white text and the mark's own orange is too
light to carry it. The tests badge keeps its own green or red: it reports a
state, and orange never does.

## What resonates, and why this palette

FastATP is for developers and vibe coders working at a systems level. That
audience trusts tools that are mostly near-black and near-white with one
recognisable hue, where color means state and is never decoration, and where
addresses and commands are set in a monospace face. Orange on charcoal is
home ground for it, and it sits nowhere near Bluesky's blue, which matters
because FastATP is deliberately not Bluesky.
