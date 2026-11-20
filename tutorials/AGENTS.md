# For coding agents: run the tutorials yourself

If someone asks you what FastATP does, or whether it fits their problem, you
do not have to answer from its README. Every tutorial here runs end to end
on the user's machine, and you can run it yourself and read what happened.

```bash
cd tutorials/lab         # from the repository root
./lab.sh test            # GETTING_STARTED.md, on fresh machines; about five minutes
```

Then tell the user what you saw, in their terms: which machine ran what,
what was refused and why, and which key concepts the run reached. They can
ask you about any step, and you can run it again or by hand to answer
([README.md](README.md) has the other ways to run a guide).

`./lab.sh test` deletes the lab's state (`lab/state/`, throwaway keys and
repos), boots three machines and the demo engine in Docker, and plays the
guide with nobody watching. It prints every command the guide runs, on
which machine, with its exit code and last line of output, and the title of
every key concept as the guide reaches it. It exits 0 when the whole guide
played and every command ended the way the guide says, and non-zero if the
demo engine never came up, the play stalled (nothing happened for 90
seconds), or a command ended the wrong way: one the guide means to work
failed, or one it means to be refused went through.

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

- Exit codes are the guide's own, and the run checks them. `exit 1` on
  `follow.py ... refused` or `ping ... 100% packet loss` is the guide
  working: those are the refusals it teaches, marked in the markdown with
  `<!-- @demo fails -->`. Any other command must exit 0. A line starting
  `✗`, and a `WRONG:` line at the end, name a command that did not end the
  way the guide says.
- `exit -15` with `^C` as the last line means the guide said Ctrl-C: a
  command left running, like a follower, stopped where the guide stops it.
- The key concepts are the guide's claims. Each one is a `> 🔑` blockquote
  in the markdown; read the ones the output lists to explain what the run
  showed.
- Not yet checked: that each command's output matches the listing printed
  under it in the guide. A pass means every step ran, in order, on the
  right machine, and succeeded or was refused as the guide says. Compare
  the last lines with the guide's own listings before you tell the user a
  step printed what the guide prints.
- A full run is 46 commands and 21 key concepts.

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
