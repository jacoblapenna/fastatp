"""A car: this machine's peer.py, plus a loop that drives, by its own clock.

python cab.py cab-17.lan

It follows the company it drives for. Given a dispatch, it drives: a
position each minute on the way to the pickup, a photo of the curb on
arrival, a position each minute of the ride, then the dropoff. All of it is
signed by the car, in the car's own repo.

Everything here is pretend: the clock runs a minute a second (MINUTE_SECONDS),
the GPS is a straight line, the photo is a drawing. What it shows is real:
whatever the car says, it signs, and nobody can say it in the car's place.
"""

import asyncio
import os
from datetime import datetime, timedelta

import peer  # this machine's peer, exactly as guide one left it
import ride
from ride import ALICE, Arrival, Dispatch, Dropoff, Plan, Position, Seen, rkey, serve

MINUTE = timedelta(seconds=float(os.environ.get("MINUTE_SECONDS", "1")))
TO_PICKUP, RIDE = 4, 17  # minutes
# A pretend map, in millionths of a degree: where the car starts, the
# pickup, and the airport.
START = (45_010_000, -100_020_000)
PICKUP = (45_000_000, -100_000_000)
AIRPORT = (45_090_000, -100_150_000)
COMPANY = "did:web:hoot.lan"  # the company this car drives for


def along(a, b, part: float) -> tuple[int, int]:
    return round(a[0] + (b[0] - a[0]) * part), round(a[1] + (b[1] - a[1]) * part)


def curb(when: str) -> bytes:
    """The photo of the curb, as this car's camera took it: here, a drawing."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">
<rect width="320" height="180" fill="#1e2a38"/><rect y="120" width="320" height="60" fill="#3b3b3b"/>
<rect y="112" width="320" height="8" fill="#9a9a9a"/><rect x="40" y="40" width="90" height="72" fill="#5a4636"/>
<rect x="72" y="70" width="26" height="42" fill="#2b2018"/><circle cx="250" cy="40" r="14" fill="#e8e2c6"/>
<text x="12" y="172" fill="#ffffff" font-family="monospace" font-size="10">alice's curb, as cab-17 saw it, {when}</text>
</svg>""".encode()


def car(me: str, add_blob):
    """This car's loop. add_blob stores the photo's bytes beside the repo and
    returns the reference a record carries."""
    photos: dict[str, object] = {}  # one photo per ride, taken once

    def decide(seen: Seen, now: datetime) -> Plan:
        plan = Plan()
        plan.let(
            "ride", COMPANY, ALICE
        )  # a repo of its own, for the company it drives for and the rider
        for dispatch_ref, dispatch in seen.all(Dispatch, by=COMPANY):
            if dispatch.car != me:
                continue
            key = rkey(dispatch_ref)
            started = seen.first[dispatch_ref.uri]  # the car's own clock starts when it hears
            minutes = int((now - started) / MINUTE)
            for m in range(min(minutes, TO_PICKUP) + 1):  # on the way to the pickup
                where = along(START, PICKUP, m / TO_PICKUP)
                leg = position(dispatch_ref, "to pickup", TO_PICKUP - m, where)
                plan.write("ride", f"{key}-a{m:02d}", leg)
            if minutes < TO_PICKUP:
                continue
            if key not in photos:  # at the curb: one photo, taken once
                photos[key] = add_blob(curb(peer.now()), "image/svg+xml")
            arrival = Arrival(dispatch=dispatch_ref, photo=photos[key], created_at=peer.now())
            plan.write("ride", key, arrival)
            for m in range(min(minutes - TO_PICKUP, RIDE) + 1):  # the ride
                where = along(PICKUP, AIRPORT, m / RIDE)
                plan.write(
                    "ride", f"{key}-r{m:02d}", position(dispatch_ref, "riding", RIDE - m, where)
                )
            arrived = [ref for ref, a in seen.all(Arrival, by=me) if a.dispatch == dispatch_ref]
            if minutes >= TO_PICKUP + RIDE and arrived:  # at the airport
                dropoff = Dropoff(
                    dispatch=dispatch_ref, arrival=arrived[0], minutes=RIDE, created_at=peer.now()
                )
                plan.write("ride", key, dropoff)
        return plan

    return decide


def position(dispatch_ref, leg: str, left: int, where: tuple[int, int]) -> Position:
    return Position(
        dispatch=dispatch_ref,
        leg=leg,
        minutes_left=left,
        lat_e6=where[0],
        lon_e6=where[1],
        created_at=peer.now(),
    )


if __name__ == "__main__":

    async def main() -> None:
        def add_blob(data: bytes, mime: str):
            # the photo's bytes go beside the ride's repo, which the car made
            # with a place for them; the arrival record names them by hash
            return ride.DESK.repos["ride"].repo.add_blob(data, mime)

        decide = car(f"did:web:{peer.domain}", add_blob)
        await serve(peer, decide, f"driving for {COMPANY}")

    asyncio.run(main())
