"""The conductor: plays a tutorial's score across the lab, and shows it on one page.

python conductor.py   (the page is http://localhost:8080, through the published port)

Backstage infrastructure, not part of the tutorial it plays. The conductor is
a peer, did:web:conductor.stage, on the stage network only. It reads the
score (manifest.json, built from the tutorial's markdown) and, one step at a
time, signs a cue into its own repo addressed to the player that should
perform it. Then it waits: it follows every player's repo, verifying each
commit, and moves on when that player's result for the cue arrives. It is a
loop over a list of tasks that waits for signed answers, the same shape as
an agent asking a tool.

Everything the players publish is turned into the events the page draws.
The browser is not a peer; it only watches what the conductor verified.
"""

import asyncio
import contextlib
import json
import os
import re
import time
from pathlib import Path

import uvicorn
from backstage import Control, Cue, File, Image, Show, follow
from dids import resolver
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from fastatp import DIDServer, Identity, build_did_document
from fastatp.tid import generate_tid

SCORE = os.environ.get("SCORE", "GETTING_STARTED")
with open(f"{SCORE}.json", encoding="utf-8") as f:
    score = json.load(f)
steps = score["steps"]

# --- the conductor's own peer -------------------------------------------------
# Made like peer.py's identity, in state/backstage/conductor.stage on your
# computer: it survives a restart, so every past show stays verifiable, and
# goes with `./lab.sh clean`. Each boot starts a new show in the same repo.
host = "conductor.stage"
did = f"did:web:{host}"
SHOW = generate_tid()
BOOT_MS = int(time.time() * 1000)


async def own_peer() -> None:
    """Bootstrapping is awaited, so the conductor's peer is made here, in
    main(), and left in these globals for everything below."""
    global identity, server, twin_slug, document
    identity = await Identity.bootstrap(domain=host, state_dir=Path("/srv/backstage"))
    server = identity.create_server(resolver=resolver())
    twin_slug = identity.chain.twin_did.rsplit(":", 1)[-1]
    document = build_did_document(
        did=did,
        signing_key=identity.signer.public_key,
        also_known_as=[identity.chain.twin_did],
        pds_endpoint=f"http://{host}",
    )


async def did_resolver(slug: str):
    if slug == "":
        return document
    return identity.twin_document() if slug == twin_slug else None


async def chain_resolver(slug: str):
    return identity.chain.to_json() if slug == twin_slug else None


def player(machine: str) -> str:
    return f"did:web:{machine}.stage%3A1337"


async def write(record) -> str:
    rkey = generate_tid()
    await server.put_record(record.nsid, rkey, record)
    return f"{record.nsid}/{rkey}"


# --- the show ---------------------------------------------------------------

events: list[dict] = []  # everything the page has been told, so a tab opened late sees it all
browsers: set[WebSocket] = set()
state = {
    "cursor": -1,
    "mode": "paused",
    "concept": None,
    "speed": 1.0,
    "done": False,
    "until": None,
}
# until: a chapter's first step the show is hurrying to, through the chapters before it
env: dict[str, str] = {}  # CH=... captured from a command's output
subs: dict[str, str] = {}  # an rkey printed in the guide -> the one this run produced
cue_step: dict[str, int] = {}  # cue path -> score step
results: dict[int, asyncio.Event] = {}
output: dict[int, list[str]] = {}
seen: set[tuple[str, str]] = set()  # (machine, path) already turned into events
wake = asyncio.Event()


async def broadcast(message: dict) -> None:
    for ws in list(browsers):
        try:
            await ws.send_text(json.dumps(message))
        except Exception:
            browsers.discard(ws)


async def tell(event: dict) -> None:
    events.append(event)
    await broadcast({"type": "event", "event": event})


async def status() -> None:
    await broadcast({"type": "status", **state})


def listener(machine: str):
    """What to do with each verified record from one player's repo."""

    async def on_record(path: str, record: dict) -> None:
        kind = record.get("$type")
        now = time.time()
        if kind == "com.fastatp.demo.view":  # updated in place, so never deduplicated
            await tell(
                {
                    "type": "view",
                    "machine": machine,
                    "t": now,
                    "identity": record["identity"],
                    "repo": record["repo"] or None,
                    "wg": record["wg"],
                }
            )
            return
        if (machine, path) in seen:
            return  # a checkpoint after a reconnect repeats what we already told
        seen.add((machine, path))
        if kind == "com.fastatp.demo.packets":
            for p in (p for p in record["packets"] if p["at_ms"] >= BOOT_MS):  # this show's only
                await tell(
                    {"type": "wire", "machine": machine, "t": now, **p, "at": p["at_ms"] / 1000}
                )
            return
        step = cue_step.get(record.get("cue", "").split("/", 3)[-1])
        if step is None:
            return
        if kind == "com.fastatp.demo.output":
            for line in record["lines"]:
                output.setdefault(step, []).append(line)
                await tell(
                    {"type": "out", "machine": machine, "t": now, "step": step, "line": line}
                )
        elif kind == "com.fastatp.demo.result":
            if (
                steps[step]["kind"] != "stop"
            ):  # a stop answers for commands that already ended on screen
                await tell(
                    {
                        "type": "proc",
                        "machine": machine,
                        "t": now,
                        "step": step,
                        "state": "exit",
                        "code": record["code"],
                    }
                )
            results.setdefault(step, asyncio.Event()).set()

    return on_record


def first_json(lines: list[str]) -> dict:
    m = re.search(r"\{.*\}", "\n".join(lines), re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


async def cue(step: dict, command: str = "") -> int:
    """Sign a cue into the conductor's repo; the player sees it on the firehose.

    The cue carries the whole step (its words, its targets, the code on show),
    so the repo alone replays the show."""
    path = await write(
        Cue.for_step(
            step,
            show=SHOW,
            to=player(step["machine"]) if "machine" in step else "",
            command=command,
            env=env,
        )
    )
    cue_step[path] = step["id"]
    return step["id"]


# What one press of Next shows. Headings, layout changes and timed waits are
# not steps a viewer looks at: the director runs through them to the next of
# these. A key concept waits for Got it, and Got it is also Next: the step
# the concept set up follows without another press. Play (auto) paces
# everything by the speed instead.
VISIBLE = ("run", "restart", "stop", "say", "concept", "chapter")
PAUSES = tuple(k for k in VISIBLE if k != "concept")


async def perform(step: dict) -> None:
    kind = step["kind"]
    auto = state["mode"] == "playing"
    hurrying = state["until"] is not None  # catching up to a chosen chapter: no pauses at all
    pace = 0 if hurrying or not auto else 1 / state["speed"]  # stepping: the viewer sets the pace
    if kind in ("run", "restart", "stop"):
        command = step.get("cmd", "")
        for literal, value in subs.items():  # an rkey the guide printed, swapped for this run's
            command = command.replace(literal, value)
        done = results.setdefault(step["id"], asyncio.Event())
        await cue(step, command)
        if kind != "stop":
            await tell(
                {
                    "type": "proc",
                    "machine": step["machine"],
                    "t": time.time(),
                    "step": step["id"],
                    "state": "start",
                    "display": step["display"],
                    "bg": step.get("bg", False),
                }
            )
        if kind == "run" and step["bg"]:
            # Left running, like a second terminal: move on once it has had
            # its say, or as soon as it gives up on its own.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(done.wait(), timeout=6)
            return
        await done.wait()  # the player's signed answer, verified, is the only way past this line
        if kind == "run":
            lines = output.get(step["id"], [])
            if "capture" in step:
                env[step["capture"]] = "\n".join(lines).strip()
            for save in step.get("save", []):
                uri = first_json(lines).get("uri", "")
                if save["field"] == "rkey" and uri:
                    subs[save["literal"]] = uri.rsplit("/", 1)[-1]
            await asyncio.sleep(1.2 * pace)
        return
    await cue(step)  # the rest of the score is in the repo too: the show is a record
    if kind == "heading":
        await asyncio.sleep(1.5 * pace)
    elif kind == "say":
        await asyncio.sleep(
            min(max(len(step["text"].split()) / 4, 3), 14) * pace
        )  # about reading speed
    elif kind == "concept" and not hurrying:
        state["concept"] = step["id"]
        await status()
        while state["concept"] is not None:
            wake.clear()
            await wake.wait()
    elif kind == "wait":
        await asyncio.sleep(step["seconds"])


async def director() -> None:
    while True:
        if state["mode"] == "paused" or state["done"]:
            wake.clear()
            await wake.wait()
            continue
        state["cursor"] += 1
        if state["cursor"] >= len(steps):
            state["done"], state["mode"] = True, "paused"
            await status()
            continue
        if state["until"] is not None and state["cursor"] >= state["until"]:
            state["until"], state["mode"] = None, "step"  # arrived: this press opens the chapter
        await status()
        step = steps[state["cursor"]]
        await perform(step)
        if state["mode"] == "step" and (
            step["kind"] in PAUSES or state["cursor"] + 1 >= len(steps)
        ):
            state["mode"] = "paused"  # one visible step per press of Next
            await status()


def jump(number: str) -> None:
    """Open a chapter, when nothing is running.

    A chapter builds on the ones before it, so choosing one the show has not
    reached plays everything up to it first, as fast as the machines go and
    with no stops, then opens it. Choosing one already passed starts it again
    from its first step, on the machines as they are: every step really ran.
    Steps performed again forget their earlier answers."""
    if state["mode"] != "paused" or state["concept"] is not None:
        return
    start = next((c["start"] for c in score["chapters"] if str(c["number"]) == number), None)
    if start is None:
        return
    if start > state["cursor"] + 1:
        state.update(until=start, mode="playing", done=False)  # hurry there, then open it
        return
    for sid in [k for k in results if k >= start]:
        results.pop(sid)
        output.pop(sid, None)
    state.update(cursor=start - 1, done=False, mode="step")  # this press opens the chapter


# --- the page -----------------------------------------------------------------


async def browser(ws: WebSocket) -> None:
    await ws.accept()
    browsers.add(ws)
    await ws.send_text(
        json.dumps({"type": "hello", "did": did, "manifest": score, "events": events, **state})
    )
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg["type"] not in ("play", "pause", "step", "gotit", "speed", "chapter"):
                continue
            await write(
                Control(action=msg["type"], value=str(msg.get("value", "")))
            )  # every press is signed too
            if msg["type"] in ("play", "pause", "step"):
                state["mode"] = {"play": "playing", "pause": "paused", "step": "step"}[msg["type"]]
            elif msg["type"] == "gotit":
                state["concept"] = None
            elif msg["type"] == "speed":
                state["speed"] = float(msg["value"])
            elif msg["type"] == "chapter":
                jump(str(msg["value"]))
            wake.set()
            await status()
    except WebSocketDisconnect:
        browsers.discard(ws)


@contextlib.asynccontextmanager
async def lifespan(app):
    await server.put_record(
        Show.nsid, "self", Show(id=SHOW, score=SCORE)
    )  # before any cue: "this is the show now"
    for name, lab_file in score.get("files", {}).items():  # the code a cue can put on show
        await server.put_record(File.nsid, name, File(show=SHOW, name=name, **lab_file))
    for name, image in score.get("images", {}).items():  # the pictures, as blobs beside the repo
        data = Path(name).read_bytes()
        blob = server.repo.add_blob(data, "image/svg+xml" if name.endswith(".svg") else "image/png")
        await server.put_record(
            Image.nsid,
            name.replace("/", "-"),
            Image(show=SHOW, name=name, alt=image["alt"], image=blob),
        )
        image["cid"] = blob.ref.ref  # the page fetches it from this repo with getBlob
    tasks = [asyncio.create_task(director())]
    for machine in score["machines"]:
        tasks.append(
            asyncio.create_task(follow(player(machine), f"{machine}.stage:1337", listener(machine)))
        )
        tasks.append(
            asyncio.create_task(
                tell({"type": "stage", "machine": machine, "state": "up", "t": time.time()})
            )
        )
    yield
    for task in tasks:
        task.cancel()


async def main() -> None:
    await own_peer()
    app = Starlette(
        routes=[
            WebSocketRoute("/ws", browser),
            Route("/manifest.json", lambda r: JSONResponse(score)),
            *server.app.routes,  # the conductor's repo, on the standard sync routes, for the players to follow
            *DIDServer(
                domain=host, resolver=did_resolver, chain_resolver=chain_resolver
            ).app.routes,
            Route("/", lambda r: FileResponse("page/index.html")),
            Mount("/", StaticFiles(directory="page")),
        ],
        lifespan=lifespan,
    )
    await uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=80, log_level="warning")).serve()


if __name__ == "__main__":
    asyncio.run(main())
