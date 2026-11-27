# The conductor and the orchestra: orchestrating work with signed records

Getting Started ended on a claim: the engine that played it, step by step,
on three machines, is FastATP too. This guide opens that engine up, checks
the claim, and then puts the engine to work. By the end, a conductor plays
a tune on alice, bob and eve, and you hear each note the moment that
machine's signed record of it lands.

Along the way you will see what makes FastATP a library for orchestrating
work, not just for storing records. A task is a signed record in the
asker's repo. A finished task is a signed record in the worker's repo,
with its results in it. Nobody holds a connection open for the task in
between: you get the answer from your follow when it lands, or you check
back when you are ready.

This guide builds on [Getting Started](GETTING_STARTED.md): the same lab,
the same three machines, the same `./lab.sh`. Run that one first. This one
reuses its words and its files, and explains only what is new.

> 🤖 **Coding agents:** you can run this whole guide yourself, headless, and
> read what happened. [AGENTS.md](AGENTS.md) shows how; the score is
> `CONDUCTOR_AND_ORCHESTRA`.

## What you are building

<!-- @demo machines alice bob eve -->
<!-- @demo peer orchestra.py -->
<!-- @demo start -->
<!-- @demo chapter 1 "Backstage" -->
<!-- @demo blurb The engine that played Getting Started, opened up. One command on alice, then the two signed records behind it: who asked, and who did it. -->

Getting Started ended on a claim: the engine playing it is FastATP too.
This show checks that claim, then puts the engine to work.

<!-- @demo end -->

<!-- @demo start -->

> 🔑 **Key concept: the words in this guide, one line each.**
>
> - **Repo**: one signed tree of records, owned by one DID. Only its owner
>   writes to it.
> - **Record**: one typed entry in a repo, addressed as
>   `at://<did>/<collection>/<rkey>`.
> - **DID**: a permanent name for whoever signs a repo. `did:web:alice.lan`
>   is alice.
> - **Lexicon**: a record type: its name, and the rules its fields keep.
> - **Follow**: read a repo whole, then every commit after it, checking
>   every signature on the way.
> - **Conductor**: the peer that plays the show. It writes down what should
>   happen, in its own repo, and nowhere else.
> - **Cue**: a record in the conductor's repo that asks one machine to do
>   one step.
> - **Player**: a second peer on each machine, backstage, that follows the
>   conductor and acts on the cues addressed to it.

The conductor is one more peer, with a repo of its own. Each machine's
player follows it, and answers in a repo of its own.

![the conductor's repo holds a cue; a machine's backstage repo holds the result; each follows the other](sketches/cue-and-result.svg)

In four chapters:
1. **Backstage.** One command on alice, and the two signed records behind
   it: who asked, and who did it.
2. **The orchestra.** The conductor writes down a note and alice plays it.
   Bob is offline when its part is written, and plays it on coming back.
3. **Scales and chords.** A scale passed from member to member, chords for
   two and three, then a tune, each note signed only once the last was heard.
4. **The show is a repo.** Eve reads the whole show back from the
   conductor's repo, and the one rule every follower has to choose.

<!-- @demo glossary -->
The rest of the guide's words, each one line. On the conductor's screen a
dotted word shows its line when you hover it.

- **Peer**: one process with an identity of its own that hosts repos.
  There is no client and no server; every participant is a peer.
- **Commit**: one signed change to a repo. Every write is one.
- **Signature**: bytes only the holder of a private key could have made.
  Anyone with the matching public key can check them.
- **Backstage**: the demo engine's own network and peers, kept apart so the
  LAN carries only the guide's traffic.
- **Result**: a record in a player's repo that says how a cue's command ended.
- **Member**: a machine in the orchestra, running `orchestra.py`.
- **Play**: a record in the conductor's repo naming members and the notes
  they should play.
- **Tone**: a record in a member's own repo saying the note it played, as
  it played it.
- **Tempo**: a record in the conductor's repo saying how long one tick lasts.
- **Tick**: the unit a Play counts a note's length in.
- **Octave**: the same note, higher or lower. Each octave up doubles the pitch.
- **Hertz**: vibrations per second. Middle C is about 261.6.
- **TID**: a record key made from a timestamp, so keys sort in the order
  they were made.
<!-- @demo /glossary -->

<!-- @demo end -->

## Setup

You need what Getting Started needed: Docker with the compose plugin, and
a kernel that can make a WireGuard interface. Getting Started's Setup
checks both.

This guide runs on the conductor:

```bash
cd tutorials/lab                          # from the repository root
./lab.sh clean                            # start from fresh identities
./lab.sh conductor CONDUCTOR_AND_ORCHESTRA
```

<!-- @demo terminal -->Then open <http://localhost:8080> and press
**Start**. The `./lab.sh` commands in this guide are also yours to type in
a second terminal while the show is up. The notes are not: the conductor
writes those, and only the conductor holds its key.<!-- @demo /terminal -->

The machines are the ones from Getting Started, with one change: they run
`orchestra.py` in place of `peer.py`. The guide asks for that itself, in a
line `./lab.sh conductor` reads. Four files are new:

```text
tutorials/
├── CONDUCTOR_AND_ORCHESTRA.md   this guide
└── lab/
    ├── trace.py                 who asked, and who did it
    ├── music.py                 the orchestra's record types
    ├── orchestra.py             one member of the orchestra: peer.py, plus a follow of the conductor
    └── replay.py                the show, read back from the conductor's repo
```

### `trace.py`: who asked, and who did it

`trace.py` takes the last thing a machine was asked to do and checks both
halves of it: the cue in the conductor's repo, and the result in the
machine's backstage repo. It trusts neither host; it verifies both repos.

```python
"""Trace the last thing a machine was asked to do: the cue that asked, the result that answered.
python trace.py <machine>

Reads two repos and trusts neither host. The machine's backstage repo holds
its answers; the conductor's repo holds the cues. Each is checkpointed and
verified against its own DID document, exactly as follow.py verifies alice.
"""

import asyncio
import sys

import httpx
from backstage import Result
from dids import resolver

from fastatp import RepoMirror

machine = sys.argv[1]
PLAYER = f"did:web:{machine}.stage%3A1337"  # the machine's backstage peer: port 1337 is in its name
CONDUCTOR = "did:web:conductor.stage"


async def checkpoint(did: str, host: str) -> RepoMirror:
    """The whole repo, verified: its commit's signature against the DID's key, and every hash."""
    mirror = RepoMirror(did, resolver=resolver())
    async with httpx.AsyncClient(timeout=5) as http:
        response = await http.get(
            f"http://{host}/xrpc/com.atproto.sync.getRepo", params={"did": did}
        )
    response.raise_for_status()
    await mirror.load_checkpoint(response.content)
    return mirror


async def main() -> None:
    player = await checkpoint(PLAYER, f"{machine}.stage:1337")
    # Record keys are TIDs, timestamps that sort, so the last key is the newest answer.
    answers = sorted(path for path in player.entries if path.startswith(f"{Result.nsid}/"))
    if not answers:
        sys.exit(f"{machine} has answered nothing yet")
    collection, rkey = answers[-1].split("/")
    result = player.get_record(collection, rkey)

    # The answer names the request it answers, by its at:// URI in the conductor's repo.
    cue_uri = result["cue"]
    conductor = await checkpoint(CONDUCTOR, "conductor.stage")
    cue_collection, cue_rkey = cue_uri.removeprefix(f"at://{CONDUCTOR}/").split("/")
    cue = conductor.get_record(cue_collection, cue_rkey)
    if cue["to"] != PLAYER:  # an answer to a cue for someone else proves nothing about this one
        sys.exit(f"{machine}'s last answer names a cue addressed to {cue['to'] or 'nobody'}")

    print(f"asked:    {cue_uri}")
    print(f"          signed by {CONDUCTOR}, verified")
    print(f"          to {cue['to']}: {cue['action']} {cue['command']!r}")
    print(f"answered: at://{PLAYER}/{collection}/{rkey}")
    print(f"          signed by {PLAYER}, verified")
    print(f"          exit {result['code']}, for the cue above")


asyncio.run(main())
```

### `music.py`: the orchestra's record types

`music.py` holds the three record types the orchestra uses: the
conductor's `Tempo` and `Play`, and the `Tone` a member writes back, built
on Getting Started's `Note`.

```python
"""The orchestra's record types: the conductor's Tempo and Play, and the Tone a member writes back.

The conductor says what to play: which member, which note, how long. Each
member decides how: its own table turns a note into a pitch. Tone starts
from guide one's Note and adds what that member actually played. Every number
in here is a whole number with its scale written down, because the atproto
data model has no floats.
"""

from fractions import Fraction
from typing import Annotated

from lexicons import Note
from pydantic import BaseModel, Field

from fastatp import Lexicon
from fastatp.lex.types import AtUri, Did, MaxGraphemes


class Tempo(Lexicon):
    """The conductor, in its own repo, for nobody in particular: how long one tick lasts.

    Every Play counts its notes in ticks, and every member reads the Tempo
    in force to turn ticks into time. Changing it changes the speed of
    everything played after it.
    """

    nsid = "com.example.tutorial.tempo"
    tick_ms: Annotated[int, Field(ge=1, le=60_000, description="how long one tick lasts, in ms")]


class Part(BaseModel):
    """One member's share of a Play: who plays which note, and for how many ticks."""

    member: Did  # the member's DID; a Play may name one member or several
    note: Annotated[str, MaxGraphemes(8)]  # a name like "do" or "sol"; members decide its pitch
    # Each octave doubles the pitch: the "do" in octave 5 is twice the "do" in
    # octave 4, and the same note to the ear, only higher.
    # No upper bound: which octaves are worth playing is a member's rule, not the record's.
    octave: Annotated[int, Field(ge=0)]
    ticks: Annotated[int, Field(ge=1, le=64)]  # how long, counted in the Tempo's ticks


class Play(Lexicon):
    """The conductor, in its own repo: these members, these notes."""

    nsid = "com.example.tutorial.play"
    # No created_at: the record key is a TID, a timestamp already, and the
    # record in the guide is then exactly the record that gets signed.
    parts: Annotated[list[Part], Field(min_length=1)]  # one part is a note, three are a chord


# A frequency is two integers: hertz = pitch x 10^pitch_exponent. Middle C,
# 261.626 Hz, is pitch 261626 with exponent -3, counted in millihertz. The
# exponent is data in the record, like the pitch, so the writer says how
# precise the number is and every reader, in any language, gets exactly that
# number back. A float would leave the precision to whoever printed it last.
Pitch = Annotated[int, Field(ge=1, description="the frequency, in units of 10^pitch_exponent Hz")]
PitchExponent = Annotated[int, Field(description="the power of ten pitch counts in: -3 is mHz")]


class Tone(Note):
    """A member, in its own repo: the note it played for a Play, as it played it.

    text and created_at come from Note, unchanged. Subclassing a Lexicon keeps
    its fields and rules; the new nsid makes it a record type of its own.
    """

    nsid = "com.example.tutorial.tone"
    play: AtUri  # the Play this Tone answers, in the conductor's repo
    pitch: Pitch
    pitch_exponent: PitchExponent
    duration_ms: Annotated[int, Field(ge=1, description="how long it sounded, in ms")]


def hertz(pitch: int, exponent: int) -> Fraction:
    """The frequency exactly: a Fraction, never a float, so every peer agrees on it."""
    return pitch * Fraction(10) ** exponent
```

### `orchestra.py`: one member of the orchestra

`orchestra.py` is a machine's `peer.py`, imported unchanged, plus a follow
of the conductor's repo. It reads each `Play` with its own model, decides
whether to play its part, and writes a `Tone` into its own repo.

```python
"""One member of the orchestra: this machine's peer.py, plus an eye on the conductor.
python orchestra.py <domain>

Runs everything peer.py runs, unchanged: it imports it. Beside it, it follows
the conductor's repo. When a Play there names this machine, this machine
decides, with its own model and its own rules, whether to play its part. If it
does, it writes a Tone into its own repo. Nothing is sent to it and nothing is
sent back: the conductor wrote a record, this machine read it and wrote one of
its own. It can start long after the Play was written and still play its part.
"""

import asyncio
import os
import socket
from datetime import UTC, datetime
from fractions import Fraction

import peer  # this machine's peer, exactly as guide one left it
import uvicorn
from backstage import follow
from music import Play, Tempo, Tone, hertz
from pydantic import ValidationError

CONDUCTOR = os.environ.get("CONDUCTOR") or "did:web:conductor.stage"
CONDUCTOR_HOST = os.environ.get("CONDUCTOR_HOST", "conductor.stage")

# This member's own table: what each note means, in octave 4, in millihertz
# (exponent -3). The conductor never says a frequency; tuning is the member's
# business. Another member could hold another table for the same records.
NOTES = {
    "do": 261_626,
    "re": 293_665,
    "mi": 329_628,
    "fa": 349_228,
    "sol": 391_995,
    "la": 440_000,
    "ti": 493_883,
}
EXPONENT = -3

# This member's own rule, not the conductor's and not the lexicon's: it only
# plays what a person can hear. Fractions keep the comparison exact.
HEARING = (Fraction(20), Fraction(20_000))
# Above octave 10 every note in the table is past hearing, so this member
# stops there, before working out a pitch that could have any number of digits.
HIGHEST_OCTAVE = 10


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def pitch_of(note: str, octave: int) -> int | None:
    """This member's pitch for a note, in millihertz, or None for a note it doesn't know.

    Each octave doubles or halves the pitch. Halving an odd number of
    millihertz leaves half a millihertz over: this member rounds it to the
    nearest whole one, and its Tone says exactly which number it played."""
    if note not in NOTES:
        return None
    return round(NOTES[note] * Fraction(2) ** (octave - 4))


def my_parts(path: str, record: dict, me: str, tick_ms: int) -> list[tuple[str, Tone]]:
    """What this member writes for one Play in the conductor's repo: (rkey, Tone) pairs.

    A Play this member's model refuses is skipped, not obeyed: reading a
    record is not a promise to do what it says.
    """
    try:
        play = Play.model_validate(record)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
        print(f"[orchestra] {path} is not a Play this member understands: {problems}")
        return []
    rkey = path.split("/")[1]
    tones = []
    for i, part in enumerate(play.parts):
        if part.member != me:
            continue  # someone else's part
        if part.octave > HIGHEST_OCTAVE:
            print(f"[orchestra] {path} part {i}: not a note a person can hear; not playing it")
            continue
        pitch = pitch_of(part.note, part.octave)
        if pitch is None:
            print(f"[orchestra] {path} part {i}: no note called {part.note!r} here; not playing it")
            continue
        if not HEARING[0] <= hertz(pitch, EXPONENT) <= HEARING[1]:
            print(f"[orchestra] {path} part {i}: not a note a person can hear; not playing it")
            continue
        tone = Tone(
            text=part.note,
            created_at=now(),
            play=f"at://{CONDUCTOR}/{path}",
            pitch=pitch,
            pitch_exponent=EXPONENT,
            duration_ms=part.ticks * tick_ms,
        )
        # The Tone's rkey is the Play's rkey and the part's place in it, so
        # the same part always lands at the same path. That is what makes
        # playing it idempotent: see on_record.
        tones.append((f"{rkey}-{i}", tone))
    return tones


tempo: dict[str, int | None] = {"tick_ms": None}  # the Tempo in force, once one is seen
held: list[tuple[str, dict]] = []  # Plays seen before any Tempo: they wait for one


async def on_record(path: str, record: dict) -> None:
    kind = record.get("$type")
    if kind == Tempo.nsid:
        # Not addressed to anyone: every member reads it, and it sets the speed
        # of everything played from here on. A new one replaces the old.
        try:
            tempo["tick_ms"] = Tempo.model_validate(record).tick_ms
        except ValidationError:
            print(f"[orchestra] {path} is not a Tempo this member understands")
            return
        print(f"[{peer.domain}] tempo: a tick is {tempo['tick_ms']} ms")
        waiting, held[:] = held[:], []
        for play_path, play in waiting:
            await on_record(play_path, play)
        return
    if kind != Play.nsid:
        return  # anything else in the conductor's repo is not ours to act on
    if tempo["tick_ms"] is None:
        # A checkpoint lists records by collection, so a late member can meet
        # Plays before the Tempo. Without a tempo a tick has no length: wait.
        held.append((path, record))
        return
    for rkey, tone in my_parts(path, record, peer.identity.did, tempo["tick_ms"]):
        # A follow that reconnects sees its checkpoint again, Plays and all.
        # A Tone already at this path means this part was played: once is enough.
        if peer.server.repo.get_record(Tone.nsid, rkey) is not None:
            continue
        await peer.server.put_record(Tone.nsid, rkey, tone)
        print(f"[{peer.domain}] played {tone.text} for {tone.duration_ms} ms, for {tone.play}")


async def main() -> None:
    app = await peer.boot()
    # The eye on the conductor. follow() checkpoints the conductor's repo,
    # verifies it, then tails it, and starts over from a checkpoint whenever
    # the connection drops. If there is no conductor yet, it keeps trying.
    eye = asyncio.create_task(follow(CONDUCTOR, CONDUCTOR_HOST, on_record))
    sockets = [peer.listen(socket.AF_INET, "0.0.0.0"), peer.listen(socket.AF_INET6, "::")]
    print(
        f"[{peer.domain}] serving on http://{peer.domain} (LAN)"
        f" and http://[{peer.tunnel_ip}] (tunnel)"
    )
    print(f"[{peer.domain}] following {CONDUCTOR} for Plays")
    await uvicorn.Server(uvicorn.Config(app, port=80, log_level="warning")).serve(sockets=sockets)
    eye.cancel()


if __name__ == "__main__":
    asyncio.run(main())
```

### `replay.py`: the show, read back

`replay.py` reads the conductor's repo as anyone could: one checkpoint,
verified, and what the records in it say about the show, with no guide
and no page.

```python
"""Read the show back from the conductor's repo, the way anyone could: python replay.py

No guide, no score file, no page: one checkpoint of the conductor's repo,
verified against its DID document, and what the records in it say.
"""

import asyncio
from collections import Counter

import httpx
from backstage import Control, Cue, Show
from dids import resolver

from fastatp import RepoMirror

CONDUCTOR = "did:web:conductor.stage"


async def main() -> None:
    mirror = RepoMirror(CONDUCTOR, resolver=resolver())
    async with httpx.AsyncClient(timeout=10) as http:
        response = await http.get(
            "http://conductor.stage/xrpc/com.atproto.sync.getRepo", params={"did": CONDUCTOR}
        )
    response.raise_for_status()
    await mirror.load_checkpoint(response.content)  # the signature and every hash, checked
    print(f"{CONDUCTOR}: {len(mirror.entries)} records, verified")

    show = mirror.get_record(Show.nsid, "self")  # which show is playing now
    print(f"the show now: {show['score']}")

    # Record keys are TIDs, so sorting the paths puts the cues in the order they were signed.
    cues = [
        mirror.get_record(*path.split("/"))
        for path in sorted(mirror.entries)
        if path.startswith(f"{Cue.nsid}/")
    ]
    mine = [cue for cue in cues if cue["show"] == show["id"]]
    if not mine:
        print("no cues yet: this show has not started")
        return
    print(f"{len(mine)} cues in this show, one per step, in the order they were signed:")
    for action, count in sorted(Counter(cue["action"] for cue in mine).items()):
        print(f"  {count:4d} {action}")
    first = mine[0]
    print(
        f"the first: step {first['step']}, a {first['action']}: {first['title'] or first['text']!r}"
    )

    presses = sum(1 for path in mirror.entries if path.startswith(f"{Control.nsid}/"))
    print(f"{presses} presses of the page's buttons, every show's, each one signed")


asyncio.run(main())
```

<!-- @demo start -->
<!-- @demo chapter 1 "Backstage" -->

## Part 1: backstage

Start with the smallest thing the engine does: one command, on one machine.

<!-- @demo ask Nobody is typing into alice's terminal. How does alice know to run this? -->

```bash
./lab.sh alice hostname
```

```text
alice.lan
```

The conductor wrote a cue into its own repo: "alice, run `hostname`".
Alice's player follows that repo. It saw the cue, checked the conductor's
signature, and ran the command. Then it wrote a result into a repo of its
own: alice's backstage repo.

No request went to alice, and alice sent no reply. Each side read the
other's repo.

Bob had no part in any of that. Bob can still check it, from the two repos
alone.

<!-- @demo ask Bob checks what alice was asked and what alice answered. Whose word does bob take for either? -->

```bash
./lab.sh bob python trace.py alice
```

```text
asked:    at://did:web:conductor.stage/com.fastatp.demo.cue/3n2zbdgkqpsra
          signed by did:web:conductor.stage, verified
          to did:web:alice.stage%3A1337: run 'hostname'
answered: at://did:web:alice.stage%3A1337/com.fastatp.demo.result/3n2zbdhfqm2ra
          signed by did:web:alice.stage%3A1337, verified
          exit 0, for the cue above
```

<!-- @demo point bob.terminal -->

Nobody's. Bob fetched both repos and checked each one against its owner's
DID document: the conductor's key for the question, the player's key for
the answer. Two records, two signatures, two signers.

> 🔑 **Key concept: the demo was FastATP all along.** Every command in
> Getting Started, and every one in this guide, is a cue in the conductor's
> repo and a result in a machine's repo. The conductor asks by writing
> into its own repo; the machine answers by writing into its own. Each
> follows the other, checks every signature, and acts on what it reads.
> The request and the answer are both records, and anyone can check them
> later, as bob just did.

![the conductor's repo holds the cue; alice's backstage repo holds the result; each follows the other](sketches/cue-and-result.svg)

> 💡 **Did you know: a port can be part of a DID.**
> `did:web:alice.stage%3A1337` is alice's backstage peer: the host
> `alice.stage`, on port 1337. The colon before the port is written `%3A`
> because a plain colon already separates the parts of a DID. Alice's
> ordinary peer holds port 80, so the backstage one takes 1337.

<!-- @demo chapter 2 "The orchestra" -->
<!-- @demo blurb The conductor writes down a note, and alice plays it in its own repo. Bob is offline when its part is written, and plays it on coming back. -->

## Part 2: the orchestra

A shell command is the bluntest thing a cue can carry: the player runs
whatever it says. From here on the conductor asks for something narrower,
a note, and each machine decides for itself how to play it.

The machines are running `orchestra.py` this time. It is Getting Started's
`peer.py`, imported unchanged, plus a follow of the conductor's repo. Each
machine is now a member of an orchestra.

First, the tempo. The conductor writes one record that names nobody in
particular: how long one tick lasts. Every member reads it.

<!-- @demo commit at self -->
```json
{"$type": "com.example.tutorial.tempo", "tick_ms": 250}
```

Then a note, for alice alone. The conductor names the note and how long to
hold it: `do`, in octave 4, for 4 ticks. It never says a frequency.

<!-- @demo ask The conductor said "do" and nothing about pitch. Whose pitch will you hear? -->
<!-- @demo code orchestra.py "def my_parts(" -->
<!-- @demo commit alice -->
```json
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "do", "octave": 4, "ticks": 4}]}
```

Alice's. Alice's own table says `do` in octave 4 is 261.626 hertz, and the
tempo says 4 ticks is one second. Alice wrote both into a Tone in its own
repo, and the page played the Tone, not the Play.

<!-- @demo point alice.repo -->

> 🔑 **Key concept: a record type can build on another.** `Tone` is
> Getting Started's `Note`, inherited. It keeps `text` and `created_at`,
> with their rules, and adds the Play it answers, the pitch, the pitch's
> scale, and how long it sounded. `class Tone(Note)` and a new name are
> all it takes. The library exports it as an ordinary lexicon, so a reader
> in any language can check a Tone without knowing it started as a Note.

![Note has text and created_at; Tone inherits both and adds play, pitch, pitch_exponent and duration_ms](sketches/tone-is-a-note.svg)

<!-- @demo ask Alice played 261.626 hertz. Records can't hold a number with a decimal point. So how is it written down? -->

> 🔑 **Key concept: numbers are whole, and carry their scale.** atproto
> records have no floats. Alice's 261.626 hertz is two whole numbers:
> `pitch: 261626` and `pitch_exponent: -3`, which means "counted in
> thousandths of a hertz". A float is a binary approximation: no float is
> exactly 261.626, and how the nearest one is rounded and printed differs
> between languages and machines. A signature covers bytes, not intentions. Two whole numbers are the same
> bytes everywhere. The record says exactly what alice played, and nobody
> downstream gets to round it differently.

![261.626 hertz is written as pitch 261626 and pitch_exponent minus 3](sketches/whole-numbers.svg)

Now take bob offline. Bob's member stops, and the machine stays up.

```bash
./lab.sh bob pkill -xf "python orchestra.py bob.lan"
```

The conductor writes a Play for two: `mi` for alice and `sol` for bob.

<!-- @demo fails 2 -->
<!-- @demo commit alice bob -->
```json
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "mi", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "sol", "octave": 4, "ticks": 4}]}
```

Alice plays its part. Bob says nothing, because nobody is there to read
the Play. The conductor waits a few seconds for bob's answer, then moves
on without it.

<!-- @demo ask Bob was offline when the conductor wrote bob's part. Is it lost? -->

```bash
./lab.sh restart bob
```

Bob's member starts again and follows the conductor from the top. It finds
the Play that names it, and plays its part late. You heard bob's `sol`
land on its own, well after alice's `mi`.

<!-- @demo point bob.repo -->

> 🔑 **Key concept: nobody waits for anybody.** The Play is a record in
> the conductor's repo, and it stays there. A member that was offline reads
> it when it comes back, and answers then; the conductor sees the answer
> on its follow whenever it lands. No connection was held open for the
> task, and the two never had to be up at the same time. Job queues and
> webhooks can do this too. The difference is that here the request and
> the answer are the records themselves, signed, and still there for
> anyone to check later.

![the conductor writes a Play for alice and bob; alice answers at once; bob is offline, comes back, and answers then](sketches/late-member.svg)

> 💡 **Did you know: bob wrote exactly one Tone.** Coming back, bob read
> every Play in the conductor's repo from the top, alice's solo included,
> and wrote one Tone: the part that names bob. Had bob played it before,
> bob would have written nothing. A Tone's record key is the Play's key plus
> the part's place in it, so a part already played is already at its
> path, and bob skips it. Acting once per record is the rule for any
> follower, because every reconnect starts again from the whole repo.

<!-- @demo chapter 3 "Scales and chords" -->
<!-- @demo blurb A scale passed from member to member, one note at a time, then chords for two and three, then Beethoven, conducted note by note. -->

## Part 3: scales and chords

So far each note was a step of its own, and you pressed Next for each.
A scale is eight notes in a row, passed round alice, bob and eve.

The conductor doesn't write the scale down and walk away. It signs the
first note and waits. When alice's Tone lands, the conductor sends its
sound to the page, waits as long as alice says the note lasts, and only
then signs the next one, for bob. Nothing is written ahead.

<!-- @demo ask Eight notes, one member at a time. Who decides when each next note starts? -->
<!-- @demo commit alice bob eve -->
```json
[
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "do", "octave": 4, "ticks": 2}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:bob.lan", "note": "re", "octave": 4, "ticks": 2}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:eve.lan", "note": "mi", "octave": 4, "ticks": 2}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "fa", "octave": 4, "ticks": 2}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:bob.lan", "note": "sol", "octave": 4, "ticks": 2}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:eve.lan", "note": "la", "octave": 4, "ticks": 2}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "ti", "octave": 4, "ticks": 2}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:bob.lan", "note": "do", "octave": 5, "ticks": 2}]}
]
```

The conductor did, from what it saw. Each note started once the one
before it had landed and been heard out. The small gaps you heard are
real: the time it took a member to read a Play and sign a Tone, and for
the conductor to see it.

Now the other way round: one Play for several members at once. First two
of them, alice and bob, `do` and `sol`.

<!-- @demo commit alice bob -->
```json
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "do", "octave": 4, "ticks": 8}, {"member": "did:web:bob.lan", "note": "sol", "octave": 4, "ticks": 8}]}
```

Then all three: `do`, `mi` and `sol`, the chord of C major.

<!-- @demo ask One record names all three. Will the three notes start at the same instant? -->
<!-- @demo commit alice bob eve -->
```json
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "do", "octave": 4, "ticks": 8}, {"member": "did:web:bob.lan", "note": "mi", "octave": 4, "ticks": 8}, {"member": "did:web:eve.lan", "note": "sol", "octave": 4, "ticks": 8}]}
```

Close, but not quite. Each member read the same Play and answered in its
own time, and each note sounded when its own Tone landed. Nobody lined
them up; they were reading the same record.

> 🔑 **Key concept: sequence and fan-out.** Two shapes cover most
> orchestration. In a sequence, the asker writes one task, waits for its
> answer, and only then writes the next: a scale, or a pipeline where each
> step needs the last one's result. In a fan-out, one task names several
> workers, and the asker waits for all of them: a chord, or one job split
> three ways. Both are plain records and follows. The only difference is
> when the conductor writes the next record.

![in a sequence the conductor signs a Play and waits for its Tone before the next; in a fan-out one Play names three members and their Tones land in their own time](sketches/sequence-and-fan-out.svg)

Last, a tune. The conductor speeds up first: a new tempo, at the same key
as the old one, so it replaces it.

<!-- @demo commit at self -->
```json
{"$type": "com.example.tutorial.tempo", "tick_ms": 125}
```

Then the opening of Beethoven's "Ode to Joy", long in the public domain.
Alice has the tune; bob and eve play under it. Thirty Plays, each signed
only once the one before it was heard out.

<!-- @demo commit alice bob eve -->
```json
[
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "mi", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "mi", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "fa", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "sol", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "sol", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "sol", "octave": 2, "ticks": 4}, {"member": "did:web:eve.lan", "note": "re", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "fa", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "sol", "octave": 2, "ticks": 4}, {"member": "did:web:eve.lan", "note": "re", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "mi", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "sol", "octave": 2, "ticks": 4}, {"member": "did:web:eve.lan", "note": "re", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "re", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "sol", "octave": 2, "ticks": 4}, {"member": "did:web:eve.lan", "note": "re", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "do", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "do", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "re", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "mi", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "mi", "octave": 4, "ticks": 6}, {"member": "did:web:bob.lan", "note": "sol", "octave": 2, "ticks": 6}, {"member": "did:web:eve.lan", "note": "re", "octave": 3, "ticks": 6}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "re", "octave": 4, "ticks": 2}, {"member": "did:web:bob.lan", "note": "sol", "octave": 2, "ticks": 2}, {"member": "did:web:eve.lan", "note": "re", "octave": 3, "ticks": 2}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "re", "octave": 4, "ticks": 8}, {"member": "did:web:bob.lan", "note": "sol", "octave": 2, "ticks": 8}, {"member": "did:web:eve.lan", "note": "re", "octave": 3, "ticks": 8}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "mi", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "mi", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "fa", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "sol", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "sol", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "sol", "octave": 2, "ticks": 4}, {"member": "did:web:eve.lan", "note": "re", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "fa", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "sol", "octave": 2, "ticks": 4}, {"member": "did:web:eve.lan", "note": "re", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "mi", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "sol", "octave": 2, "ticks": 4}, {"member": "did:web:eve.lan", "note": "re", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "re", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "sol", "octave": 2, "ticks": 4}, {"member": "did:web:eve.lan", "note": "re", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "do", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "do", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "re", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "mi", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 4}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 4}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "re", "octave": 4, "ticks": 6}, {"member": "did:web:bob.lan", "note": "sol", "octave": 2, "ticks": 6}, {"member": "did:web:eve.lan", "note": "re", "octave": 3, "ticks": 6}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "do", "octave": 4, "ticks": 2}, {"member": "did:web:bob.lan", "note": "sol", "octave": 2, "ticks": 2}, {"member": "did:web:eve.lan", "note": "re", "octave": 3, "ticks": 2}]},
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "do", "octave": 4, "ticks": 8}, {"member": "did:web:bob.lan", "note": "do", "octave": 3, "ticks": 8}, {"member": "did:web:eve.lan", "note": "sol", "octave": 3, "ticks": 8}]}
]
```

The conductor kept time from what it saw. A slow answer from any member
would have held up every note after it, the way one late player holds up
an orchestra.

<!-- @demo chapter 4 "The show is a repo" -->
<!-- @demo blurb Eve reads the whole show back from the conductor's repo alone. Then the one rule every follower has to choose, and where this goes next. -->

## Part 4: the show is a repo

Every step of this show, the captions included, was a cue the conductor
signed into its own repo. So was every note, and every press of the
page's buttons.

<!-- @demo ask Eve never saw this guide's markdown. Can eve tell you what the show was? -->

```bash
./lab.sh eve python replay.py
```

```text
did:web:conductor.stage: ... records, verified
the show now: CONDUCTOR_AND_ORCHESTRA
100 cues in this show, one per step, in the order they were signed:
     8 ask
     4 chapter
    44 commit
     8 concept
     4 heading
     1 restart
     4 run
    27 say
the first: step 0, a chapter: 'Backstage'
... presses of the page's buttons, every show's, each one signed
```

<!-- @demo point eve.terminal -->

Yes. Eve checked the conductor's signature over the whole repo, then
read the show out of it: which guide, every step in the order it was
signed, and every press. Nothing came from the guide or from the page.

> 🔑 **Key concept: the show is a repo.** The conductor keeps everything
> the show is in its own repo: which guide is playing, one cue per step
> with that step's words, the code it showed, the music, the pictures, and
> a signed record of every press. Anyone who can read the repo can replay
> the show and check every signature, long after it ended. The guide is
> where the show came from; the repo is the show.

![the conductor's repo holds the show record, one cue per step, the music, the lab files and pictures, and every press of a button](sketches/show-is-a-repo.svg)

> ⚠️ **Important: what a follower does with a record is its own choice, so
> choose it.** Each player in this lab runs any shell line a cue carries,
> as long as the cue verifies as the conductor's. That is remote execution
> authorized by a signature: fine for a toy on your own computer, and
> nowhere else. The members are narrower. They act only on a Play their
> own model accepts, and only by writing a Tone. Both are policies, chosen
> by whoever runs the follower; nothing in FastATP makes a peer obey a
> record. A real deployment picks the narrow one, or checks each command
> against an allow-list, or keeps the follower behind a VPN's access rules.

A note is a harmless thing to ask for. What about a task that matters,
like your AI concierge booking you a ride, and paying for it? It is the
same shape: a request record, an answer record, and a follower deciding
what it will do. That is the next guide, Hoot.

<!-- @demo end -->

## What you just ran, in orchestration terms

| In this guide | In the systems you may know |
| --- | --- |
| The conductor | The orchestrator: a workflow engine, a job scheduler, an agent loop |
| A `Play` | A task or a job, written where the workers can read it |
| A `Tone` | The task's result, written by the worker, signed by the worker |
| Following a repo | Subscribing to a queue or a topic, with every message signed |
| The `Tempo` | Shared configuration every worker reads |
| A member offline, then back | A worker that was down, catching up from where the queue says |
| `replay.py` | An audit log, kept by the system itself, that anyone can verify |

The difference is the last column's hidden part: in those systems the
queue, the log and the results live on infrastructure you have to trust.
Here each one is a signed record in its owner's repo.

## Things to try

- Retune the orchestra. Change `NOTES` in `orchestra.py` (A at 432 hertz
  instead of 440, say), `./lab.sh clean` and play the guide again. The
  conductor's records don't change; every Tone for that note does, because
  tuning is the members' business.
- Ask for a note nobody knows. Change one part in the tune to
  `"note": "zap"` and play it again: that member refuses, the conductor
  waits out its answer, and the tune stalls for a moment, then carries on.
  The guide is the score, so editing it is editing the show.
- Take a member offline mid-tune. While the "Ode to Joy" plays, run
  `./lab.sh eve pkill -xf "python orchestra.py eve.lan"` in a second
  terminal. Each note naming eve now waits for an answer that never comes,
  then moves on without it. `./lab.sh restart eve` brings eve back, and
  eve plays every part it missed, late, all at once.

## Where this leads

Next is [Hoot](HOOT.md): the same shape, with work that matters. alice asks
an agent for a ride, once. The agent writes one request, two ride companies
bid on it sealed from each other, a bank holds the money and pays, and a car
drives, every step a signed record in its owner's own repo. At the end,
alice checks the whole chain, back to alice's own words.

For code to build on now, `examples/workorder.py` in this repository is
request and answer as a framework, with `PeerApp` dispatching your own
lexicons to handlers.

## Cleanup

```bash
./lab.sh clean
```

That stops the machines and the conductor, and deletes `state/`, the
conductor's repo included.
