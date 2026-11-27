"""alice's agent, the concierge: this machine's peer.py, plus a loop that turns alice's words into a ride.

python concierge.py concierge.lan

It follows alice's repo, the ride companies' and the bank's, and writes, in
its own repo, each step that is missing:

1. alice wrote a note asking for a ride: the concierge writes what it
   understood (an Intent), then a Request, for anyone who will bid.
2. The bidding time is over: it takes the cheapest bid it saw in time and
   asks the bank to hold the money for that company (an Authorize).
3. The bank held it: it books the ride with the winner (a Booking).

It calls no one. A company it has never heard of could bid, if it follows
ride requests; a bid it never sees in time is a no-bid.

Its privacy is its own to compose: one private repo for what it understood
(alice only), one for the bank, one for the ride companies, who may read it
only while a request is open, then are let go, except the winner, until the
ride is paid for. Each is one line of its plan.
"""

import asyncio
import os
import re
from datetime import datetime, timedelta

import peer  # this machine's peer, exactly as guide one left it
from lexicons import Note
from ride import (
    ALICE,
    BANK,
    COMPANIES,
    CONCIERGE,
    Allowance,
    Authorize,
    Booking,
    Hold,
    Intent,
    Plan,
    Quote,
    Ref,
    Request,
    Seen,
    Settlement,
    dollars,
    rkey,
    serve,
    when,
)

BID_SECONDS = float(os.environ.get("BID_SECONDS", "3"))  # how long a request stays open
READER = "stand-in: concierge.py read(), rules in place of a model"
NUMBERS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "hundred": 100}


def read(text: str) -> dict | None:
    """The stand-in reader: a sentence in, what alice wants out, or None.

    In production this is where a model reads alice's words. Here a few rules
    play its part, honestly named in every Intent it produces. Which workload
    a follow triggers is the builder's choice: a model, rules, a person."""
    words = text.lower()
    to = re.search(r"\bto (?:the )?([a-z][a-z ]*?)\s*(?:,|\.|$|\bunder\b|\bfor\b)", words)
    under = re.search(r"\bunder \$?(\d+|" + "|".join(NUMBERS) + r")\b", words)
    on = re.search(r"\bon (checking|savings)\b", words)
    if not (to and under and on):
        return None  # not a ride it understands: it does nothing, and says nothing
    most = int(under.group(1)) if under.group(1).isdigit() else NUMBERS[under.group(1)]
    return {
        "pickup": "home",
        "destination": to.group(1).strip(),
        "most": dollars(most * 100),
        "account": on.group(1),
    }


CARS = ["hatchback", "sedan", "suv"]  # a tie on price goes to the nicer car: later is nicer


def bids(seen: Seen, request_ref: Ref, request: Request) -> list[tuple[Ref, Quote]]:
    """Every bid on this request the concierge saw before the deadline."""
    deadline = when(request.bids_by)
    return [
        (ref, quote)
        for company in COMPANIES
        for ref, quote in seen.all(Quote, by=company)
        if quote.request == request_ref and seen.first[ref.uri] <= deadline
    ]


def choose(offers: list[tuple[Ref, Quote]], most) -> tuple[Ref, Quote] | None:
    """The cheapest bid within what alice will pay; on a tie, the nicer car."""
    within = [(ref, q) for ref, q in offers if q.price.value() <= most.value()]
    nicer = {car: i for i, car in enumerate(CARS)}
    return min(within, key=lambda o: (o[1].price.value(), -nicer.get(o[1].car, -1)), default=None)


def decide(seen: Seen, now: datetime) -> Plan:
    plan = Plan()
    # Its repos, and who reads each. alice reads them all: they are alice's
    # errands. "main" holds what it understood; "desk" what it asks the bank;
    # "rides" what it asks the ride companies, who are let in while a request
    # is open, then let go, except a winner, until its ride is paid for.
    plan.let("main", ALICE)
    plan.let("desk", ALICE, BANK)
    plan.let("rides", ALICE)
    allowances = [(r, a) for r, a in seen.all(Allowance, by=ALICE) if a.agent == CONCIERGE]
    mine = {rkey(ref): (ref, x) for ref, x in seen.all(Intent, by=CONCIERGE)}
    requests = {rkey(ref): (ref, r) for ref, r in seen.all(Request, by=CONCIERGE)}

    # 1. alice's words, understood, then put out for bids
    for said, note in seen.all(Note, by=ALICE):
        key = rkey(said)  # one ride, one key, in every repo it touches
        wanted = read(note.text)
        if wanted is None:
            continue
        if key not in mine:
            plan.write(
                "main", key, Intent(said=said, reader=READER, created_at=peer.now(), **wanted)
            )
            continue
        intent_ref, intent = mine[key]
        if key not in requests:
            plan.let("rides", *COMPANIES)  # anyone who bids: let in before the request lands
        bids_by = when(note.created_at) + timedelta(seconds=BID_SECONDS)
        bids_by = max(bids_by, seen.first[intent_ref.uri] + timedelta(seconds=BID_SECONDS))
        request = Request(
            intent=intent_ref,
            pickup=intent.pickup,
            destination=intent.destination,
            bids_by=bids_by.isoformat().replace("+00:00", "Z"),
            created_at=peer.now(),
        )
        plan.write("rides", key, request)

    # 2. bidding closed: the best bid, and the bank asked to hold the money, once
    authorized = {rkey(ref) for ref, _ in seen.all(Authorize, by=CONCIERGE)}
    for key, (request_ref, request) in requests.items():
        if now < when(request.bids_by):
            plan.let("rides", *COMPANIES)  # still open: every company may see it and bid
            continue
        if key in authorized or key not in mine or not allowances:
            continue
        intent = mine[key][1]
        best = choose(bids(seen, request_ref, request), intent.most)
        if best is None:
            continue  # no bid in time, or none within alice's limit: no ride
        plan.let("rides", seen.who(best[0]))  # the winner stays; the others go
        allowance_ref, _ = allowances[-1]  # the newest say-so alice gave
        authorize = Authorize(
            allowance=allowance_ref,
            quote=best[0],
            payee=seen.who(best[0]),
            amount=intent.most,
            account=intent.account,
            created_at=peer.now(),
        )
        plan.write("desk", key, authorize)

    # 3. the money is held: the ride is booked with the winner, who alone stays
    #    in "rides" until the bank has paid it
    settled = {s.hold.uri for _, s in seen.all(Settlement, by=BANK)}
    for authorize_ref, authorize in seen.all(Authorize, by=CONCIERGE):
        key = rkey(authorize_ref)
        held = [ref for ref, h in seen.all(Hold, by=BANK) if h.authorize == authorize_ref]
        if key not in requests:
            continue
        if not held or held[0].uri not in settled:
            plan.let("rides", authorize.payee)
        if not held:
            continue
        request_ref, request = requests[key]
        passed = [ref for ref, _ in bids(seen, request_ref, request) if ref != authorize.quote]
        booking = Booking(
            request=request_ref,
            quote=authorize.quote,
            passed=passed,
            hold=held[0],
            created_at=peer.now(),
        )
        plan.write("rides", key, booking)
    return plan


if __name__ == "__main__":
    asyncio.run(serve(peer, decide, "taking alice's notes as ride requests"))
