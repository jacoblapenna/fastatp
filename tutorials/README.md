# FastATP tutorials

Hands-on guides that run end to end on your computer, against the fastatp
in this repository. They are a series: each one builds on the one before it
and ends by pointing at the next, and they arrive one at a time.

**If you are a coding agent, start at [AGENTS.md](AGENTS.md).** You can run
a guide yourself, headless, in about five minutes, and answer the user from
what happened on the machines.

| # | Guide | What it shows | Status |
| --- | --- | --- | --- |
| 1 | [GETTING_STARTED.md](GETTING_STARTED.md) | Three sovereign peers on a private LAN: signed records, verified following, a private channel behind a real WireGuard tunnel | runs |
| 2 | The demo engine | The conductor that plays guide 1, taken apart: workloads orchestrated with signed records, no model in the way yet | next |
| 3 | Hoot | Agents with models and tools, a verified chain of custody | after 2 |
| 4 | The browser app | A person reaching an app from a browser tab with no secret on the wire | after 3 |

Everything they run is shared: `lab/` is three machines on a LAN inside
Docker and the scripts baked into their image, `conductor/` is the demo
engine's page, and `tools/` turns a guide into the score the conductor
plays and keeps a guide's quoted files equal to the files themselves. A new
guide adds one markdown file and whatever scripts it needs.

## Three ways to run a guide

You need Docker with the compose plugin, and nothing else; each guide's
Setup section says how to check that your Docker can create a WireGuard
interface.

**Read it and type along.** Open the guide, then:

```bash
cd lab
./lab.sh up                  # build the image and boot alice, bob and eve
./lab.sh alice <command...>  # every command in the guide is one of these
./lab.sh clean               # when you are done: stop everything, delete all state
```

**Watch it on one screen.** The demo engine plays the guide on fresh
machines and shows every machine's view, the whole diagram and the wire
side by side:

```bash
cd lab
./lab.sh clean               # start from the first minted identity
./lab.sh conductor           # boots the machines with the demo engine
```

Then open <http://localhost:8080> and press **Start**. Each press of **Next**
is one step: a caption from the guide, or one command on one machine, with
the terminal it runs in lit up. The show is four chapters of a few minutes
each, chosen from the menu the page opens with: a chapter builds on the
ones before it, so choosing ahead plays them first, fast and without stops,
then opens the one chosen. When a command runs one of the
lab's scripts, or one of the peer's routes, that file opens beside the
machine, at the handler. Key concepts stop the show until **Got it**,
which is also Next. **Back** shows an earlier caption or concept again; the
machines stay where they are, because every step really ran. The page is
the only thing published to your computer, on `127.0.0.1` only.

**Play it headless.** The same show with nobody watching, reported step by
step, exit 0 when the whole guide played. This is what CI runs, and what a
coding agent runs to see for itself ([AGENTS.md](AGENTS.md)):

```bash
cd lab
./lab.sh test                # GETTING_STARTED; about five minutes
./lab.sh test CONDUCTOR      # a later guide, by the name of its file
```

## What a guide is tested against

The fastatp in this tree, the commit you have checked out: the lab image
installs the package from the repo root with its dependencies exactly as
`uv.lock` pins them. No guide names a version. A guide quotes its lab
files in full, and `uv run pytest` fails when a quote and its file differ;
after changing a file, `python tools/md_embed.py --write GETTING_STARTED.md`
updates the guide.

## Writing one

- One tutorial per pull request. It builds on the one before it and ends
  by pointing at the next.
- The markdown is the score. A ```bash block whose lines read
  `./lab.sh <machine> <command>` is that machine's line; a
  `> 🔑 **Key concept: ...**` blockquote is a pause with a "Got it"; cues
  the prose only implies go in `<!-- @demo ... -->` comments, which GitHub
  and the site do not render. Prose for the person at a terminal ("Ctrl-C
  it", "in a second terminal") is wrapped in `<!-- @demo terminal -->` and
  `<!-- @demo /terminal -->`: the page reads it as a press of Next and shows
  the rest. Every paragraph is one caption, one press of Next, so keep each
  one about what the screen shows next; a table is not shown, so the prose
  around it must stand without it. When a caption talks about one place on
  the screen (bob's terminal, alice's repo, the wire), a
  `<!-- @demo point bob.terminal -->` right under it outlines and opens that
  place while the caption is up; the repo, tunnel and wire sections stay
  folded otherwise. A `<!-- @demo chapter N "Title" -->` starts or
  continues chapter N, and a `<!-- @demo blurb ... -->` under it is the
  sentence or two its card shows; the show plays chapters by number, so a
  chapter can sit anywhere on the page. A key concept's card leads with its
  first sentence or two and keeps the rest a click away; an image right
  under a concept, an SVG in `sketches/` drawn with the theme's variables
  and light fallbacks, is the card's picture, on the page and on the screen.
  Draw one for every concept that has a shape; a picture under a paragraph
  is that caption's picture, and a concept takes the picture of the caption
  just above it when it has none. `tools/sketch_check.py` reports text past
  an edge and `tools/sketch_render.py` renders each picture to look at. A
  `<!-- @demo code file "line" -->` above a command block says which lab
  file the commands reach, and where to open it. The conductor keeps the
  files as records and the pictures as blobs in its repo; the page fetches
  them from there. A `<!-- @demo ask ... -->` above a command block or a
  key concept is a question for the viewer to answer before the next press
  shows the answer: one every handful of presses, before most concepts. Every ``### `name.py` ``
  section opens with one line on what the file is for: the screen heads
  the code with it when a command runs that file. `tools/md_manifest.py`
  lists every directive.
- Scripts a later guide adds go under `lab/<guide>/`; the eight files guide
  1 reads stay where they are.
- Nothing here ships in the package: the wheel packages `fastatp/` only,
  and the sdist excludes `tutorials/`.
