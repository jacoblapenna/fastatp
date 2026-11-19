"""Fetch one record with its proof and verify it offline-style:
python verify.py <did> <host> <collection> <rkey>
"""

import asyncio
import sys

import httpx
import lexicons  # noqa: F401  (registers Note/Reply so the verified record comes back typed)
from dids import resolver

from fastatp import Peer

did, host, collection, rkey = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]


async def main() -> None:
    # getRecord returns a proof slice, not just the record: the signed commit,
    # the tree nodes on the path from that commit down to this record, and the
    # record itself. That is enough to check one record without the rest of the repo.
    url = f"http://{host}/xrpc/com.atproto.sync.getRecord"
    proof = httpx.get(url, params={"did": did, "collection": collection, "rkey": rkey}).content
    print(f"proof slice: {len(proof)} bytes (signed commit + MST path + record)")
    # verify() resolves the DID to find the public signing key, checks the commit's
    # signature, walks the hashes down to the record, and raises if any step
    # fails or if the signer is not the DID we expected. The host we fetched
    # from plays no part in the trust. It could be a stranger's cache.
    record = await Peer(resolver=resolver()).verify(proof, expected_signer=did)
    print(f"verified, signed by {did}: {record!r}")


asyncio.run(main())
