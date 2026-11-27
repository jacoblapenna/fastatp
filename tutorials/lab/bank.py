"""The bank: this machine's peer.py, plus a loop that holds and pays, by its own rules.

python bank.py bank.lan

It follows alice, the concierge and the ride companies, and writes, in its
own repo, each step that is missing:

1. Its own limits: the most it pays any one payee at once.
2. A request from the concierge to hold money: it checks alice's own
   allowance, the limits and the balance, then holds the money (a Hold) or
   says why not (a Refusal).
3. A payee's fare: it pays it from the hold and lets the rest go (a Settlement).
"""

import asyncio
from datetime import datetime

import peer  # this machine's peer, exactly as guide one left it
from ride import (
    ALICE,
    BANK,
    COMPANIES,
    CONCIERGE,
    Allowance,
    Authorize,
    Booking,
    Fare,
    Hold,
    Money,
    PayeeLimit,
    Refusal,
    Seen,
    Settlement,
    author,
    dollars,
    host,
    rkey,
    serve,
)

# The bank's own books. In a real bank these live in its database, not here.
BALANCES = {(ALICE, "checking"): dollars(25000)}
# The most each payee is paid at once, whoever asks. The bank publishes these
# as records, so payees and customers can see them; it checks its books.
LIMITS = {payee: dollars(10000) for payee in COMPANIES}


def check(seen: Seen, authorize_ref, authorize: Authorize) -> str | None:
    """Why the bank will not hold this money, or None if it will."""
    allowance = seen.get(authorize.allowance, Allowance)
    if allowance is None:
        return "no such allowance, or it changed since it was named"
    owner = author(authorize.allowance)
    if allowance.bank != BANK or allowance.agent != author(authorize_ref):
        return "the allowance does not let this agent spend at this bank"
    if authorize.amount.value() > allowance.per_ride.value():
        return f"{authorize.amount} is over the {allowance.per_ride} the allowance gives a ride"
    limit = LIMITS.get(authorize.payee)
    if limit is None or authorize.amount.value() > limit.value():
        return f"{host(authorize.payee)} is not a payee this bank pays that much"
    balance = BALANCES.get((owner, authorize.account))
    held = sum(
        (h.amount.value() for ref, h in seen.all(Hold, by=BANK) if not paid(seen, ref)), start=0
    )
    if balance is None or balance.value() - held < authorize.amount.value():
        return "not enough money in that account"
    return None


def paid(seen: Seen, hold_ref) -> bool:
    return any(s.hold == hold_ref for _, s in seen.all(Settlement, by=BANK))


def decide(seen: Seen, now: datetime) -> list[tuple[str, object]]:
    out = []
    # 1. its own limits, published once
    for payee, limit in LIMITS.items():
        out.append((host(payee), PayeeLimit(payee=payee, per_payment=limit, created_at=peer.now())))

    # 2. each request to hold money, answered once, either way
    answered = {h.authorize.uri for _, h in seen.all(Hold, by=BANK)}
    answered |= {r.authorize.uri for _, r in seen.all(Refusal, by=BANK)}
    for authorize_ref, authorize in seen.all(Authorize, by=CONCIERGE):
        key = rkey(authorize_ref)
        if authorize_ref.uri in answered:
            continue
        if (why := check(seen, authorize_ref, authorize)) is not None:
            out.append((key, Refusal(authorize=authorize_ref, reason=why, created_at=peer.now())))
        else:
            hold = Hold(
                authorize=authorize_ref,
                payee=authorize.payee,
                amount=authorize.amount,
                created_at=peer.now(),
            )
            out.append((key, hold))

    # 3. a fare from the payee of a hold: paid from it, the rest let go
    bookings = {ref.uri: b for ref, b in seen.all(Booking, by=CONCIERGE)}
    for company in COMPANIES:
        for fare_ref, fare in seen.all(Fare, by=company):
            booking = bookings.get(fare.booking.uri)
            if booking is None or author(booking.hold) != BANK:
                continue  # only a hold in this bank's own repo
            hold = seen.get(booking.hold, Hold)
            if not hold or hold.payee != company or fare.amount.value() > hold.amount.value():
                continue  # not a ride this bank held money for, for this company, at this size
            rest = Money(amount=hold.amount.amount - fare.amount.amount)  # both in cents
            out.append(
                (
                    rkey(booking.hold),
                    Settlement(
                        hold=booking.hold,
                        fare=fare_ref,
                        paid=fare.amount,
                        released=rest,
                        created_at=peer.now(),
                    ),
                )
            )
    return out


if __name__ == "__main__":
    asyncio.run(serve(peer, [ALICE, CONCIERGE, *COMPANIES], decide, "holding and paying"))
