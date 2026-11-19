"""Play a tutorial's score to the end with nobody watching, and report each step.

python play.py [ws://localhost/ws] [--speed 4]   (run it on the conductor)

Presses Play, answers every key concept with "Got it", and prints every
command a machine ran with its exit code and last line of output. Exits
non-zero if a machine refused a step or the play stalls. The start of the
CI runner; it checks that the guide plays, not yet that each output matches
the guide's own listings.
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
opts = args.parse_args()


async def main() -> int:
    procs: dict[int, dict] = {}
    failures = 0
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
                    return 1 if failures else 0
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
                elif ev["type"] == "refused":
                    failures += 1
                    print(f"REFUSED step {ev['step']} on {ev['machine']}: {ev['reason']}")


sys.exit(asyncio.run(main()))
