"""Play a tutorial's score to the end with nobody watching, and report each step.

python play.py [ws://localhost/ws] [--speed 4]   (run it on the conductor)

Presses Play, answers every key concept with "Got it", and prints every
command a machine ran with its exit code and last line of output. Exits
non-zero if there is no conductor, the show was already played, the play
stalls, or a command ended the wrong way.

The score says how each command is meant to end. One the guide marks
`<!-- @demo fails -->` must exit non-zero: a refusal, a ping nobody answers.
Every other command must exit 0. A command left running may be stopped by
the guide's own Ctrl-C; a command that was not left running and is killed
anyway took too long, and that is wrong too. What this does not yet check is
each command's output against the listing printed under it in the guide.
"""

import argparse
import asyncio
import json
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


async def main() -> int:
    procs: dict[int, dict] = {}
    ended: set[int] = set()
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
                    meant = sum(1 for s in steps if s.get("fails"))
                    print(f"{len(ended)} commands ended, {meant} of them meant to fail")
                    for line in wrong:
                        print(f"WRONG: {line}")
                    return 1 if wrong else 0
            elif msg["type"] == "event":
                ev = msg["event"]
                if ev["type"] == "proc" and ev["state"] == "start":
                    procs[ev["step"]] = {"lines": [], **ev}
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
