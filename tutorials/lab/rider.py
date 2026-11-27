"""alice's machine in the ride: this machine's peer.py, plus one more local route.

python rider.py alice.lan

alice asks for a ride the way guide one wrote notes: a note, committed with
/commit. The one thing a note can't say is the money: which agent may have
which bank hold how much. That is a record of its own, an Allowance, signed
here with the same key as every note. Like /commit, only this machine may call it.

    {"agent": "did:web:concierge.lan", "bank": "did:web:bank.lan",
     "account": "checking", "per_ride": 4000}         $40.00, in cents
"""

import asyncio

import peer  # this machine's peer, exactly as guide one left it
from pydantic import ValidationError
from ride import Allowance, dollars, serve
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
    await peer.server.put_record(Allowance.nsid, rkey, allowance)
    return JSONResponse({"uri": f"at://{peer.identity.did}/{Allowance.nsid}/{rkey}"})


if __name__ == "__main__":
    # nothing to follow and nothing to decide: alice writes by hand
    asyncio.run(
        serve(
            peer,
            [],
            lambda seen, now: [],
            "alice's machine: /commit for notes, /allow for money",
            routes=[Route("/allow", allow, methods=["POST"])],
        )
    )
