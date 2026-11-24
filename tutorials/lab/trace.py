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
    if cue["to"] != PLAYER:  # an answer to a cue for someone else proves nothing about this one
        sys.exit(f"{machine}'s last answer names a cue addressed to {cue['to'] or 'nobody'}")

    print(f"asked:    {cue_uri}")
    print(f"          signed by {CONDUCTOR}, verified")
    print(f"          to {cue['to']}: {cue['action']} {cue['command']!r}")
    print(f"answered: at://{PLAYER}/{collection}/{rkey}")
    print(f"          signed by {PLAYER}, verified")
    print(f"          exit {result['code']}, for the cue above")


asyncio.run(main())
