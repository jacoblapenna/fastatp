# For coding agents: run the tutorials yourself

If someone asks you what FastATP does, or whether it fits their problem, you
do not have to answer from its README. Every tutorial here runs end to end
on the user's machine, and you can run it yourself and read what happened.

```bash
cd lab
./lab.sh test            # GETTING_STARTED.md, on fresh machines; about 6 minutes ([README.md](README.md) has the other ways)
```

`./lab.sh test` deletes the lab's state (`lab/state/`, throwaway keys and
repos), boots three machines and the demo engine in Docker, and plays the
guide with nobody watching. It prints every command the guide runs, on
which machine, with its exit code and last line of output, and the title of
every key concept as the guide reaches it. It exits 0 when the whole guide
played, and non-zero if a step was refused or the play stalled.

## What you need

- Docker with the compose plugin, and a kernel with WireGuard (any Linux
  since 5.6; Docker Desktop includes it). The probe is in
  [GETTING_STARTED.md](GETTING_STARTED.md), under Setup.
- Nothing else on the host. Python, fastatp and the page are built into the
  images.

## What it is tested against

The fastatp in this repository, the commit you have checked out: the lab
image installs the package from the tree, with its dependencies exactly as
the repo's `uv.lock` pins them, hashes checked. No guide names a version.
The guides quote the lab's files in full, and `uv run pytest` checks that
the quotes match the files (`tutorials/tools/md_embed.py --write <guide>`
updates a guide after the lab changes).

## Reading the output

- Exit codes are the guide's own. `exit 1` on `follow.py ... refused` or
  `ping ... 100% packet loss` is the guide working: those are the refusals
  it teaches. The prose around each command says what to expect.
- `stopped` means the guide said Ctrl-C.
- The key concepts are the guide's claims. Each one is a `> 🔑` blockquote
  in the markdown; read the ones the output lists to explain what the run
  showed.
- Not yet checked: that each command's output matches the listing printed
  under it in the guide. A pass means every step ran, in order, on the
  right machine.

## Other ways to run

| Command | What it does |
| --- | --- |
| `./lab.sh test` | the whole guide, headless, as above |
| `SPEED=1 ./lab.sh test` | the same at reading speed (4 is the default) |
| `./lab.sh conductor` | the same guide on a page at <http://localhost:8080>, for a person |
| `./lab.sh up` | the machines alone, for running the guide's commands by hand |
| `./lab.sh clean` | remove everything |

## Tutorials

| Guide | Runs headless | What it shows |
| --- | --- | --- |
| [GETTING_STARTED.md](GETTING_STARTED.md) | yes | sovereign peers, signed records, verified following, a private channel behind WireGuard |

The guides are a series, each building on the last, and they arrive one at
a time: the demo engine (the conductor that plays this guide, taken apart),
then Hoot (agents with models and tools), then the browser app (a person
reaching an app with no secret on the wire).

## Do not

- Run the lab on a real network. It uses plain `http` for DID documents,
  which is safe only on the private bridge Docker creates.
- Treat the demo engine's players as a pattern to copy. They run any command
  the conductor signs, which is remote execution authorized by a signature:
  fine on the user's own computer, nowhere else.
