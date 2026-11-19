"""Fetch one record with its proof and verify it offline-style:
python verify.py <did> <host> <collection> <rkey> [--tamper]

With --tamper, one byte of the record is changed after the fetch and before
the check, to show what a verifier does with bytes that are not what was signed.
"""

import asyncio
import sys

import httpx
import lexicons  # noqa: F401  (registers Note/Reply so the verified record comes back typed)
from dids import resolver

from fastatp import Peer
from fastatp.exceptions import VerificationError

did, host, collection, rkey = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
tamper = "--tamper" in sys.argv


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
    if tamper:
        # Change one byte, as a failing disk or a dishonest mirror might. The record
        # is the last block in the slice, so the slice's last byte is the record's.
        changed = bytearray(proof)
        changed[-1] ^= 0x01
        print(f"changed one byte of the record: {proof[-9:]!r} -> {bytes(changed[-9:])!r}")
        proof = bytes(changed)
    try:
        record = await Peer(resolver=resolver()).verify(proof, expected_signer=did)
    except VerificationError as error:
        # Nothing is returned for bytes that fail: there is no "mostly valid" record.
        print(f"refused: {error}")
        sys.exit(0 if tamper else 1)
    print(f"verified, signed by {did}: {record!r}")


asyncio.run(main())
