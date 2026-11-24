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
