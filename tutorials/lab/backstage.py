"""Backstage: the demo engine's own record types, and how it follows a peer.

The conductor and every player import this. None of it is part of the
tutorial being played; it is what plays it. The demo engine tutorial is the
one place these records are the subject rather than the scenery.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx
import websockets
from dids import resolver

from fastatp import Lexicon, RepoMirror
from fastatp.lex.types import Blob


class Show(Lexicon):
    """The conductor, in its own repo, at one fixed rkey: the show now playing."""

    nsid = "com.fastatp.demo.show"
    id: str  # a TID, new each time the conductor boots
    score: str  # which guide


class File(Lexicon):
    """The conductor, in its own repo, one per lab file at a fixed rkey: the
    code a cue can put on show, with what the guide says it is for."""

    nsid = "com.fastatp.demo.file"
    show: str
    name: str
    gloss: str
    text: str


class Image(Lexicon):
    """The conductor, in its own repo, one per picture the guide shows: the
    bytes ride as a blob beside the repo, fetched with ``getBlob``."""

    nsid = "com.fastatp.demo.image"
    show: str
    name: str  # the path the guide uses, such as sketches/private-repo.svg
    alt: str
    image: Blob


class Cue(Lexicon):
    """The conductor, in its own repo: "you, do this step of the score".

    A cue carries everything its step is, so the conductor's repo alone is the
    whole show: a reader who never saw the guide can replay the captions, the
    key concepts, the code on show and what each machine was asked, in order,
    and check the signature on every one. The guide is where the show came
    from; the repo is the show.
    """

    nsid = "com.fastatp.demo.cue"
    show: str  # the Show this cue belongs to; a player only plays the current one
    to: str  # the player's DID; empty for steps no machine performs (a caption, a key concept)
    step: int  # the step's position in the score
    action: (
        str  # run, stop, restart; say, ask, concept, heading, chapter, wait; and the layout verbs
    )
    command: str = (
        ""  # exactly what the player will run, after the conductor filled in this run's values
    )
    env: dict[str, str] = {}  # values captured earlier in the show, such as CH
    display: str = ""  # the line as the guide prints it
    bg: bool = False  # left running, like a second terminal
    text: str = ""  # a caption, a question, a key concept's body, or a heading
    title: str = ""  # a key concept's title
    style: str = ""  # a key concept's kind: key, important, tip
    level: int = 0  # a heading's depth
    targets: list[str] = []  # what the step points at or lays out: machines, sections, the wire
    sketch: str = ""  # the picture a key concept's card carries: an Image record's name
    file: str = ""  # the lab file a command runs, shown beside its machine (a File record)...
    focus: str = ""  # ...open at the first line holding this
    wait_ms: int = 0  # a wait, in milliseconds: the data model has no floats
    chapter: int = 0  # a chapter's number, where one begins

    @classmethod
    def for_step(
        cls, step: dict, *, show: str, to: str, command: str, env: dict[str, str]
    ) -> "Cue":
        """The cue for one step of the score, with all the step says in it."""
        kind = step["kind"]
        code = step.get("code") or {}
        return cls(
            show=show,
            to=to,
            step=step["id"],
            action=step["action"] if kind == "layout" else kind,
            command=command,
            env=env if kind == "run" else {},
            display=step.get("display", ""),
            bg=step.get("bg", False),
            text=step.get("text") or step.get("body") or "",
            title=step.get("title", ""),
            style=step.get("style", ""),
            level=step.get("level", 0),
            targets=step.get("point") or step.get("targets") or step.get("machines") or [],
            sketch=step.get("sketch", ""),
            file=code.get("file", ""),
            focus=code.get("focus", ""),
            wait_ms=int(step.get("seconds", 0) * 1000),
            chapter=step.get("number", 0),
        )


class Output(Lexicon):
    """A player, in its own repo: lines a cue printed, a few at a time."""

    nsid = "com.fastatp.demo.output"
    cue: str  # the at:// URI of the cue
    lines: list[str]


class Result(Lexicon):
    """A player, in its own repo: the cue is finished."""

    nsid = "com.fastatp.demo.result"
    cue: str
    code: int


class View(Lexicon):
    """A player, in its own repo, at one fixed rkey: what this machine sees right now."""

    nsid = "com.fastatp.demo.view"
    identity: dict[str, Any]
    repo: dict[str, Any]
    wg: list[dict[str, Any]]


class Packets(Lexicon):
    """A player, in its own repo: packets this machine sent on the LAN, about a second's worth."""

    nsid = "com.fastatp.demo.packets"
    packets: list[dict[str, Any]]  # times are integer milliseconds: the data model has no floats


class Control(Lexicon):
    """The conductor, in its own repo: a press of Play, Pause, Step or Got it."""

    nsid = "com.fastatp.demo.control"
    action: str
    value: str = ""


OnRecord = Callable[[str, dict], Awaitable[None]]


async def follow(did: str, host: str, on_record: OnRecord) -> None:
    """Follow one repo forever: checkpoint, then tail, verifying every commit.

    on_record(path, record) is called for every record in the checkpoint and
    for every create or update after it. After a reconnect the checkpoint
    repeats records already seen, so on_record must be idempotent per path.
    """
    base = f"{host}/xrpc/com.atproto.sync"
    while True:
        try:
            # A fresh mirror and resolver each time: a peer that restarted may
            # have a new key, and a cached DID document would refuse it.
            mirror = RepoMirror(did, resolver=resolver())
            async with httpx.AsyncClient(timeout=5) as http:
                resp = await http.get(f"http://{base}.getRepo", params={"did": did})
            resp.raise_for_status()
            await mirror.load_checkpoint(resp.content)
            seq = int(resp.headers["x-fastatp-seq"])
            for key in sorted(mirror.entries):
                collection, rkey = key.split("/", 1)
                await on_record(key, mirror.get_record(collection, rkey))
            url = f"ws://{base}.subscribeRepos?did={quote(did, safe='')}&cursor={seq}"
            async with websockets.connect(url, max_size=None) as ws:
                async for frame in ws:
                    for item in await mirror.handle_frame(frame):
                        await on_record(item["path"], item["record"])
                    if mirror.resync_target is not None:
                        break  # take a fresh checkpoint rather than guess
        except Exception:
            await asyncio.sleep(1)
