"""Walk the ride back, as alice, and check every step: python custody.py

Starts from the bank's settlement and follows every strong ref back to
alice's own note. Each record is fetched from its owner's private repo,
through the tunnel, and checked twice: the repo's commit is signed by the
repo's key, and the record's hash is the one the step before it named. Whose
repo it is comes from the owner's signed announcement, not from a server.

It knows nothing about rides. A strong ref ({"uri", "cid"}) looks the same in
any record, so this is the walk for any work done this way. A step alice was
never let in to read is reported as sealed, not as a failure.
"""

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
from dids import resolver
from ride import ALICE, BANK, CAB, COMPANIES, CONCIERGE, Channel, host

from fastatp import RepoMirror
from fastatp.authz.wireguard import parse_wireguard_service
from fastatp.cid import CID

PARTIES = [ALICE, CONCIERGE, BANK, *COMPANIES, CAB]


def refs(value) -> Iterator[dict]:
    """Every strong ref inside a record, wherever it sits, in order."""
    if isinstance(value, dict):
        if (
            isinstance(value.get("uri"), str)
            and value["uri"].startswith("at://")
            and "cid" in value
        ):
            yield value
            return
        for inner in value.values():
            yield from refs(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from refs(inner)


def walk(start: dict, look) -> Iterator[tuple[int, dict, str, dict | None]]:
    """Depth first from one ref: (depth, ref, verdict, record) for each step.

    look(ref) -> (verdict, record): "verified", "sealed", or "changed"."""
    checked: set[tuple[str, str]] = set()

    def visit(ref: dict, depth: int) -> Iterator[tuple[int, dict, str, dict | None]]:
        # a step is a record AND its hash: the same address named with another
        # hash is another claim, and is checked on its own
        if (ref["uri"], ref["cid"]) in checked:
            yield depth, ref, "again", None
            return
        checked.add((ref["uri"], ref["cid"]))
        verdict, record = look(ref)
        yield depth, ref, verdict, record
        if verdict == "verified":
            for inner in refs(record):
                yield from visit(inner, depth + 1)

    yield from visit(start, 0)


async def main() -> int:
    async with httpx.AsyncClient(timeout=3) as http:
        # Whose is each private repo? Each party's public repo says, signed.
        owners: dict[str, str] = {}
        for party in PARTIES:
            mirror = RepoMirror(party, resolver=resolver())
            public = await http.get(
                f"http://{host(party)}/xrpc/com.atproto.sync.getRepo", params={"did": party}
            )
            await mirror.load_checkpoint(public.content)
            for path in mirror.entries:
                if path.startswith(f"{Channel.nsid}/"):
                    owners[mirror.get_record(*path.split("/"))["did"]] = party

        # Every private repo alice is let in to, read whole through the tunnel and
        # checked: the commit's signature against the repo's key, every hash.
        records: dict[str, tuple[str, dict]] = {}  # uri -> (cid, record)
        sealed: set[str] = set()
        for repo, party in owners.items():
            if party == ALICE:
                continue  # alice's own: alice knows what alice wrote (below)
            tunnel = parse_wireguard_service(await resolver()(party)).tunnel_ip
            try:
                answer = await http.get(
                    f"http://[{tunnel}]/xrpc/com.atproto.sync.getRepo", params={"did": repo}
                )
            except httpx.TransportError:
                sealed.add(repo)  # no tunnel to it: alice was never meant to reach it
                continue
            if answer.status_code == 404:
                sealed.add(repo)  # the doorman's no: no record lets alice in
                continue
            answer.raise_for_status()  # anything else is a fault, not a seal: the walk stops
            mirror = RepoMirror(repo, resolver=resolver())
            await mirror.load_checkpoint(answer.content)
            for path, cid in mirror.entries.items():  # each hash, from the verified tree
                records[f"at://{repo}/{path}"] = (cid.encode(), mirror.get_record(*path.split("/")))
        own = await http.get("http://127.0.0.1/mine")  # alice's own repos, from alice's peer
        own.raise_for_status()
        for repo in own.json():
            for r in repo["records"]:
                records[f"at://{repo['did']}/{r['path']}"] = (r["cid"], r["value"])

        def look(ref: dict) -> tuple[str, dict | None]:
            repo = ref["uri"].removeprefix("at://").split("/")[0]
            if ref["uri"] in records:
                cid, value = records[ref["uri"]]
                return ("verified", value) if cid == ref["cid"] else ("changed", None)
            return ("sealed", None) if repo in sealed else ("missing", None)

        # the newest settlement, by the time the bank signed into it
        settlements = sorted(
            (records[u][1].get("created_at", ""), u)
            for u in records
            if "/com.example.bank.settlement/" in u
        )
        if not settlements:
            print("no settlement yet: the ride isn't paid for")
            return 1
        newest = settlements[-1][1]
        start = {"uri": newest, "cid": records[newest][0]}
        print("the ride, walked back from the bank's settlement, as alice:")
        counts = {"verified": 0, "sealed": 0, "changed": 0, "missing": 0}
        photo = None
        for depth, ref, verdict, record in walk(start, look):
            repo, collection, _ = ref["uri"].removeprefix("at://").split("/")
            kind = collection.rsplit(".", 1)[1]
            who = host(owners.get(repo, repo))
            if verdict == "again":
                continue  # already checked above: a chain can meet itself
            counts[verdict] += 1
            mark = {"verified": "✓ signed, hash matches", "sealed": "· sealed: alice isn't let in"}
            line = mark.get(verdict, f"✗ {verdict.upper()}: not the record named")
            print(f"{'  ' * depth}{kind:<11} {who:<14} {line}  {ref['uri']}")
            if record and isinstance(record.get("photo"), dict):
                photo = (repo, owners.get(repo, repo), record["photo"])

        if photo is not None:  # the curb, fetched and checked against the hash its record names
            repo, party, blob = photo
            tunnel = parse_wireguard_service(await resolver()(party)).tunnel_ip
            answer = await http.get(
                f"http://[{tunnel}]/xrpc/com.atproto.sync.getBlob",
                params={"did": repo, "cid": blob["ref"]["$link"]},
            )
            answer.raise_for_status()
            data = answer.content
            if CID.for_raw(data).encode() == blob["ref"]["$link"]:
                Path("curb.svg").write_bytes(data)  # only a photo that checks out is shown
                print(
                    f"the curb photo: {len(data)} bytes, hash matches,"
                    " saved as curb.svg: is it your curb?"
                )
            else:
                counts["changed"] += 1
                print("the curb photo: ✗ HASH DIFFERS: not the photo the car signed, not shown")
        print(
            f"{counts['verified']} steps verified, {counts['sealed']} sealed from alice,"
            f" {counts['changed'] + counts['missing']} wrong"
        )
        return 0 if counts["changed"] + counts["missing"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
