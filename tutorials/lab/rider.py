"""alice's machine in the ride: this machine's peer.py, plus one more local route.

python rider.py alice.lan

alice asks for a ride the way guide one wrote private notes: a note,
committed with /commit into alice's channel, which only alice's agent may
read. The one thing a note can't say is the money: which agent may have which
bank hold how much. That is a record of its own, an Allowance, signed into a
second private repo, one the bank and the agent may read and nobody else.
Like /commit, only this machine may call /allow.

    {"agent": "did:web:concierge.lan", "bank": "did:web:bank.lan",
     "account": "checking", "per_ride": 4000}         $40.00, in cents
"""

import asyncio

import peer  # this machine's peer, exactly as guide one left it
import ride
from pydantic import ValidationError
from ride import BANK, CONCIERGE, Allowance, Plan, dollars, serve
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastatp.tid import generate_tid


async def allow(request: Request) -> JSONResponse:
    if (denied := peer.local_only(request)) is not None:
        return denied
    body = await request.json()
    try:
        allowance = Allowance(
            bank=body["bank"],
            agent=body["agent"],
            account=body["account"],
            per_ride=dollars(int(body["per_ride"])),
            created_at=peer.now(),
        )
    except (KeyError, ValueError, ValidationError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    rkey = generate_tid()
    if ride.DESK is None:
        return JSONResponse({"error": "not ready yet"}, status_code=503)
    for_bank = await ride.DESK.repo("bank")
    await for_bank.put_record(Allowance.nsid, rkey, allowance)
    return JSONResponse({"uri": f"at://{for_bank.did}/{Allowance.nsid}/{rkey}"})


def decide(seen, now) -> Plan:
    """alice writes by hand; these are only who may read what alice writes."""
    plan = Plan()
    plan.let("main", CONCIERGE)  # alice's notes: for alice's agent
    plan.let("bank", BANK, CONCIERGE)  # alice's allowance: for the bank, and the agent it names
    return plan


if __name__ == "__main__":
    # nothing to follow and nothing to decide: alice writes by hand
    asyncio.run(
        serve(
            peer,
            decide,
            "alice's machine: /commit for notes, /allow for money",
            routes=[Route("/allow", allow, methods=["POST"])],
        )
    )
