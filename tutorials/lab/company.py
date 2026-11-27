"""A ride company: this machine's peer.py, plus a loop that bids, dispatches and bills.

python company.py hoot.lan     (or jitney.lan: the same program, its own prices)

It follows ride requests from the concierges it knows, the bank, and its own
cars, and writes, in its own repo, each step that is missing:

1. A request it can serve, still open: a bid (a Quote).
2. A booking for its bid: it checks the hold in the bank's own repo, not on
   the concierge's word, and sends a car (a Dispatch).
3. Its car dropped the rider off: the bill (a Fare).
"""

import asyncio
from datetime import datetime

import peer  # this machine's peer, exactly as guide one left it
from ride import (
    ALICE,
    BANK,
    CAB,
    CONCIERGE,
    Booking,
    Dispatch,
    Dropoff,
    Fare,
    Hold,
    Plan,
    Quote,
    Request,
    Seen,
    dollars,
    rkey,
    serve,
    when,
)

# Each company's own business: what it charges, how soon, in what, with which cars.
# The bid is all the concierge learns of it.
# readers: who may read the company's private repo. Hoot lets in the
# concierge it bids to, the bank that pays it, its car, and the rider; Jitney,
# which this story has lose, keeps its bids to the concierge and the bank.
OFFERS = {
    "hoot.lan": {
        "price": dollars(3400),
        "eta": 4,
        "car": "sedan",
        "cars": [CAB],
        "readers": [CONCIERGE, BANK, CAB, ALICE],
    },
    "jitney.lan": {
        "price": dollars(3700),
        "eta": 3,
        "car": "hatchback",
        "cars": [],
        "readers": [CONCIERGE, BANK],
    },
}


def company(domain: str):
    me = f"did:web:{domain}"
    offer = OFFERS[domain]

    def decide(seen: Seen, now: datetime) -> Plan:
        plan = Plan()
        plan.let("main", *offer["readers"])
        my_quotes = {ref.uri: (ref, q) for ref, q in seen.all(Quote, by=me)}

        # 1. open requests: a bid on each
        for request_ref, request in seen.all(Request, by=CONCIERGE):
            if now < when(request.bids_by):
                quote = Quote(
                    request=request_ref,
                    price=offer["price"],
                    eta_minutes=offer["eta"],
                    car=offer["car"],
                    created_at=peer.now(),
                )
                plan.write("main", rkey(request_ref), quote)

        # 2. a booking for one of its bids: the hold checked in the bank's repo, then a car
        for booking_ref, booking in seen.all(Booking, by=CONCIERGE):
            if booking.quote.uri not in my_quotes or not offer["cars"]:
                continue
            if seen.who(booking.hold) != BANK:
                continue  # a hold is only a hold in the bank's own repo
            hold = seen.get(booking.hold, Hold)
            price = my_quotes[booking.quote.uri][1].price
            if hold is None or hold.payee != me or hold.amount.value() < price.value():
                continue  # no such hold, not for this company, or too small: no car
            dispatch = Dispatch(
                booking=booking_ref, hold=booking.hold, car=offer["cars"][0], created_at=peer.now()
            )
            plan.write("main", rkey(booking_ref), dispatch)

        # 3. its car's dropoff: the fare, the price it bid
        bookings = {ref.uri: b for ref, b in seen.all(Booking, by=CONCIERGE)}
        for dispatch_ref, dispatch in seen.all(Dispatch, by=me):
            booking = bookings.get(dispatch.booking.uri)
            if booking is None:
                continue
            for dropoff_ref, dropoff in seen.all(Dropoff, by=dispatch.car):
                if dropoff.dispatch == dispatch_ref:
                    fare = Fare(
                        booking=dispatch.booking,
                        dropoff=dropoff_ref,
                        hold=dispatch.hold,
                        amount=my_quotes[booking.quote.uri][1].price,
                        created_at=peer.now(),
                    )
                    plan.write("main", rkey(dispatch_ref), fare)
        return plan

    return me, offer, decide


if __name__ == "__main__":
    me, offer, decide = company(peer.domain)
    asyncio.run(serve(peer, decide, "bidding on ride requests"))
