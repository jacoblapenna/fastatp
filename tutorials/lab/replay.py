"""Read the show back from the conductor's repo, the way anyone could: python replay.py

No guide, no score file, no page: one checkpoint of the conductor's repo,
verified against its DID document, and what the records in it say.
"""

import asyncio
from collections import Counter

import httpx
from backstage import Control, Cue, Show
from dids import resolver

from fastatp import RepoMirror

CONDUCTOR = "did:web:conductor.stage"


async def main() -> None:
    mirror = RepoMirror(CONDUCTOR, resolver=resolver())
    async with httpx.AsyncClient(timeout=10) as http:
        response = await http.get(
            "http://conductor.stage/xrpc/com.atproto.sync.getRepo", params={"did": CONDUCTOR}
        )
    response.raise_for_status()
    await mirror.load_checkpoint(response.content)  # the signature and every hash, checked
    print(f"{CONDUCTOR}: {len(mirror.entries)} records, verified")

    show = mirror.get_record(Show.nsid, "self")  # which show is playing now
    print(f"the show now: {show['score']}")

    # Record keys are TIDs, so sorting the paths puts the cues in the order they were signed.
    cues = [
        mirror.get_record(*path.split("/"))
        for path in sorted(mirror.entries)
        if path.startswith(f"{Cue.nsid}/")
    ]
    mine = [cue for cue in cues if cue["show"] == show["id"]]
    print(f"{len(mine)} cues in this show, one per step, in the order they were signed:")
    for action, count in sorted(Counter(cue["action"] for cue in mine).items()):
        print(f"  {count:4d} {action}")
    first = mine[0]
    print(
        f"the first: step {first['step']}, a {first['action']}: {first['title'] or first['text']!r}"
    )

    presses = sum(1 for path in mirror.entries if path.startswith(f"{Control.nsid}/"))
    print(f"{presses} presses of the page's buttons, every show's, each one signed")


asyncio.run(main())
