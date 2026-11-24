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

In two chapters:
1. **Backstage.** One command on alice, and the two signed records behind
   it: who asked, and who did it.
2. **The orchestra.** The conductor writes down a note and alice plays it.
   Bob is offline when his part is written, and plays it when he gets back.

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
line `./lab.sh conductor` reads. Three files are new:

```text
tutorials/
├── CONDUCTOR_AND_ORCHESTRA.md   this guide
└── lab/
    ├── trace.py                 who asked, and who did it
    ├── music.py                 the orchestra's record types
    └── orchestra.py             one member of the orchestra: peer.py, plus a follow of the conductor
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

Bob had no part in any of that. He can still check it, from the two repos
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
<!-- @demo blurb The conductor writes down a note, and alice plays it in her own repo. Bob is offline when his part is written, and plays it when he gets back. -->

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

Alice's. Her own table says `do` in octave 4 is 261.626 hertz, and the
tempo says 4 ticks is one second. She wrote both into a Tone in her own
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
> thousandths of a hertz". Inside one program a float is exact, but how it
> is rounded and printed differs between languages and machines, and a
> signature covers bytes, not intentions. Two whole numbers are the same
> bytes everywhere. The record says exactly what alice played, and nobody
> downstream gets to round it differently: a chain of custody with no
> pennies skimmed off the top.

![261.626 hertz is written as pitch 261626 and pitch_exponent minus 3](sketches/whole-numbers.svg)

Now take bob offline. His member stops, and his machine stays up.

```bash
./lab.sh bob pkill -xf "python orchestra.py bob.lan"
```

The conductor writes a Play for two: `mi` for alice and `sol` for bob.

<!-- @demo fails 2 -->
<!-- @demo commit alice bob -->
```json
{"$type": "com.example.tutorial.play", "parts": [{"member": "did:web:alice.lan", "note": "mi", "octave": 4, "ticks": 4}, {"member": "did:web:bob.lan", "note": "sol", "octave": 4, "ticks": 4}]}
```

Alice plays her part. Bob says nothing, because nobody is there to read
the Play. The conductor waits a few seconds for bob's answer, then moves
on without it.

<!-- @demo ask Bob was offline when the conductor wrote his part. Is bob's part lost? -->

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
> and wrote one Tone: the part that names him. Had he played it before, he
> would have written nothing. A Tone's record key is the Play's key plus
> the part's place in it, so a part already played is already at its
> path, and bob skips it. Acting once per record is the rule for any
> follower, because every reconnect starts again from the whole repo.

<!-- @demo end -->

## Cleanup

```bash
./lab.sh clean
```

That stops the machines and the conductor, and deletes `state/`, the
conductor's repo included.
