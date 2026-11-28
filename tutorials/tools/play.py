"""Play a tutorial's score to the end with nobody watching, and report each step.

python play.py [ws://localhost/ws] [--speed 4]   (run it on the conductor)

Presses Play, answers every key concept with "Got it", and prints every
command a machine ran with its exit code and last line of output. Exits
non-zero if there is no conductor, the show was already played, the play
stalls, or a command ended the wrong way.

The score says how each command is meant to end. One the guide marks
`<!-- @demo fails -->` must exit non-zero: a refusal, a ping nobody answers.
Every other command must exit 0. A record the conductor commits for some
members is answered by each of them, except the members the guide's fails
cue names, who must stay silent. A command left running may be stopped by
the guide's own Ctrl-C; a command that was not left running and is killed
anyway took too long, and that is wrong too.

A step that is the viewer's turn (`<!-- @demo you ... -->`) is done in the
app, by hands: `hands.py`, on the computer the page is on, plays them in a
headless Chrome while this plays the rest. Each turn must end with the
request the score names; a turn skipped, or never ended, is wrong.

The score also carries the listing the guide prints under a command block,
and what the commands really printed is held to it (`listing_problems`):
every line the guide shows must have been printed, in the guide's order.
A listing is an excerpt, so lines it leaves out are fine, and so is a value
it cuts short with `...`. Values that are new on every run are compared by
their shape and not their value; SHAPES is the whole list of those.
"""

import argparse
import asyncio
import json
import re
import sys

import websockets

args = argparse.ArgumentParser()
args.add_argument("url", nargs="?", default="ws://localhost/ws")
args.add_argument("--speed", type=float, default=4)
args.add_argument(
    "--stall", type=float, default=90, help="seconds without progress before giving up"
)
args.add_argument(
    "--until", help="stop, leaving this key concept open on screen (a substring of its title)"
)


BASE58 = "1-9A-HJ-NP-Za-km-z"
# What is new on every run, and so is compared by shape: (what it is, how to
# spot one in the guide's listing, what may stand in its place in the output).
# Everything else in a listing is compared exactly, numbers included (spacing and
# line wrapping aside: see listing_problems).
SHAPES = [
    ("a timestamp", r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z", None),
    ("the last digits of a timestamp", r"\d\.\d{6}(?=[Z\[])", r"\d\.\d{6}"),
    ("a did:key", rf"did:key:z[{BASE58}]+", None),
    ("a key in multibase", rf"(?<![A-Za-z0-9])z[{BASE58}]{{40,}}", rf"z[{BASE58}]{{40,}}"),
    ("a CID", r"baf[a-z2-7]{8,}", None),
    ("a WireGuard key in an address", r"(?<=pubkey=)[A-Za-z0-9%+/=]+", None),
    ("a WireGuard key", r"[A-Za-z0-9+/]{43}=", None),
    (
        "how long ago",
        r"\d+ (?:second|minute|hour)s?(?:, \d+ (?:second|minute)s?)* ago",
        r"\d+(?:second|minute|hour)s?(?:,\d+(?:second|minute)s?)*ago",
    ),
    (
        "how much traffic",
        r"\d+(?:\.\d+)? (?:B|KiB|MiB|GiB)(?= (?:received|sent))",
        r"\d+(?:\.\d+)?(?:B|KiB|MiB|GiB)",
    ),
    ("a tunnel address", r"(?:[0-9a-f]{1,4}:){4,7}[0-9a-f]{1,4}", None),
    ("a private key in hex", r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", r"[0-9a-f]{64}"),
    ("a twin's name", r"(?<=:)[a-z2-7]{24,40}(?![a-z0-9.])", r"[a-z2-7]{24,40}"),
    ("a record key or a rev", r"(?<![a-z0-9])3[a-z2-7]{12}(?![a-z0-9])", r"3[a-z2-7]{12}"),
    ("a value cut short", r"[A-Za-z0-9+/]{4,}(?=\.\.\.)", r"[A-Za-z0-9+/=]+"),
]
SPOT = re.compile("|".join(f"(?P<s{n}>{spot})" for n, (_, spot, _) in enumerate(SHAPES)))


def line_pattern(line: str) -> str:
    """One line of a listing as a pattern over output with its whitespace taken out."""

    def exact(text: str) -> str:
        # `...` in a listing stands for whatever was left out
        return ".*?".join(re.escape("".join(part.split())) for part in text.split("..."))

    pattern, at = "", 0
    for found in SPOT.finditer(line):
        n = int(found.lastgroup[1:])
        pattern += exact(line[at : found.start()]) + (SHAPES[n][2] or SHAPES[n][1])
        at = found.end()
    return pattern + exact(line[at:])


def listing_problems(listing: str, printed: list[str]) -> list[str]:
    """The lines of a guide's listing that the commands did not print, in order.

    Whitespace is not compared: the guide may wrap a long line or show JSON
    on one line that a command printed on several."""
    output = "".join("".join(line.split()) for line in printed)
    missing, at = [], 0
    for line in listing.splitlines():
        if not line.strip():
            continue
        found = re.compile(line_pattern(line), re.S).search(output, at)
        if found:
            at = found.end()
        else:
            missing.append(line.strip())
    return missing


NEVER_RAN = (126, 127)  # the shell's own codes: not executable, and command not found


def verdict(step: dict, code: int, lines: list[str] | None = None) -> str | None:
    """What is wrong with how a command ended, or None if it ended as the guide says."""
    if any(line.startswith("[player] ") for line in lines or []):
        # the player could not start it, or was restarted under it: nothing was shown
        return "the player could not run this, so it showed nothing either way"
    if step.get("fails"):
        if code in NEVER_RAN:
            return f"the guide says this is refused, and it never ran (exit {code})"
        return None if code > 0 else f"the guide says this fails, and it exited {code}"
    if code == 0:
        return None
    if code < 0 and step.get("bg"):
        return None  # left running, and stopped where the guide stops it
    if code < 0:
        return f"killed (signal {-code}): it was not meant to be left running, and took too long"
    return f"the guide says this works, and it exited {code}"


def commit_verdict(step: dict, silent: list[str], meant: list[str] | None = None) -> str | None:
    """What is wrong with who answered a committed record, or None if it went as the guide says.

    meant is who the conductor says was meant to stay silent among the members
    this one record names: in a sequence, a record may not name them all."""
    meant = set(step.get("silent", []) if meant is None else meant)
    if unexpected := sorted(set(silent) - meant):
        return f"no answer from {', '.join(unexpected)}"
    if spoke := sorted(meant - set(silent)):
        return f"the guide says {', '.join(spoke)} stays silent, and answered"
    return None


async def main() -> int:
    procs: dict[int, dict] = {}
    ended: set[int] = set()
    committed: set[int] = set()  # commit steps whose wait for answers is over
    turned: set[int] = set()  # the viewer's turns that ended
    wrong: list[str] = []
    for _ in range(60):  # the conductor may still be starting
        try:
            ws = await websockets.connect(opts.url, max_size=None)
            break
        except OSError:
            await asyncio.sleep(0.5)
    else:
        print(f"no conductor at {opts.url}")
        return 1
    async with ws:
        hello = json.loads(await ws.recv())
        steps = hello["manifest"]["steps"]
        if hello["done"]:
            # Pressing Play on a finished show does nothing, and nothing is not a pass.
            print("this show has already been played; ./lab.sh clean starts a fresh one")
            return 1
        await ws.send(json.dumps({"type": "speed", "value": opts.speed}))
        await ws.send(json.dumps({"type": "play"}))
        while True:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=opts.stall))
            except TimeoutError:
                print(f"stalled: nothing happened for {opts.stall:.0f}s")
                return 1
            if msg["type"] == "status":
                if msg["concept"] is not None:
                    title = steps[msg["concept"]]["title"]
                    print(f"  🔑 {title}")
                    if opts.until and opts.until in title:
                        return 0
                    await ws.send(json.dumps({"type": "gotit"}))
                if msg["done"]:
                    for step in steps:  # a refusal that never came is not a refusal
                        if step.get("fails") and step["id"] not in ended:
                            wrong.append(f"step {step['id']} never ended: {step['display'][:70]}")
                    for step in steps:
                        if step["kind"] == "commit" and step["id"] not in committed:
                            wrong.append(
                                f"step {step['id']} never finished: {step['display'][:70]}"
                            )
                    for step in steps:
                        if step["kind"] == "you" and step["id"] not in turned:
                            wrong.append(f"step {step['id']}, your turn, never ended: no hands?")
                    meant = sum(1 for s in steps if s.get("fails"))
                    print(f"{len(ended)} commands ended, {meant} of them meant to fail")
                    compared = 0
                    for step in steps:  # at the end: a command left running printed until now
                        for listing in step.get("listings", []):
                            compared += 1
                            printed = [
                                line
                                for source in listing["from"]
                                for line in procs.get(source, {"lines": []})["lines"]
                            ]
                            for line in listing_problems(listing["text"], printed):
                                wrong.append(
                                    f"step {step['id']}: the guide's listing shows"
                                    f" {line[:90]!r}, and that was not printed"
                                )
                    print(f"{compared} listings compared with what the commands printed")
                    for line in wrong:
                        print(f"WRONG: {line}")
                    return 1 if wrong else 0
            elif msg["type"] == "event":
                ev = msg["event"]
                if ev["type"] == "proc" and ev["state"] == "start":
                    procs[ev["step"]] = {"lines": [], **ev}
                elif ev["type"] == "commit":
                    step = steps[ev["step"]]
                    if ev.get("of", 1) == 1:
                        print(f"{ev['step']:4d} conductor commits {step['display'][:70]}")
                    elif ev["index"] == 1:
                        print(f"{ev['step']:4d} conductor conducts {step['display'][:70]}")
                elif ev["type"] == "answer":
                    if steps[ev["step"]].get("records") is None:  # a sequence is too many to list
                        print(f"            └ {ev['machine']} answered: {ev['uri']}")
                elif ev["type"] == "you":
                    turned.add(ev["step"])
                    text = steps[ev["step"]]["text"]
                    print(f"{ev['step']:4d} you    {text[:62]}")
                    print(f"            └ {ev['saw']}")
                    if ev["saw"] == "skipped":
                        wrong.append(f"step {ev['step']}: your turn was skipped, not done")
                elif ev["type"] == "commit-end":
                    committed.add(ev["step"])
                    if problem := commit_verdict(steps[ev["step"]], ev["silent"], ev.get("meant")):
                        wrong.append(f"step {ev['step']}: {problem}")
                        print(f"            ✗ {problem}")
                elif ev["type"] == "out" and ev["step"] in procs:
                    procs[ev["step"]]["lines"].append(ev["line"])
                elif ev["type"] == "proc" and ev["state"] == "exit":
                    p = procs.get(ev["step"], {"display": "?", "lines": []})
                    last = next((line for line in reversed(p["lines"]) if line.strip()), "")
                    print(
                        f"{ev['step']:4d} {ev['machine']:6s} exit {ev['code']:<3} {p['display'][:70]}"
                    )
                    if last:
                        print(f"            └ {last[:110]}")
                    ended.add(ev["step"])
                    if problem := verdict(steps[ev["step"]], ev["code"], p["lines"]):
                        wrong.append(f"step {ev['step']} on {ev['machine']}: {problem}")
                        print(f"            ✗ {problem}")


if __name__ == "__main__":
    opts = args.parse_args()  # read here, so the checks above can be imported and tested
    sys.exit(asyncio.run(main()))
