"""Watch the raw wire: python tail.py <host> [--cursor N]  (decodes frames, verifies nothing)"""

import argparse
import asyncio

import websockets

from fastatp.exceptions import FirehoseErrorFrame
from fastatp.repo.sync import decode_frame

# No RepoMirror, no resolver, no signature checks. This script shows what the
# bytes on the wire say and nothing more. follow.py is the one that checks.

args = argparse.ArgumentParser()
args.add_argument("host")
args.add_argument("--cursor", type=int, help="replay everything after this seq, then stay live")
opts = args.parse_args()

host = f"[{opts.host}]" if ":" in opts.host else opts.host  # an IPv6 literal goes in brackets
url = f"ws://{host}/xrpc/com.atproto.sync.subscribeRepos"
if opts.cursor is not None:
    url += f"?cursor={opts.cursor}"  # no cursor means "live only": nothing from before we connected


async def main() -> None:
    async with websockets.connect(url) as ws:
        print(f"on the wire at {url}")
        async for frame in ws:  # one frame per commit; the server pushes, we never poll
            try:
                header, body = decode_frame(frame)  # splits the binary frame into two dicts
            except FirehoseErrorFrame as e:
                print(f"error frame: {e}")  # the server's way of closing with a reason
                return
            kind = header.get("t")  # the frame type: #commit, #sync, ...
            summary = {k: v for k, v in body.items() if k not in ("blocks",)}
            if "blocks" in body:
                # blocks is the CAR payload, raw bytes. Show how big it is, not what is in it.
                summary["blocks"] = f"<{len(body['blocks'])} bytes of CAR>"
            print(f"{kind} {summary}")


asyncio.run(main())
