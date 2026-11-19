"""Follow a peer's repo: checkpoint it, then tail its firehose, verifying everything.
python follow.py <did> <host>

<host> is the wire to use: alice.lan reaches alice over the LAN, alice's tunnel
address reaches alice through the WireGuard tunnel. Both come from alice's DID
document: the first is its PDS endpoint, the second is derived from the
WireGuard public key in its service entry. Nobody has to be told either one.
"""

import argparse
import asyncio
from urllib.parse import quote

import httpx
import lexicons  # noqa: F401  (registers Note/Reply so parse_record knows them)
import websockets
from dids import resolver

from fastatp import RepoMirror
from fastatp.exceptions import FirehoseErrorFrame, UnknownLexicon
from fastatp.lexicon import parse_record
from fastatp.repo.sync import decode_frame

args = argparse.ArgumentParser()
args.add_argument("did")
args.add_argument("host")
opts = args.parse_args()

# A tunnel address is IPv6, and an IPv6 literal goes in brackets in a URL.
HOST = f"[{opts.host}]" if ":" in opts.host else opts.host
BASE = f"{HOST}/xrpc/com.atproto.sync"


def show(record: dict) -> object:
    # A record whose type this script never imported is still a verified,
    # well-formed record. Show it raw instead of dying: supporting a lexicon is
    # the reader's choice, not a condition of following the repo.
    try:
        return parse_record(record)
    except UnknownLexicon:
        return f"(unknown type {record['$type']}) {record}"


DID_PARAM = quote(opts.did, safe="")  # the %3A inside a did:web must survive the query string


class Unreachable(Exception):
    """The host did not answer at all. Fatal on first contact, a retry later."""


async def checkpoint(mirror: RepoMirror) -> int:
    """getRepo: the whole current repo as one CAR, verified, adopted as our state."""
    # One request fetches a CAR file: every block the signed commit can reach
    # and nothing else. After this we hold the entire repo, not a feed of it.
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get(f"http://{BASE}.getRepo", params={"did": opts.did})
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            # A tunnel address with no tunnel open fails like an unplugged cable:
            # no answer at all, not a refusal. That silence is the point of WireGuard.
            raise Unreachable(
                f"no route to {opts.host}: {type(e).__name__} (is there a tunnel?)"
            ) from None
    if resp.status_code == 404:
        # The host answered, but serves no repo by that DID to us. A typo, a
        # channel DID from before that peer rebooted (the lab's channel is new
        # each boot), or a gated repo and a wire the doorman does not admit:
        # the doorman answers exactly as if the repo did not exist, so a
        # stranger cannot even learn that it does.
        raise SystemExit(f"{opts.host} serves no repo {opts.did} on this wire")
    resp.raise_for_status()
    # This is where the trust happens. load_checkpoint resolves the DID (through
    # our resolver) to find the public signing key, checks the commit's signature,
    # rebuilds the tree, and confirms every block's hash. Only then is it our state.
    await mirror.load_checkpoint(resp.content)
    # The server also says which firehose position this snapshot matches, so we
    # can subscribe from exactly there: no gap, no duplicate.
    seq = int(resp.headers["x-fastatp-seq"])
    print(
        f"checkpoint: rev {mirror.rev}, {len(mirror.entries)} records, verified; stream at seq {seq}"
    )
    for key in sorted(
        mirror.entries
    ):  # entries is the tree flattened: "collection/rkey" -> record hash
        collection, rkey = key.split("/", 1)
        print(f"   {key}: {show(mirror.get_record(collection, rkey))!r}")
    return seq


async def main() -> None:
    # A RepoMirror is a verifying copy of one repo. It needs the resolver to find
    # the public signing key. It never trusts the server it reads from, only the signature.
    mirror = RepoMirror(opts.did, resolver=resolver())
    try:
        cursor = await checkpoint(mirror)
    except Unreachable as e:
        raise SystemExit(str(e)) from None  # never reached the host at all: say so and stop
    while True:
        # subscribeRepos is a websocket that pushes one frame per commit. cursor=N
        # means "start after seq N": the server replays what we missed, then stays live.
        url = f"ws://{BASE}.subscribeRepos?did={DID_PARAM}&cursor={cursor}"
        try:
            async with websockets.connect(url) as ws:
                print(f"tailing {opts.did} after seq {cursor}, via {opts.host}")
                async for frame in ws:
                    try:
                        header, _body = decode_frame(
                            frame
                        )  # only to show the frame type and ops; the checking is below
                    except FirehoseErrorFrame as refusal:
                        print(
                            f"refused: {refusal}"
                        )  # the server closes with a reason, e.g. a cursor it cannot honour
                        return
                    # handle_frame verifies the frame (signature, hashes, that it builds on
                    # the rev we hold) and applies it. It returns records created or updated.
                    # Deletes are applied too, but there is no record to return for them.
                    records = await mirror.handle_frame(frame)
                    if mirror.resync_target is not None:
                        # A #sync frame announces a new root without a full tree. If the
                        # mirror cannot connect it to what it holds, it refuses to guess
                        # and we fetch a fresh checkpoint instead.
                        cursor = await checkpoint(mirror)
                        continue
                    print(f"seq {mirror.seq}: {header['t']} rev={mirror.rev} verified")
                    for item in records:
                        print(f"   {item['action']} {item['path']}: {show(item['record'])!r}")
                    for op in _body.get(
                        "ops", []
                    ):  # deletes carry no record, only the path that is gone
                        if op["action"] == "delete":
                            print(f"   delete {op['path']}")
        except (websockets.exceptions.WebSocketException, OSError) as e:
            # The peer went away: a restart, a crash, a pulled cable. The server
            # says 1012 (service restart) when it can. Our cursor may now mean
            # nothing to a fresh process, so we do not resume from it. We go back
            # to the start of the protocol: checkpoint, then tail. That is all an
            # outage costs a follower, one getRepo, because the repo rev is the
            # durable clock and the mirror already holds everything up to it.
            print(f"connection lost: {e}")
        while True:
            await asyncio.sleep(2)
            try:
                cursor = await checkpoint(mirror)
                break
            except Unreachable as e:
                print(f"{e}; retrying")  # a production follower would back off, not hammer


asyncio.run(main())
