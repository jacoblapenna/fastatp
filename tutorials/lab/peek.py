"""Try to read another party's private repos, from this machine: python peek.py <party>

Each party announces its private repos, signed, in its public repo: that
they exist, and that they are its. What is in them is another matter. This
asks each one, through the tunnel, the way any reader would, and says what
came back: the records, a refusal, or no answer at all.
"""

import asyncio
import socket
import sys

import httpx
from dids import resolver
from ride import Channel, host

from fastatp import RepoMirror
from fastatp.authz.wireguard import parse_wireguard_service

me = socket.gethostname()  # this machine: jitney.lan, concierge.lan, ...
party = f"did:web:{sys.argv[1].removesuffix('.lan')}.lan"


async def main() -> int:
    async with httpx.AsyncClient(timeout=3) as http:
        # the public repo: anyone may read it, and it holds only the announcements
        public = RepoMirror(party, resolver=resolver())
        answer = await http.get(
            f"http://{host(party)}/xrpc/com.atproto.sync.getRepo", params={"did": party}
        )
        await public.load_checkpoint(answer.content)
        repos = [
            public.get_record(*path.split("/"))["did"]
            for path in sorted(public.entries)
            if path.startswith(f"{Channel.nsid}/")
        ]
        print(f"{host(party)} announces {len(repos)} private repo(s); {me} asks each:")
        tunnel = parse_wireguard_service(await resolver()(party)).tunnel_ip
        read = 0
        for repo in repos:
            try:
                answer = await http.get(
                    f"http://[{tunnel}]/xrpc/com.atproto.sync.getRepo", params={"did": repo}
                )
            except httpx.HTTPError:
                print(f"  {repo}: no answer: no tunnel between {me} and {host(party)}")
                continue
            if answer.status_code != 200:
                print(f"  {repo}: refused ({answer.status_code}): no record lets {me} in")
                continue
            mirror = RepoMirror(repo, resolver=resolver())
            await mirror.load_checkpoint(answer.content)  # its signature and every hash, checked
            kinds = sorted({path.split("/")[0].rsplit(".", 1)[1] for path in mirror.entries})
            kinds = [k for k in kinds if k != "capability"]  # who is let in, not what it says
            print(f"  {repo}: let in, verified: {', '.join(kinds) or 'nothing written yet'}")
            read += 1
    print(f"{me} read {read} of {len(repos)}")
    return 0 if read else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
