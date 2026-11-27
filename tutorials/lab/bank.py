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
    Fare,
    Hold,
    Money,
    PayeeLimit,
    Plan,
    Refusal,
    Seen,
    Settlement,
    dollars,
    host,
    rkey,
    serve,
)

# The bank's own books. In a real bank these live in its database, not here.
BALANCES = {(ALICE, "checking"): dollars(25000)}
# The most each payee is paid at once, whoever asks. The bank checks its
# books; it also writes them as records, for the customer and the agent.
LIMITS = {payee: dollars(10000) for payee in COMPANIES}


def check(seen: Seen, authorize_ref, authorize: Authorize) -> str | None:
    """Why the bank will not hold this money, or None if it will."""
    allowance = seen.get(authorize.allowance, Allowance)
    if allowance is None:
        return "no such allowance, or it changed since it was named"
    owner = seen.who(authorize.allowance)
    if allowance.bank != BANK or allowance.agent != seen.who(authorize_ref):
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


def decide(seen: Seen, now: datetime) -> Plan:
    plan = Plan()
    # Everything private. "main", for the customer and the customer's agent:
    # its limits and its refusals. One repo per hold, made when it holds: the
    # customer, the agent, and the payee the agent named, let in at that
    # moment, so the payee can check the money is there. No other payee, and
    # no other customer, sees it.
    plan.let("main", ALICE, CONCIERGE)

    # 1. its own limits, written once
    for payee, limit in LIMITS.items():
        plan.write(
            "main", host(payee), PayeeLimit(payee=payee, per_payment=limit, created_at=peer.now())
        )

    # 2. each request to hold money, answered once, either way
    holds = {h.authorize.uri: (ref, h) for ref, h in seen.all(Hold, by=BANK)}
    refused = {r.authorize.uri for _, r in seen.all(Refusal, by=BANK)}
    for authorize_ref, authorize in seen.all(Authorize, by=CONCIERGE):
        key = rkey(authorize_ref)
        if authorize_ref.uri in holds:
            plan.let(f"ride-{key}", ALICE, CONCIERGE, holds[authorize_ref.uri][1].payee)
            continue
        if authorize_ref.uri in refused:
            continue
        if (why := check(seen, authorize_ref, authorize)) is not None:
            plan.write(
                "main", key, Refusal(authorize=authorize_ref, reason=why, created_at=peer.now())
            )
            continue
        hold = Hold(
            authorize=authorize_ref,
            payee=authorize.payee,
            amount=authorize.amount,
            created_at=peer.now(),
        )
        plan.let(f"ride-{key}", ALICE, CONCIERGE, authorize.payee)
        plan.write(f"ride-{key}", key, hold)

    # 3. a fare from the payee of a hold: paid from it, the rest let go
    for company in COMPANIES:
        for fare_ref, fare in seen.all(Fare, by=company):
            if seen.who(fare.hold) != BANK:
                continue  # only a hold in this bank's own repo
            hold = seen.get(fare.hold, Hold)
            if not hold or hold.payee != company or fare.amount.value() > hold.amount.value():
                continue  # not a hold for this company, or less than the fare
            rest = Money(amount=hold.amount.amount - fare.amount.amount)  # both in cents
            settlement = Settlement(
                hold=fare.hold,
                fare=fare_ref,
                paid=fare.amount,
                released=rest,
                created_at=peer.now(),
            )
            key = rkey(fare.hold)
            plan.let(f"ride-{key}", ALICE, CONCIERGE, company)
            plan.write(f"ride-{key}", key, settlement)
    return plan


if __name__ == "__main__":
    asyncio.run(serve(peer, decide, "holding and paying"))
