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
from music import Play, Tone, hertz
from pydantic import ValidationError

CONDUCTOR = os.environ.get("CONDUCTOR") or "did:web:conductor.stage"
CONDUCTOR_HOST = os.environ.get("CONDUCTOR_HOST", "conductor.stage")

# This member's own rule, not the conductor's and not the lexicon's: it only
# plays what a person can hear. Another member could hold a different rule
# for the same records. Fractions keep the comparison exact.
HEARING = (Fraction(20), Fraction(20_000))


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def my_parts(path: str, record: dict, me: str) -> list[tuple[str, Tone]]:
    """What this member writes for one record in the conductor's repo: (rkey, Tone) pairs.

    Anything that is not a Play is not ours to act on. A Play this member's
    model refuses is skipped, not obeyed: reading a record is not a promise
    to do what it says.
    """
    if record.get("$type") != Play.nsid:
        return []
    try:
        play = Play.model_validate(record)
    except ValidationError as e:
        print(f"[orchestra] {path} is not a Play this member understands: {e.error_count()} errors")
        return []
    rkey = path.split("/")[1]
    tones = []
    for i, part in enumerate(play.parts):
        if part.member != me:
            continue  # someone else's part
        if not HEARING[0] <= hertz(part.pitch, part.pitch_exponent) <= HEARING[1]:
            print(f"[orchestra] {path} part {i}: not a note a person can hear; not playing it")
            continue
        tone = Tone(
            text=part.text,
            created_at=now(),
            play=f"at://{CONDUCTOR}/{path}",
            pitch=part.pitch,
            pitch_exponent=part.pitch_exponent,
            duration_ms=part.duration_ms,
        )
        # The Tone's rkey is the Play's rkey and the part's place in it, so
        # the same part always lands at the same path. That is what makes
        # playing it idempotent: see on_record.
        tones.append((f"{rkey}-{i}", tone))
    return tones


async def on_record(path: str, record: dict) -> None:
    for rkey, tone in my_parts(path, record, peer.identity.did):
        # A follow that reconnects sees its checkpoint again, Plays and all.
        # A Tone already at this path means this part was played: once is enough.
        if peer.server.repo.get_record(Tone.nsid, rkey) is not None:
            continue
        await peer.server.put_record(Tone.nsid, rkey, tone)
        print(f"[{peer.domain}] played {tone.text} for {tone.play}")


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
