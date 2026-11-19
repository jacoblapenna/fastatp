# Getting started: three machines on a LAN, on one computer

This is a hands-on walk through of what a FastATP peer *is*. You will run
three sovereign peers (alice, bob, eve) as three separate machines on a
private LAN, define your own record type, watch signed commits cross the
wire, verify them from another machine, and gate a private channel behind a
real WireGuard tunnel that admits bob and refuses eve. Everything here is
throwaway; `./lab.sh clean` removes it all.

This guide is for anyone who wants to run a FastATP peer. It assumes you
can use a terminal and have Docker installed, and nothing else. You will
not write Docker files or learn Docker; the three machines are a fixture.

Every command below runs against the fastatp in this repository, the exact
commit you have checked out: the lab image installs it from the tree, and
`lab/lab.sh test` plays the whole guide against it. Read the explanations
between the commands; they are the point.

> 🤖 **Coding agents:** you can run this whole guide yourself, headless, and
> read what happened. [AGENTS.md](AGENTS.md) shows how.

## What you are building

```text
                         the LAN  192.168.77.0/24
           (a private network inside Docker; your computer is its router)
  ════════════╤═════════════════════════╤═════════════════════════╤════════
              │                         │                         │
      ┌───────┴────────┐        ┌───────┴────────┐        ┌───────┴────────┐
      │   alice.lan    │        │    bob.lan     │        │    eve.lan     │
      │  192.168.77.11 │        │  192.168.77.12 │        │  192.168.77.13 │
      │    peer.py     │        │    peer.py     │        │    peer.py     │
      │ wg0 fd5a:…:8f31│        │ wg0 fd5a:…:d7c │        │ wg0 fd5a:…:21a4│
      └───────┬────────┘        └───────┬────────┘        └───────┬────────┘
              │                         │                         │
              └──── WireGuard tunnel ───┘                         ╳
                 opened by admission                      never handshakes

        (each wg0 address is derived from that machine's WireGuard key)
```

<!-- @demo machines alice bob eve -->
<!-- @demo start -->
<!-- @demo chapter 1 "A sovereign peer" -->
<!-- @demo blurb Three machines boot and mint identities. Alice's DID document, read from bob; one drop, one verify, and what the repo actually holds. -->

Three machines on one LAN, alice, bob and eve, each running the same peer.
In four parts:

1. **alice** publishes signed records to their own repo. Anyone on the LAN
   can read and verify them.
2. **bob** follows alice, verifying every commit, and answers from their own
   repo. Alice reboots and loses nothing.
3. **eve** reads alice's public repo like anyone else. Alice opens a
   private channel, admits bob, which opens a WireGuard tunnel to bob,
   and eve cannot even see that the channel exists. Alice evicts bob and
   bob's tunnel dies mid-session.
4. One screen shows all of it at once: every machine's view side by side,
   the whole picture none of them has, and the wire as an outsider sees it.

<!-- @demo end -->

The three machines are containers, but nothing in this guide depends on
that. Each one has its own hostname, its own address on the LAN, its own
disk, its own kernel network stack, and its own WireGuard interface. To
each other they are three Linux boxes on a network. Learning Docker is
not part of this tutorial; a six-line wrapper, `lab.sh`, is the only thing
you type.

<!-- @demo start -->

> 🔑 **Key concept: the words in this guide, one line each.**
>
> - **Peer**: one process with an identity of its own that hosts repos: its
>   own, and any others it registers, each with its own DID and signing key.
>   There is no client and no server; every participant is a peer.
> - **DID**: the permanent identifier. `did:web:alice.lan` means "the
>   document at `alice.lan/.well-known/did.json`".
> - **DID document**: public key plus service endpoints. What verifiers fetch.
>   Here it also carries the machine's WireGuard identity.
> - **Twin**: a second DID beside the main one, `did:web:alice.lan:<slug>`,
>   that serves the identity's key history. Only FastATP peers keep one.
> - **Pen**: the rotation secret, printed once when an identity is minted
>   and kept off the machine. It alone can move the identity to a new
>   signing key.
> - **Repo**: one signed tree of records per DID. Present state only.
> - **Commit**: the signed root of that tree, with a revision `rev`. Every
>   change is one: a create, an update, or a delete.
> - **Record**: one typed object in the repo, addressed as
>   `at://<did>/<collection>/<rkey>`. The *collection* is the record's
>   lexicon name, and the *rkey* (record key) is the part that is unique
>   within it. Together they are the record's path in the repo.
> - **CID**: the hash of a block's bytes, which is also its address. A
>   record, a tree node and a commit are each one block.
> - **Drop**: this guide's word for one write, creating a record or deleting
>   one, which is one signed commit. Not a protocol term; `/drop` is a route in
>   `peer.py`.
> - **Lexicon**: the schema language. You write your own; FastATP ships none.
> - **Firehose**: the WebSocket stream of commits (`subscribeRepos`). Each
>   frame on it is numbered by `seq`.
> - **Checkpoint**: the whole repo as one CAR file (`getRepo`).
> - **Mirror**: a verified local copy of someone else's repo (`RepoMirror`).
> - **Tunnel**: a WireGuard link. Inside it, the source address *is* the
>   caller's identity, because the kernel bound that address to a key.

Each machine runs one symmetric `peer.py`, which is a complete personal data server:
it mints or reloads an identity, serves that identity's DID document on
port 80, hosts its signed repo on the atproto sync surface
(`/xrpc/com.atproto.sync.*`), and takes local drops on a loopback-only
`/drop` route. That is the same shape as the peer you would run on your own domain, with
`alice.lan` standing in for the public hostname.

<!-- @demo end -->

## Setup

You need Docker with the compose plugin. On Linux that is Docker Engine;
on macOS and Windows it is Docker Desktop, and on Windows run the commands
from a WSL shell. Nothing else is installed on your computer: no Python,
no venv, no WireGuard tools. Everything runs inside the three machines.

```bash
docker compose version      # any v2
```

Then check that your Docker can create a WireGuard interface at all:

```bash
docker run --rm --cap-add NET_ADMIN alpine:3.20 \
  sh -c 'apk add -q wireguard-tools iproute2 && ip link add wg0 type wireguard && echo WIREGUARD_OK'
```

> ⚠️ **Important: WireGuard is a kernel feature, not a program.** The peer
> process only *configures* it. On Linux, containers share your kernel, so the probe is
> asking whether your kernel has the WireGuard module; every mainline
> kernel since 5.6 does. On macOS and Windows, Docker Desktop runs
> containers inside a small Linux VM, and the probe is asking about *that*
> kernel; current Docker Desktop releases include it. If the probe does not
> print `WIREGUARD_OK`, the machines will not boot, because each one brings
> up its tunnel interface before starting its peer. Fix Docker first.

Everything lives in one directory, `lab/`, next to this file:

```text
fastatp/                     the fastatp repository; the lab installs the package from here
└── tutorials/
    ├── GETTING_STARTED.md   this guide; the later guides build on it, one at a time
    ├── conductor/           Part 4's demo engine: the conductor and its page
    ├── tools/md_manifest.py turns this guide into the score the conductor plays
    └── lab/
        ├── lab.sh           the only command you run: up, down, logs, restart, clean, <machine> <cmd>
        ├── compose.yml      three machines on one LAN            (docker; you do not need to read it)
        ├── Dockerfile       python + wireguard-tools + fastatp   (docker; you do not need to read it)
        ├── entrypoint.sh    boots a machine: wg0 up, then peer.py (a few lines of shell)
        ├── lexicons.py      your record types
        ├── dids.py          how this LAN resolves did:web
        ├── peer.py          one sovereign peer
        ├── pair.py          point a machine's tunnel at a hub
        ├── follow.py        a verified reader
        ├── tail.py          the raw wire
        ├── verify.py        anyone, later, offline
        ├── blocks.py        what is actually in a repo
        ├── player.py        Part 4, backstage: this machine's part in the demo engine
        ├── backstage.py     Part 4, backstage: the demo engine's own records
        └── state/           created on first boot: alice.lan/, bob.lan/, eve.lan/
```

`state/<machine>/` is that machine's disk. It holds one private signing key,
one WireGuard key and one repo, and no machine can see another's. <!-- @demo terminal -->The eight
Python files are baked into every machine's image, so each box has the
scripts and `./lab.sh up` rebuilds them after you edit one.

The wrapper, in full:<!-- @demo /terminal -->

<!-- @demo skip -->
```bash
./lab.sh up                  # build the image and boot alice, bob and eve
./lab.sh logs alice          # what alice's process prints (Ctrl-C to stop watching)
./lab.sh alice <command...>  # run a command on alice's machine (curl, python follow.py, wg show, ...)
./lab.sh restart alice       # reboot alice's machine
./lab.sh conductor           # Part 4: boot the machines with the demo engine, on http://localhost:8080
./lab.sh tap <tcpdump args>  # watch the LAN from outside with tcpdump, as nobody (optional)
./lab.sh down                # stop the machines; state survives
./lab.sh clean               # stop the machines and delete all state
```

The eight Python files are the app's own code: what someone building on
FastATP writes, with the library underneath. <!-- @demo terminal -->Read them before you boot
anything. They are short.<!-- @demo /terminal -->

### `lexicons.py`: your own record types

`lexicons.py` declares the two record types this guide uses, `Note` and
`Reply`, as Pydantic models: their fields, their limits, and the namespace
they belong to.

```python
"""Our own record types. FastATP ships none of these: the namespace is ours."""

from typing import Annotated

from fastatp import Lexicon
from fastatp.lex.types import Datetime, MaxGraphemes

# A Lexicon is a record schema. Subclassing it does two jobs at once. The class
# becomes a validator: building a Note that breaks a rule raises, before anything
# is signed. And importing this module registers the nsid, so any script that
# imports lexicons can turn raw bytes off the wire back into a Note or a Reply.


class Note(Lexicon):
    # The nsid is the record type's name, reversed-domain style so two projects
    # never collide. Every Note in the repo is filed under this collection.
    nsid = "com.example.tutorial.note"
    # 300 grapheme clusters, not 300 Python characters: an emoji built from
    # several code points counts as one. This is a lexicon rule, checked on write.
    text: Annotated[str, MaxGraphemes(300)]
    created_at: Datetime  # must parse as an ISO-8601 timestamp; the type checks that


class Reply(Lexicon):
    nsid = "com.example.tutorial.reply"
    to: str  # the at:// URI of the note being answered; a plain string, nothing is looked up
    text: Annotated[str, MaxGraphemes(300)]
    created_at: Datetime
```

Two things to notice. The namespace is yours (`com.example.tutorial.*`),
and `MaxGraphemes(300)` is a real lexicon constraint, counted in grapheme
clusters, not Python `len()`. It is enforced before anything is signed<!-- @demo terminal -->; you
will see it refuse a record in "Things to try"<!-- @demo /terminal -->.

### `dids.py`: how this LAN resolves did:web

`dids.py` is how every script on this LAN turns a `did:web` into its
document: the shipped resolver, handed a plain-`http` client because the
lab has no certificate authority.

```python
"""did:web resolution on this LAN.

Production verifiers fetch https://<domain>/.well-known/did.json. This LAN has
no certificate authority, so the same WebResolver is handed an HTTP client that
speaks plain http to the same URL. That is the only stand-in in this lab.
"""

import httpx

from fastatp import WebResolver


class _PlainHTTP(httpx.AsyncHTTPTransport):
    # httpx lets you swap the layer that actually sends a request. This is the
    # normal sender with one change: whatever URL the resolver built, send it
    # over http instead of https. The resolver never knows and never cares.
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.url = request.url.copy_with(scheme="http")
        return await super().handle_async_request(request)


def resolver() -> WebResolver:
    # Every script in the lab calls this instead of WebResolver() directly, so
    # the stand-in lives in exactly one place. WebResolver() with no arguments
    # is the production behaviour.
    #
    # Why https matters there. A signature proves "signed by key K". The DID
    # document is what turns that into "signed by alice": it is where a reader
    # first learns that K is alice's key. If that one fetch goes over plain
    # http, anyone between the reader and alice (a bad router, a laptop on the
    # same Wi-Fi) can answer first with a document naming a public key whose private half
    # they hold. From
    # then on the reader verifies the impostor's records and rejects alice's,
    # and every check passes honestly. TLS makes that fetch untamperable. This
    # LAN has no one in between, which is the only reason the shortcut is safe.
    return WebResolver(client=httpx.AsyncClient(transport=_PlainHTTP()))
```

This is the one stand-in in the lab, so look at it closely. `WebResolver`
is the production resolver: given `did:web:alice.lan` it fetches
`https://alice.lan/.well-known/did.json`, exactly as a relay or a stranger
would. Our LAN has hostnames and routing but no certificate authority, so
the resolver is handed an HTTP client that makes the same request over
plain `http`. Every other part of resolution (the URL, the document
checks, the caching rules) is the shipped code. The chain it serves beside
the document, and why, is a key concept in Part 1, where the document is
first read.


### `peer.py`: one sovereign peer

`peer.py` is the whole peer: it mints or reloads an identity, serves the
DID document, hosts the public repo and a gated channel, and takes drops,
admissions and evictions on loopback-only routes.

```python
"""One sovereign peer, one machine: python peer.py <domain>

Mints (or reloads) did:web:<domain> in ./<domain>/, serves its DID document on
port 80, hosts its repo on the atproto sync surface, and takes local drops on
/drop. Its WireGuard key is published in the DID document, and admitting a
peer to the gated channel opens that peer's tunnel.
"""

import asyncio
import ipaddress
import socket
import subprocess
import sys
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import uvicorn
from dids import resolver
from lexicons import Note, Reply
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastatp import DIDServer, Identity, Repo
from fastatp.authz import WgToolInterface, build_wireguard_service
from fastatp.authz.capability import Capability, live_capabilities
from fastatp.authz.wireguard import overlay_address, parse_wireguard_service
from fastatp.crypto import encode_did_key, generate_keypair
from fastatp.did.document import build_did_document
from fastatp.tid import generate_tid

print = partial(print, flush=True)

domain = sys.argv[1]  # this machine's hostname on the LAN; the DID is did:web:<domain>

# --- this machine's tunnel: wg0 was brought up before we started -------------
# entrypoint.sh created wg0 and loaded its private key before Python ran. This
# process never sees that private key. It asks the kernel for the matching
# public key, because that is what goes in the DID document: "to reach me
# privately, talk to this key". The address inside the tunnel is not chosen
# and not configured anywhere: it is derived from the key, by this machine
# and by anyone who reads the document, so the two can never disagree.
wg = WgToolInterface("wg0")  # a thin wrapper over the `wg set` command: add_peer and remove_peer
wg_public_key = subprocess.run(
    ["wg", "show", "wg0", "public-key"], capture_output=True, text=True, check=True
).stdout.strip()
tunnel_ip = overlay_address(wg_public_key)  # the same function entrypoint.sh used to put it on wg0

resolve = resolver()


async def boot() -> Starlette:
    """Identity, server, DID document, gated channel: the parts that sign or
    serve are awaited, so they live in one async function. Everything it
    makes is kept in module globals for the routes below to use."""
    global identity, server, channel, twin_slug

    # --- identity: who this machine is ----------------------------------------
    # bootstrap() looks in ./<domain>/ for an identity. First boot: it generates a
    # signing key and a rotation key, writes the signing key to signing.key, and
    # keeps the rotation key only in memory so it can be shown once. Every later
    # boot: it reloads the same signing key, so the DID and every old signature
    # stay valid. The directory is the identity; back it up and you keep the DID.
    identity = await Identity.bootstrap(domain=domain, state_dir=Path(domain))
    if identity.rotation_private_key is not None:
        # Only set on first boot. The rotation key (the "pen") is the one that can
        # replace the signing key if it ever leaks. It is never written to disk
        # here; printing it is the lab's stand-in for copying it to cold storage.
        print(f"[{domain}] minted {identity.did}")
        print(f"[{domain}] ROTATION PEN (shown once; a throwaway here, cold storage for real):")
        pen = identity.rotation_private_key.private_numbers().private_value
        print(f"[{domain}]   {pen.to_bytes(32, 'big').hex()}")
    else:
        print(f"[{domain}] reloaded {identity.did}")

    # The server hosts this identity's repo on the standard atproto sync routes
    # (getRepo, getRecord, subscribeRepos). Everything it serves was signed with
    # signing.key, so readers verify it themselves and never have to trust the server.
    # It gets a resolver because the doorman and the verifier inside it look up DIDs.
    server = identity.create_server(resolver=resolve)

    # --- the DID document: public signing key, PDS endpoint, tunnel identity ----
    # The "twin" is a second did:web under this domain, named by a hash, that
    # FastATP keeps alongside the pretty one. It carries the rotation history so a
    # verifier can tell a legitimate key rotation from a new occupant of the domain.
    # You do not need it for this guide; DIDServer serves it because it is standard.
    twin_slug = identity.chain.twin_did.rsplit(":", 1)[-1]

    # --- a gated channel beside the public repo (part 3) ------------------------
    # A second repo with its own key, and therefore its own DID. A did:key needs no
    # hostname: the DID *is* the public key, so there is nothing to resolve.
    # public=False puts it behind the doorman. On every read the doorman takes the
    # request's source address, derives from each live Capability record's
    # WireGuard key the one address that key may send from, and looks for a
    # match. A LAN address matches nothing and is refused; a tunnel address
    # matches the DID that was admitted with that key. Nothing else is consulted.
    #
    # Both the key and the repo exist only in this process. The key is never
    # written to disk, and Repo.create() with no storage keeps its blocks in
    # memory. So a reboot makes a brand-new channel with a new DID and no
    # records: every private note and every admission is gone, and readers must
    # be admitted again. That is fine for a Part 3 demo. A real peer would save
    # the key next to signing.key and give the repo a SqliteStorage, exactly as
    # the public repo already has.
    channel_key = generate_keypair("k256")
    channel = await server.register(
        await Repo.create(encode_did_key(channel_key.public_key()), channel_key), public=False
    )

    dids = DIDServer(domain=domain, resolver=did_resolver, chain_resolver=chain_resolver)
    return Starlette(
        routes=[
            # Four routes of our own, then two sets the library ships. One process,
            # one port, serving the DID document and the repo side by side.
            Route("/whoami", whoami),
            Route("/drop", drop, methods=["POST"]),
            Route("/admit", admit, methods=["POST"]),
            Route("/evict", evict, methods=["POST"]),
            *server.app.routes,  # /xrpc/com.atproto.sync.*: the standard atproto sync surface
            *dids.app.routes,  # /.well-known/did.json, /<slug>/did.json, /<slug>/chain.json
        ]
    )


# A DID document is a DID's public business card. It is the first thing anyone
# fetches (http://<domain>/.well-known/did.json) and it answers three questions:
# which public key verifies this repo's signatures, where the repo is served,
# and, our addition, where the private tunnel is and which WireGuard public
# key answers there. Only public halves go in it; the private halves never leave
# the disk. No address is in it either: the key is enough to derive one.
def document():
    return build_did_document(
        did=identity.did,
        signing_key=identity.signer.public_key,  # the public half only
        also_known_as=[identity.chain.twin_did],
        pds_endpoint=f"http://{domain}",  # where the repo is served; https in production
        extra_services=[build_wireguard_service(wg_public_key, endpoint=f"{domain}:51820")],
    )


async def did_resolver(slug: str):
    # DIDServer asks this for every did.json request: "what lives at <slug>?"
    # "" is /.well-known/did.json, the main DID. Anything unknown is a 404.
    if slug == "":
        return document()
    if slug == twin_slug:
        return identity.twin_document()
    return None


async def chain_resolver(slug: str):
    return identity.chain.to_json() if slug == twin_slug else None


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def local_only(request: Request) -> JSONResponse | None:
    # The admin routes below have no login. The process holds the keys, so the
    # only question is "is the caller on this machine?", and a loopback source
    # address is the simplest way to ask it.
    #
    # Illustrative, not robust. A loopback source proves the caller is a process
    # on this machine, nothing more: any proxy or port mapping in front of the
    # peer makes every caller look local, and any local process, a browser
    # included, qualifies. Do not carry this check to a real host as is.
    #
    # The server listens on IPv4 and IPv6 (see main), so a loopback caller
    # shows up as 127.0.0.1 or as ::1.
    if request.client is None or not is_loopback(request.client.host):
        return JSONResponse({"error": "local only"}, status_code=403)
    return None


def is_loopback(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return address.is_loopback or (mapped is not None and mapped.is_loopback)


async def drop(request: Request) -> JSONResponse:
    """Write one signed commit to one repo. Only this machine may call it.

    {"repo": "public" | "channel", "text": "..."}        create a note
    {"repo": ..., "to": "at://...", "text": "..."}       create a reply
    {"repo": ..., "delete": "<collection>/<rkey>"}       delete a record
    """
    if (denied := local_only(request)) is not None:  # := assigns and tests in one step
        return denied
    body = await request.json()
    # Two repos live in this process, and the caller names which one, every
    # time. A write that has to guess its repo is a write that can land in the
    # wrong one, so there is no default.
    repos = {"public": server, "channel": channel}
    if body.get("repo") not in repos:
        return JSONResponse({"error": "repo must be 'public' or 'channel'"}, status_code=400)
    target = repos[body["repo"]]
    if "delete" in body:  # a delete is a write too
        collection, rkey = body["delete"].split("/")
        # delete_record writes a commit whose only op is "this path is gone". The
        # bytes may linger in blocks.sqlite until the next commit's garbage
        # collection, but the signed tree no longer reaches them.
        event = await target.delete_record(collection, rkey)
        return JSONResponse(
            {"deleted": f"{collection}/{rkey}", "rev": event.rev, "seq": target.seq}
        )
    rkey = generate_tid()  # a TID is a timestamp-based id, so rkeys sort in creation order
    if "to" in body:  # a reply names the note it answers; "to" is that note's at:// URI
        record = Reply(to=body["to"], text=body["text"], created_at=now())
    else:
        record = Note(text=body["text"], created_at=now())
    # Validation happened inside Note(...) / Reply(...): text over 300 graphemes
    # raised there, and nothing below ran. That is "enforced before signing".
    #
    # put_record is the whole write path in one call: encode the record to
    # DAG-CBOR, update the tree, sign a new commit, take the next seq, push a
    # frame to every subscriber, persist the new blocks. It is awaited because
    # signing may be slow when the pen is a hardware key; here it is not.
    event = await target.put_record(record.nsid, rkey, record)
    return JSONResponse(
        {
            "uri": f"at://{target.repo.did}/{record.nsid}/{rkey}",  # the record's permanent address
            "cid": event.commit_cid.encode(),  # hash of the new commit; changes on every write
            "rev": event.rev,  # the commit's own id, a TID; the repo's durable clock
            "seq": target.seq,  # position in this repo's firehose; readers resume from it
        }
    )


async def admit(request: Request) -> JSONResponse:
    """Admit a DID to the gated channel: read its tunnel identity from its DID
    document, write a capability record, open the tunnel. The record is the
    signed decision; the kernel peer is what enforces it."""
    if (denied := local_only(request)) is not None:
        return denied
    body = await request.json()
    # Step 1, who is this? Fetch their DID document, the same way anyone on the
    # LAN would, and read the WireGuard service out of it: their tunnel public
    # key. Their document is the right source for that key: nobody else can say
    # which public key is theirs. The address that key may send from is derived
    # from it, by us and by the kernel alike.
    info = parse_wireguard_service(await resolve(body["did"]))
    # Step 2, write it down. A Capability is just a record in the channel that
    # says "<did> may subscribe", and carries the key it was admitted with. It
    # is signed and part of the repo's history, so admitting and evicting are
    # both visible commits. The key is what the doorman keys on: a request
    # arriving from the address that key derives is this DID.
    cap = Capability(subject=body["did"], actions=["subscribe"], wg_public_key=info.public_key)
    await channel.put_record(Capability.nsid, generate_tid(), cap)
    # Step 3, open the door. Until this line the kernel drops their handshakes
    # unanswered. After it, their key is accepted, and only from their tunnel address.
    wg.add_peer(info.public_key, info.allowed_ips)
    return JSONResponse(
        {"admitted": body["did"], "tunnel_ip": info.tunnel_ip, "channel": channel.did}
    )


async def evict(request: Request) -> JSONResponse:
    """Evict a DID: delete its capability record, close its tunnel."""
    if (denied := local_only(request)) is not None:
        return denied
    body = await request.json()
    # live_capabilities walks the channel's current records and returns, per
    # subject, the one capability still in force. A deleted capability is gone
    # from the tree, so it does not come back.
    live = live_capabilities(channel.repo).get(body["did"])
    if live is None:
        return JSONResponse({"error": f"{body['did']} holds no live capability"}, status_code=404)
    rkey, cap = live
    # The same two steps as admit, undone in the same order: the decision
    # first, then the enforcement. Removing the peer also
    # ends any session in flight. Their next packet is dropped unanswered.
    await channel.delete_record(Capability.nsid, rkey)
    wg.remove_peer(cap.wg_public_key)
    return JSONResponse({"evicted": body["did"], "deleted": f"{Capability.nsid}/{rkey}"})


async def whoami(request: Request) -> JSONResponse:
    # A lab convenience, not an atproto route. The channel's DID is a did:key
    # made at boot, so nothing on disk or in any document names it; this is
    # how Part 3 reads it, and how it reads this machine's tunnel address. Not
    # loopback-gated because nothing here is secret: the DID is in the
    # hostname, the address is derived from a published key, and knowing the
    # channel's DID gets a stranger nothing past the doorman.
    return JSONResponse(
        {"did": identity.did, "channel": channel.did, "seq": server.seq, "tunnel_ip": tunnel_ip}
    )


def listen(family: int, address: str) -> socket.socket:
    """One listening socket on port 80 for one address family."""
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)  # v4 has its own socket below
    sock.bind((address, 80))
    sock.listen(128)
    return sock


async def main() -> None:
    app = await boot()
    # Bind every interface, in both address families: the LAN is IPv4 and the
    # tunnel is IPv6, and both must land here, on the same routes. Public and
    # private are told apart by where a request comes from, not by which URL
    # it asks for. Binding wg0 directly matters: a proxy in between would hide
    # the source address from the doorman.
    print(f"[{domain}] serving on http://{domain} (LAN) and http://[{tunnel_ip}] (tunnel)")
    sockets = [listen(socket.AF_INET, "0.0.0.0"), listen(socket.AF_INET6, "::")]
    await uvicorn.Server(uvicorn.Config(app, port=80, log_level="warning")).serve(sockets=sockets)


if __name__ == "__main__":
    asyncio.run(main())
```

<!-- @demo terminal -->Read it top to bottom once.<!-- @demo /terminal --> The sequence is: this machine's tunnel identity
read off `wg0`, then, inside `boot()` because minting and registering are
awaited, an identity from a state directory, a server over that identity's
repo, a DID document that publishes the public half of the signing key plus
the PDS endpoint and the tunnel identity, and a gated side channel; then
three loopback-only admin routes. Nothing in it authenticates anyone,
because the process *is* the keyholder.

`/admit` is worth a second look. It takes a DID and nothing else. It
resolves that DID's document, reads the `WireGuardFirehose` service out of
it, writes one `Capability` record into the channel naming the DID and its
tunnel key, and tells the kernel to accept that key at the one address the
key derives.
`/evict` deletes the record and removes the kernel peer. The record is the
decision, signed and kept; the kernel peer is the enforcement, and it is
the only thing that lets a packet through. The two are written in that
order.

### `pair.py`: point a machine's tunnel at a hub

`pair.py` points this machine's tunnel at a hub, using nothing but the
hub's DID document.

```python
"""Point this machine's tunnel at a hub, using nothing but the hub's DID document:
python pair.py <hub-did>

This is not a request and not a subscribe. It changes this machine's own
kernel and sends nothing the hub will answer. The ask ("admit my DID") happens
out of band; the hub's /admit is the other half; follow.py through the tunnel
is the subscribe. FastATP's WireGuard helpers cover the hub's half
(add_peer, remove_peer, the reconciler) and the DID-document service. The hub
needs no endpoint, because WireGuard learns a peer's address from its
handshake. The reader is the asymmetric side: it must know where to send the
first packet and must keep knocking, so this one `wg set` is plain WireGuard.
"""

import asyncio
import subprocess
import sys

from dids import resolver

from fastatp.authz.wireguard import parse_wireguard_service

hub = sys.argv[
    1
]  # a DID such as did:web:alice.lan, not an address; the address comes from the document


async def main() -> None:
    # Fetch the hub's DID document, the same request anyone on the LAN can make,
    # and read the WireGuard service out of it: the public key that answers and
    # the UDP endpoint to send to. The tunnel address that key is allowed to use
    # is not in the document: it is derived from the key, here, by the same
    # function the hub used to put it on its own interface.
    info = parse_wireguard_service(await resolver()(hub))
    # Hand those to the kernel. This is the whole "client side" of a WireGuard
    # tunnel, four facts:
    #   peer <key>                trust this key
    #   endpoint <host>:51820     send to it here
    #   allowed-ips <addr>/128    packets from this key may only claim the one
    #                             address its key derives
    #   persistent-keepalive 5    knock every 5 s, so the tunnel comes up on its
    #                             own the moment the hub admits us
    subprocess.run(
        [
            "wg",
            "set",
            "wg0",
            "peer",
            info.public_key,
            "endpoint",
            info.endpoint,
            "allowed-ips",
            info.allowed_ips,
            "persistent-keepalive",
            "5",
        ],
        check=True,
    )
    print(
        f"tunnel peer set: {hub} is {info.public_key} at {info.endpoint}, tunnel ip {info.tunnel_ip}"
    )
    # WireGuard is silent to strangers. Until the hub adds our key on its side,
    # our knocks get no reply at all, not even a refusal.
    print("nothing will handshake until the hub admits this machine")


asyncio.run(main())
```

A device that wants to read alice's private channel needs to know where
alice's tunnel is and which WireGuard public key answers there. Both are in
alice's DID document.
`pair.py` configures this machine's `wg0` with that one peer. It does not
ask alice for anything, and alice does not learn that it happened. No
handshake can complete until alice adds this machine's key on alice's side,
which is what `/admit` does.

### `follow.py`: a verified reader

`follow.py` is a reader that trusts nothing it has not checked. It fetches a
repo as one checkpoint, verifies the commit's signature against the DID
document, then subscribes to the firehose and verifies every commit as it
arrives. It is what bob and eve run in Parts 2 and 3.

```python
"""Follow a peer's repo: checkpoint it, then tail its firehose, verifying everything.
python follow.py <did> <host>

<host> is the wire to use: alice.lan reaches alice over the LAN, alice's tunnel
address reaches alice through the WireGuard tunnel. Both come from alice's DID
document: the first is its PDS endpoint, the second is derived from the
WireGuard key in its service entry. Nobody has to be told either one.
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
        # channel DID from before the hub rebooted (the lab's channel is new
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
```

### `tail.py`: the raw wire

`tail.py` is the opposite: it prints the firehose frames as they arrive and
verifies nothing, so you can see what the wire says before anything checks
it.

```python
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
```

### `verify.py`: anyone, later, offline

`verify.py` fetches one record with its proof, the signed commit and the
tree path down to the record, and checks it with nothing but the DID
document. Anyone can run it, later, with no connection to the author.

```python
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
```

### `blocks.py`: what is actually in a repo

`blocks.py` opens a repo's SQLite file read-only and decodes every block in
it: the records, the tree nodes and the commit, each stored under the hash
of its bytes.

```python
"""What is actually in a repo: python blocks.py <state-dir>

Every block in blocks.sqlite, decoded. A repo holds three kinds of block and
nothing else: one signed commit, the tree nodes, and the records. All of it
is DAG-CBOR, a binary encoding of the same data model JSON uses. None of it
is encrypted; the only secret in the directory is signing.key.
"""

import sqlite3
import sys

from fastatp import dagcbor, datamodel
from fastatp.cid import CID

# Open read-only so a running peer is never disturbed. The whole store is one
# table: cid (the hash of the bytes) -> data (the bytes). The hash is the key,
# which is what "content-addressed" means.
db = sqlite3.connect(f"file:{sys.argv[1]}/blocks.sqlite?mode=ro", uri=True)
rows = db.execute("SELECT cid, data FROM blocks").fetchall()
print(f"{len(rows)} blocks")
raw = b""
for cid, data in rows:
    obj = dagcbor.decode(bytes(data))  # every block decodes to a plain dict
    # Three kinds of block, told apart by shape alone:
    if "sig" in obj:  # the commit: the only signed block; it names the tree root and a rev
        kind = f"commit     rev={obj['rev']} did={obj['did']}"
    elif "$type" in obj:  # a record: the same dict a Note or Reply validated, nothing added
        kind = f"record     {datamodel.to_json(obj)}"
        raw = bytes(data)  # keep one to show its stored form at the end
    else:  # a tree node: sorted entries pointing (by hash) at records and child nodes
        kind = f"tree node  {len(obj.get('e', []))} entries"
    print(f"  {CID.decode(bytes(cid)).encode()[:16]}...  {len(data):4d} bytes  {kind}")
if raw:
    print(f"one record, as stored: {raw[:56]!r}...")  # binary, but the field names are right there
```

### The runner, for the curious

You do not need to read `compose.yml` or the `Dockerfile`. What they do:
build one image (its base is named in `lab/Dockerfile`) with `wireguard-tools`,
`iproute2`, `curl`, `tcpdump`, the dependencies the repo's `uv.lock` pins,
hashes checked, and the fastatp from this tree; create a private network
`192.168.77.0/24`; start three machines on it with fixed addresses and the
hostnames `alice.lan`, `bob.lan`, `eve.lan`; give each the one Linux
capability (`NET_ADMIN`) needed to configure its own network interfaces;
and mount `state/<machine>` as its disk. `entrypoint.sh` runs at boot on
each machine: generate a WireGuard key if there is none, create `wg0` with
that key and the address the key derives, then start `peer.py`. No ports
are published to your computer and nothing listens on it, except in Part 4,
where the demo engine's conductor serves one page on `127.0.0.1:8080`. It
talks to the machines on a second, separate network, so the LAN carries only
the guide's traffic, and its image adds a Node build of the page. A last service,
`tap`, is not a machine: it runs `tcpdump` attached to the LAN's switch.

<!-- @demo start -->
<!-- @demo chapter 1 "A sovereign peer" -->

## Part 1: alice, a sovereign peer
<!-- @demo collapse eve -->

<!-- @demo terminal -->Boot the LAN:<!-- @demo /terminal -->

```bash
cd lab
./lab.sh up
```

<!-- @demo terminal -->The first run builds the image, which takes a minute. Then:<!-- @demo /terminal -->

```text
 Container fastatp-lab-alice-1  Healthy
 Container fastatp-lab-bob-1  Healthy
 Container fastatp-lab-eve-1  Healthy
```

All three machines booted and all three minted an identity. Look at
alice's:

```bash
./lab.sh logs alice
```

```json
[alice.lan] minted did:web:alice.lan
[alice.lan] ROTATION PEN (shown once; a throwaway here, cold storage for real):
[alice.lan]   d2375303b9c1c96d2c4f346bd876496b811318e225e169550aaf60e92e62c297
[alice.lan] serving on http://alice.lan (LAN) and http://[fd5a:7a7f:9c3e:0:5c0e:7a19:2bd4:8f31] (tunnel)
```

The pen is the rotation secret. It was printed once and is not on disk.
With it, alice can move to a new signing key and prove they are the same
identity; without it, nobody can, including whoever gets alice's
disk. <!-- @demo terminal -->Ctrl-C to stop watching the log; the machine keeps running.

Look at what minting created:<!-- @demo /terminal -->

<!-- @demo run alice: ls -la alice.lan -->
```bash
ls -la state/alice.lan/
```

| File | What it is |
| --- | --- |
| `signing.key` | The private signing key. Mode 0600. The only secret that signs. |
| `wg.key` | The machine's WireGuard private key. Mode 0600. Its tunnel identity. |
| `identity.json` | DID, domain, curve. |
| `chain.json` | The continuity chain: a self-certifying twin DID and its rotation history. Only FastATP peers use it. |
| `blocks.sqlite` | The repo: every record and tree node, by content hash, plus the head (the latest commit) and the firehose position, so a reboot resumes where it left off. |

Two of these are secrets, `signing.key` and `wg.key`, and neither ever
leaves the machine. Nothing else in the directory is one.

Now read alice's DID document the way any verifier would, from a different
machine, over the LAN:

```bash
./lab.sh bob sh -c 'curl -s alice.lan/.well-known/did.json | python -m json.tool'
```

```json
{
    "@context": ["https://www.w3.org/ns/did/v1", "https://w3id.org/security/multikey/v1"],
    "id": "did:web:alice.lan",
    "alsoKnownAs": ["did:web:alice.lan:broycfibbyie3tzs7wmzs2nqtkwzbdfn"],
    "verificationMethod": [
        {
            "id": "did:web:alice.lan#atproto",
            "type": "Multikey",
            "controller": "did:web:alice.lan",
            "publicKeyMultibase": "zQ3shpMgtFpiZgXQForwAztGx9sRJGzxXeGMpSkcpHj7Cvv1d"
        }
    ],
    "rotationKeys": [],
    "service": [
        {
            "id": "#atproto_pds",
            "type": "AtprotoPersonalDataServer",
            "serviceEndpoint": "http://alice.lan"
        },
        {
            "id": "#wireguard",
            "type": "WireGuardFirehose",
            "serviceEndpoint": "wg://alice.lan:51820?pubkey=Xz4BX2dBPJSHACFSjvNabZY57FhQjafxm5hIeWy0YwA%3D"
        }
    ]
}
```

The `verificationMethod` with fragment `#atproto` is alice's public signing
key in Multikey form. `alsoKnownAs` names the twin. The first service is where alice's repo lives, the second
is alice's tunnel: the public WireGuard key and the address it answers on.
The address alice uses *inside* the tunnel is not in the document, because
anyone who has the key can derive it, and the kernel on both ends does.
Publishing the WireGuard *public* key is the point; it lets anyone pair
with alice, and pairing grants nothing.

> 🔑 **Key concept: a DID is a hostname you control.** `did:web:alice.lan`
> means nothing more than "fetch `alice.lan/.well-known/did.json`". The
> identity is permanent only as long as you control that name, so in
> production it is a domain you own, served over HTTPS, and the document is
> a static file there. Everything a stranger needs to verify you, or to
> reach your tunnel, is in that one file. `.lan` names work here because
> the lab provides DNS for them.

<!-- @demo point bob.terminal diagram -->

> 🔑 **Key concept: taking over a FastATP identity needs the domain *and* the
> pen.** Plain did:web has one weakness: whoever controls `alice.lan` can
> publish a new public key, and to every reader that looks like alice rotating on
> purpose. FastATP adds a chain. Beside `did:web:alice.lan` the peer keeps a
> twin, `did:web:alice.lan:<slug>`, whose slug is the hash of the first DID
> document, and the twin serves `chain.json`: every DID document the identity
> has had (its keys and endpoints, never its records), each new one signed by
> the previous one's rotation key, the pen printed once at boot and never kept
> on the server. A reader that checks the chain
> (`ContinuityResolver`, which wraps `WebResolver`) refuses any served public
> key that is not the chain's head. So a thief with the domain but not the pen
> cannot extend the chain, and their key fails closed. A thief with the pen
> but not the domain cannot publish a page, and alice rotates to a fresh pen
> while they watch. Only both together are alice. Two limits: ordinary atproto
> resolvers ignore the twin, so they still trust the domain alone, and this
> lab's `dids.py` uses the plain `WebResolver`, so the chain is served but not
> checked. `ContinuityResolver(strict=True)` on a real host refuses any DID
> without one.

<!-- @demo point bob.terminal -->

Drop the first record. `/drop` only answers on alice's own loopback, so the
command runs *on alice's machine*:

```bash
./lab.sh alice curl -s -X POST localhost/drop -H 'content-type: application/json' \
  -d '{"repo":"public","text":"hello, LAN"}'
```

```json
{"uri":"at://did:web:alice.lan/com.example.tutorial.note/3mz23h6bv4dmd",
 "cid":"bafyreifoptzpoxh2j5unvodufj6szfkedvrhsrl7u2upvk5shspca5kfwu","rev":"3mz23h6bwy242","seq":3}
```

<!-- @demo save rkey 3mz23h6bv4dmd -->

Four things happened inside the process: a `Note` was validated, written
into the tree, the new root was signed with `signing.key` into a commit
with revision `3mz23h6bwy242`, and a frame went out on the firehose as
sequence number 3. The `uri` is the record's permanent address.

<!-- @demo point alice.repo -->

> 🔑 **Key concept: a lexicon is a contract that can grow.** A record's
> `$type` names its lexicon, and that is the whole agreement between a
> writer and a reader: these fields, these limits, this namespace. Each
> side chooses what to honour. A reader that imports `lexicons.py` gets
> typed `Note` and `Reply` objects. A reader that never heard of them still
> receives every record, verified, as plain data, and `follow.py` shows how
> to carry on when a type is unknown. Changes that only add are backward
> compatible on their own: a writer who adds a `mood` field to notes
> tomorrow breaks no reader running today, because unknown fields are kept
> and passed through, not refused. Removing a field, renaming one, or
> tightening a limit is a new contract, and the honest move is a new nsid.
> A subscriber supports the lexicons it chooses, all of them, some of them,
> or none; the repo does not care, and the signatures hold either way.

<!-- @demo point alice.repo -->

Prove that the route really is local. The same call from bob's machine:

```bash
./lab.sh bob curl -s -X POST alice.lan/drop -H 'content-type: application/json' -d '{"repo":"public","text":"nope"}'
```

```json
{"error":"local only"}
```

> 🔑 **Key concept: the write path is the key.** The private signing key is a
> file on alice's machine and nothing else can produce a valid commit. So
> the only thing `/drop` protects is *who may ask alice's process to sign*,
> and the answer is "this machine", enforced by listening on loopback. In
> production you reach it over your own VPN or from the box itself. There
> is no login because there is nothing to log in to.

<!-- @demo point alice.terminal bob.terminal -->

### Inside the repo

`ls -la` showed the repo as one SQLite file. It is not a folder of JSON
files, and it is worth seeing what it is instead. `blocks.py` opens the
file read-only and decodes every block in it:

```bash
./lab.sh alice python blocks.py alice.lan
```

```text
3 blocks
  bafyreibm5yhvrug...    90 bytes  record     {'text': 'hello, LAN', '$type': 'com.example.tutorial.note', 'created_at': '2026-10-29T21:58:06.378398Z'}
  bafyreigygxirlnn...   100 bytes  tree node  1 entries
  bafyreia6nbnicxz...   172 bytes  commit     rev=3mz2b56hcz64m did=did:web:alice.lan
one record, as stored: b'\xa3dtextjhello, LANe$typex\x19com.example.tutorial.notejcreat'...
```

Three blocks, and there are only ever three kinds. The record is your
`Note`, encoded as DAG-CBOR: a binary form of the same data model JSON
uses, which is why the decoded block is exactly the JSON you dropped and
the raw bytes still read `hello, LAN`. The tree node is a leaf of the
Merkle search tree that maps the record's path to the record's hash. The
commit names the tree's root hash, the revision, the DID, and carries the
signature. Each block's address is the hash of its bytes, and the commit's
address is the CID you saw in the drop response. <!-- @demo terminal -->Drawn as what points to
what, with every arrow a hash:<!-- @demo /terminal -->

```text
signing.key ──signs──▶ ┌────────────────────────────────────────────┐
                       │ commit      bafyreia6nbnicxz...            │
                       │ did:web:alice.lan, rev 3mz2b56hcz64m, sig  │
                       │ data: hash of the root node ───────────────┼──┐
                       └────────────────────────────────────────────┘  │
                       ┌────────────────────────────────────────────┐◀─┘
                       │ tree node   bafyreigygxirlnn...            │
                       │ note/3mz2b56gzgpqz: hash of the record ────┼──┐
                       └────────────────────────────────────────────┘  │
                       ┌────────────────────────────────────────────┐◀─┘
                       │ record      bafyreibm5yhvrug...            │
                       │ {"text": "hello, LAN", "$type": ...}       │
                       └────────────────────────────────────────────┘
```

Follow the hashes from the record up. The record's bytes determine its CID. The tree node
contains that CID, so the node's bytes, and its CID, depend on the record.
The commit contains the root node's CID and is signed. Change one byte of
the record and all three addresses change, and the signature no longer
matches. That is the whole trick, and every record you add is one more
leaf under the same root.

> 🔑 **Key concept: the repo is a content-addressed block store, and none
> of it is encrypted.** Every record sits in `blocks.sqlite` as readable
> bytes. Anyone who gets the disk, the file, or a CAR export has every
> record, which is fine, because the records are public. The only secret
> in the directory is `signing.key`, and what it protects is not reading
> but writing: without it, nobody can produce a commit that verifies as
> alice's. In production, keep private data in a gated repo and the disk
> encrypted like any other disk.

<!-- @demo point alice.repo -->

Ask alice's sync surface what it hosts, from eve, the way a relay (a
server that mirrors many repos) would:

```bash
./lab.sh eve sh -c 'curl -s alice.lan/xrpc/com.atproto.sync.listRepos | python -m json.tool'
./lab.sh eve curl -s 'alice.lan/xrpc/com.atproto.sync.getLatestCommit?did=did:web:alice.lan'
```

```json
{"repos": [{"did": "did:web:alice.lan", "head": "bafyreifoptz...", "rev": "3mz23h6bwy242", "active": true}]}
{"cid":"bafyreifoptzpoxh2j5unvodufj6szfkedvrhsrl7u2upvk5shspca5kfwu","rev":"3mz23h6bwy242"}
```

Fetch one record *with its proof* and verify it as a stranger would, on
bob's machine. `verify.py` takes four arguments, and three of them are the
parts of the record's URI. The drop response above returned
`at://did:web:alice.lan/com.example.tutorial.note/3mz23h6bv4dmd`, which
reads as `at://<did>/<collection>/<rkey>`:

| Argument | Meaning | From the URI above |
| --- | --- | --- |
| `did:web:alice.lan` | whose repo, and whose signature to expect | `<did>` |
| `alice.lan` | which host to fetch from; any mirror would do | not in the URI |
| `com.example.tutorial.note` | the collection, which is the lexicon name | `<collection>` |
| `3mz23h6bv4dmd` | the rkey, unique within that collection | `<rkey>` |

<!-- @demo terminal -->Your rkey will differ, because it is a timestamp. Copy it from your own
drop response:<!-- @demo /terminal -->

```bash
./lab.sh bob python verify.py did:web:alice.lan alice.lan com.example.tutorial.note 3mz23h6bv4dmd
```

```text
proof slice: 534 bytes (signed commit + MST path + record)
verified, signed by did:web:alice.lan: Note(text='hello, LAN', created_at='2026-10-29T20:16:19.303514Z')
```

Those 534 bytes are the whole product. They contain the signed commit, the
tree path from the root to this record, and the record itself. Bob's
machine resolved alice's DID document over the LAN, took the public key out of
it, and checked the signature. Anyone holding those bytes plus that
document can do the same with no connection to alice, now or in ten years.
That is a proof slice, and it can ride any transport.

Now the wire itself, unverified, replayed from the beginning:

```bash
./lab.sh bob python tail.py alice.lan --cursor 0
```

```text
on the wire at ws://alice.lan/xrpc/com.atproto.sync.subscribeRepos?cursor=0
#identity {'did': 'did:web:alice.lan', 'seq': 1, 'time': '2026-10-29T20:16:04.065030Z'}
#account {'did': 'did:web:alice.lan', 'seq': 2, 'time': '2026-10-29T20:16:04.065064Z', 'active': True}
#commit {'ops': [{'cid': CID('bafyreidf3vy...'), 'path': 'com.example.tutorial.note/3mz23h6bv4dmd', 'action': 'create'}],
         'rev': '3mz23h6bwy242', 'seq': 3, 'repo': 'did:web:alice.lan', 'since': '...', 'blocks': '<... bytes of CAR>', ...}
```

Frames 1 and 2 announce the identity and that the account is active.
Relays ignore commits from accounts they never saw activate. Frame 3 is
your note: the ops say what changed, `since` names the previous revision,
and `blocks` carries the record plus just enough tree nodes for a verifier
to check the new root. The frame is long<!-- @demo terminal -->, so the listing above elides the
rest<!-- @demo /terminal -->: `commit` is the new commit's CID, `prevData` is the tree root before
this write, `blobs` is empty because notes carry no media, and `rebase` and
`tooBig` are always false here. <!-- @demo terminal -->Leave `tail.py` running in this terminal,
open a second one, and drop another note as alice; it appears live.<!-- @demo /terminal -->

`tail.py` verifies nothing. It shows you the bytes and trusts them. Never
build on it. <!-- @demo terminal -->Ctrl-C it.<!-- @demo /terminal -->

<!-- @demo stop bob -->

<!-- @demo chapter 2 "A verified follower" -->
<!-- @demo blurb Bob follows alice, checkpoint then tail, and replies from bob's own repo. Alice deletes a note, reboots, and loses nothing. -->

## Part 2: bob, a verified follower

Bob is a second, independent machine with their own key, repo, and DID
document. Have bob follow alice over the LAN. The second argument is the
*wire* to use: `alice.lan` is alice's LAN address.

```bash
./lab.sh bob python follow.py did:web:alice.lan alice.lan
```

```text
checkpoint: rev 3mz23h6bwy242, 1 records, verified; stream at seq 3
   com.example.tutorial.note/3mz23h6bv4dmd: Note(text='hello, LAN', created_at='2026-10-29T20:16:19.303514Z')
tailing did:web:alice.lan after seq 3, via alice.lan
```

<!-- @demo terminal -->Read `follow.py` again with this output next to
it.<!-- @demo /terminal --> A follower does two things, in order:

1. **Checkpoint.** `getRepo` returns alice's whole repo as one CAR. The
   mirror resolves alice's DID document, checks the commit signature
   against alice's key, rebuilds the tree, and only then adopts the records.
   The response header `x-fastatp-seq` says where the stream was when the
   snapshot was taken.
2. **Tail.** Subscribe with `cursor=<that seq>`. The server replays
   anything after it, then streams live. Every `#commit` is verified the
   same way: signature, then the ops must reproduce the signed root exactly.

A mirror cannot start from the live stream alone. A commit's ops only make
sense against the state before it, so checkpoint first is not an
optimization, it is the protocol. <!-- @demo terminal -->Leave bob's follow running.<!-- @demo /terminal -->

Drop two more notes as alice<!-- @demo terminal -->, in a second terminal<!-- @demo /terminal -->:

```bash
./lab.sh alice curl -s -X POST localhost/drop -H 'content-type: application/json' -d '{"repo":"public","text":"second note"}'
./lab.sh alice curl -s -X POST localhost/drop -H 'content-type: application/json' -d '{"repo":"public","text":"third note"}'
```

<!-- @demo save rkey 3mz2b56vjjiqz from 1 -->
<!-- @demo save rkey 3mz23hzl5mgmd from 2 -->

On bob's `follow.py` screen they arrive verified and parsed:

<!-- @demo point bob.terminal -->

```text
seq 4: #commit rev=3mz23hyixdp42 verified
   create com.example.tutorial.note/3mz23hyin3ymd: Note(text='second note', created_at='2026-10-29T20:16:46.787667Z')
seq 5: #commit rev=3mz23hzlhyz42 verified
   create com.example.tutorial.note/3mz23hzl5mgmd: Note(text='third note', created_at='2026-10-29T20:16:47.918683Z')
```

<!-- @demo terminal -->If you left `tail.py` from Part 1 running, it shows the same two commits
as raw `#commit` frames, each with `since` naming the rev before it. Same
two events, two layers: `tail.py` prints what the wire said, `follow.py`
prints what it could prove. Your revs, rkeys and times will differ from
the listings here, because every one of them is a timestamp.<!-- @demo /terminal -->

Now the other direction. Bob answers in *their own* repo; a reply is a record
that names what it answers. Use the `uri` of the third note:

```bash
./lab.sh bob curl -s -X POST localhost/drop -H 'content-type: application/json' \
  -d '{"repo":"public","to":"at://did:web:alice.lan/com.example.tutorial.note/3mz23hzl5mgmd","text":"bob here"}'
```

And alice follows bob:

```bash
./lab.sh alice python follow.py did:web:bob.lan bob.lan
```

```text
checkpoint: rev 3mz23ib6rjwcy, 1 records, verified; stream at seq 3
   com.example.tutorial.reply/3mz23ib6pp3tk: Reply(to='at://did:web:alice.lan/com.example.tutorial.note/3mz23hzl5mgmd', text='bob here', ...)
tailing did:web:bob.lan after seq 3, via bob.lan
```

That is a conversation: two repos, each written only by its own key, each
read by the other through an outbound subscription. Nobody logged in,
nobody held a connection open waiting, and either side can be verified by
a third party from the records alone. <!-- @demo terminal -->Ctrl-C both follows.<!-- @demo /terminal -->

<!-- @demo stop bob -->
<!-- @demo stop alice -->

### Alice deletes a note

A delete is a write. Delete the second note by collection and rkey, the
tail of its URI<!-- @demo terminal -->, with bob's follow of alice started again and left
running in another terminal<!-- @demo /terminal -->:

```bash
./lab.sh bob python follow.py did:web:alice.lan alice.lan
```

```bash
./lab.sh alice curl -s -X POST localhost/drop -H 'content-type: application/json' \
  -d '{"repo":"public","delete":"com.example.tutorial.note/3mz2b56vjjiqz"}'
```

```text
{"deleted":"com.example.tutorial.note/3mz2b56vjjiqz","rev":"3mz2b5d2k4m4m","seq":6}
```

<!-- @demo terminal -->On bob's screen:<!-- @demo /terminal -->

```text
checkpoint: rev 3mz2b56z6py4m, 3 records, verified; stream at seq 5
   com.example.tutorial.note/3mz2b56gzgpqz: Note(text='hello, LAN', ...)
   com.example.tutorial.note/3mz2b56vjjiqz: Note(text='second note', ...)
   com.example.tutorial.note/3mz2b56z4spqz: Note(text='third note', ...)
tailing did:web:alice.lan after seq 5, via alice.lan
seq 6: #commit rev=3mz2b5d2k4m4m verified
   delete com.example.tutorial.note/3mz2b56vjjiqz
```

The deletion arrived as a commit like any other: a new `rev`, a new
sequence number, a signature bob verified, and an op that names the path
that is gone and carries no record. Bob's mirror dropped the record. Now
look at alice's store again:

<!-- @demo point bob.terminal alice.repo -->

```bash
./lab.sh alice python blocks.py alice.lan
```

```text
4 blocks
  bafyreibm5yhvrug...    90 bytes  record     {'text': 'hello, LAN', ...}
  bafyreieper5pbqn...    90 bytes  record     {'text': 'third note', ...}
  bafyreihgibuqnfk...   160 bytes  tree node  2 entries
  bafyreif32p4eyum...   172 bytes  commit     rev=3mz2b5d2k4m4m did=did:web:alice.lan
```

Before the delete there were three records, a tree node and a commit.
After it there are two records, a *different* tree node and a *different*
commit. The second note's block is gone, and so are the old tree node and
the old commit, because nothing reachable from the new commit refers to
them and every commit ends by discarding unreachable blocks. <!-- @demo terminal -->Deleting the
same URI again returns a 500 and alice's log says
`RepoError: no record at com.example.tutorial.note/3mz2b56vjjiqz`.<!-- @demo /terminal -->

<!-- @demo point alice.repo -->

```text
before the delete                              after the delete

commit  bafyreibmn4bnr2x...  rev 3mz2b56z6py4m    commit  bafyreif32p4eyum...  rev 3mz2b5d2k4m4m   NEW
└─ tree node, 3 entries                           └─ tree node  bafyreihgibuqnfk..., 2 entries     NEW
   ├─ note/3mz2b56gzgpqz  bafyreibm5yhvrug...        ├─ note/3mz2b56gzgpqz  bafyreibm5yhvrug...   same block
   ├─ note/3mz2b56vjjiqz  "second note"              │
   └─ note/3mz2b56z4spqz  bafyreieper5pbqn...        └─ note/3mz2b56z4spqz  bafyreieper5pbqn...   same block
```

The two surviving records kept their CIDs, because their bytes did not
change. Everything on the path from the removed leaf to the root was
rebuilt and re-signed.

> 🔑 **Key concept: a delete is a commit.** You cannot remove a record by
> removing its bytes, because the tree node above it holds its hash and
> the commit holds the tree's. Removing it rewrites the path from that leaf
> to the root, produces a new root hash, and that root has to be signed
> and announced like any other change. That is what makes deletion
> verifiable: a follower checks that applying "delete this path" to the
> tree it had reproduces the root alice signed, and refuses anything else.
> It also means a repo has no history. After the commit, nothing in it
> remembers the record existed; the frame bob received is the only trace.
> If you need a record of the deletion itself, write one, which is what the
> reconciler's `Revocation` record is for.

<!-- @demo point bob.terminal alice.repo -->

<!-- @demo terminal -->One honest footnote. The block is gone from the store, but
`grep -a -c "second note" state/alice.lan/blocks.sqlite-wal` still finds
the bytes in SQLite's write-ahead log until the database recycles those
pages. Deleting from a database is not shredding a disk; that is true of
every database and has nothing to do with the protocol.<!-- @demo /terminal -->

### Reboot alice

```bash
./lab.sh restart alice
./lab.sh logs alice
```

<!-- @demo wait 5 -->

```json
[alice.lan] reloaded did:web:alice.lan
[alice.lan] serving on http://alice.lan (LAN) and http://[fd5a:7a7f:9c3e:0:5c0e:7a19:2bd4:8f31] (tunnel)
```

No new pen, same DID, same WireGuard key, and the repo is where it was:
bob's follow, still running, lost its connection when alice went down and
came back on its own to the same two notes at the same `rev` as before.
The repo is durable<!-- @demo terminal -->, and the same is true of
`./lab.sh down` followed by `./lab.sh up`<!-- @demo /terminal -->.

<!-- @demo point bob.terminal -->

The sequence numbers carry on where they were too<!-- @demo terminal -->
(`tail.py alice.lan --cursor 0` after the reboot shows them, with two new
announcement frames on top)<!-- @demo /terminal -->. The stream position is
stored with the repo, in the same file as its blocks, so a reboot does not
start the count over and a follower that kept its cursor can resume from
it. The repo revision is still the durable clock; the seq is the stream's
place in it.

Look at how bob's follow recovered. Stopping alice's process closes every
open websocket with code 1012, "service restart"; no follower can avoid
seeing that. `follow.py` printed `connection lost` and then did the simplest
safe thing: waited two seconds, took a fresh checkpoint, and subscribed
again from the seq that checkpoint reported. It could have resumed from its
old cursor instead, since alice kept the count, and a production follower
would, with backoff between retries and a limit on patience. The cost of
the simple habit is one `getRepo`, and the mirror already held every record
up to the rev that checkpoint returned, so nothing was re-verified that had
been verified before.

<!-- @demo point bob.terminal -->
<!-- @demo stop bob -->

<!-- @demo chapter 3 "The private side" -->
<!-- @demo blurb Eve reads the public repo like anyone, and the channel is absent over the LAN. Bob pairs with alice's tunnel and knocks, unanswered. -->

## Part 3: eve, and the private side

<!-- @demo expand eve -->

Eve is just another machine. First, confirm what "public" means:

```bash
./lab.sh eve python follow.py did:web:alice.lan alice.lan
```

It works. Alice's main repo is alice's public persona, and a public repo is
readable by anyone who can reach alice, by protocol. There is no setting
that changes that. Privacy in FastATP is a *different repo*, not a flag on
a record. <!-- @demo terminal -->Ctrl-C it.<!-- @demo /terminal -->

<!-- @demo stop eve -->

> 🔑 **Key concept: the words for the private side.**
>
> - **Channel**: this guide's name for alice's private repo: a second repo
>   with its own `did:key` identity, hosted by the same process.
> - **Gated repo**: a repo registered with `public=False`. It is served
>   only inside a tunnel, and to everyone else it does not exist.
> - **Doorman**: the check a gated repo runs on every caller: which wire
>   did this connection arrive on, and whose key does that wire belong to.
> - **Hub**: the peer that hosts a gated repo and decides who may read it.
> - **Capability**: a signed record in the gated repo that says who may
>   read it. Writing one is admission; deleting it is eviction.

`peer.py` registered a second repo beside the public one, with
`public=False`: the channel, with a fresh `did:key` identity, hosted by the
same process. Find its DID:

```bash
./lab.sh alice curl -s localhost/whoami
```

```json
{"did":"did:web:alice.lan","channel":"did:key:zQ3shrSrkzQW7sV64uuQdoCbFd3TYxm8sDTouDjqkRa6pKRWh","seq":8,"tunnel_ip":"fd5a:7a7f:9c3e:0:5c0e:7a19:2bd4:8f31"}
```

<!-- @demo terminal -->Put that channel DID in a shell variable, and alice's tunnel address in
another. The `sed` commands below pull them out of the JSON for you, so
nothing has to be copied by hand; `echo` them to check: a bare
`did:key:...`, and an IPv6 address starting `fd5a:`.<!-- @demo /terminal --> The channel is new
every time alice boots<!-- @demo terminal -->, so re-run this after any restart of alice<!-- @demo /terminal -->. Then,
with the channel DID in `$CH` and alice's tunnel address in `$AT`, try to
read the channel over the LAN, as bob and as eve:

```bash
CH=$(./lab.sh alice curl -s localhost/whoami | sed 's/.*"channel":"\([^"]*\)".*/\1/')
AT=$(./lab.sh alice curl -s localhost/whoami | sed 's/.*"tunnel_ip":"\([^"]*\)".*/\1/')
echo "$CH" "$AT"
./lab.sh bob python follow.py "$CH" alice.lan
./lab.sh eve python follow.py "$CH" alice.lan
```

```text
alice.lan serves no repo did:key:zQ3shrSrkz... on this wire
```

Both get the same answer a stranger gets for a repo alice does not host at
all: not refused, absent. A gated repo identifies callers by the address
they arrive from *inside a tunnel*, and a LAN address is not one. The
doorman answers as if there were nothing to see, so nobody outside a tunnel
can even learn that the channel exists. The only way to read a gated repo
is through WireGuard.

> 🔑 **Key concept: private means a different repo behind a tunnel.** A public
> repo is readable by anyone who can reach the port, by protocol; there is
> no private flag on a record. Privacy is a second repo, registered with
> `public=False`, whose doorman only trusts a source address that WireGuard
> bound to a key. Outside a tunnel every caller is nobody.

<!-- @demo point bob.terminal eve.terminal -->

That gate is the only thing FastATP adds to what atproto already is:

> 🔑 **Key concept: in atproto, all of your data is public.** Every record
> in an atproto repo is readable by anyone, and the only thing that hides a
> transfer is the TLS on the connection. Private data is on atproto's
> roadmap, not in the protocol. FastATP keeps that rule for public repos
> and adds one thing: a repo whose gate is "which network did you arrive
> from". You do not have to learn WireGuard to use it safely. If you
> already have a VPN, a WireGuard mesh, Tailscale, or your office network,
> put the peer on it and write capability records naming the callers' VPN
> addresses. The gated repo is then public to the VPN, governed by the
> VPN's own access rules, and invisible to the rest of the world. That is
> this lab with the LAN replaced by your VPN. What `peer.py` adds in this
> part, opening a tunnel per admission, is the same idea with the VPN
> built in; drop the `wg.add_peer` line and the record alone is the gate.

<!-- @demo point diagram -->

### Bob pairs

<!-- @demo open diagram -->

Bob points their tunnel at alice. Everything bob needs is in alice's DID document:

```bash
./lab.sh bob python pair.py did:web:alice.lan
./lab.sh bob wg show wg0
```

```text
tunnel peer set: did:web:alice.lan is Xz4BX2dBPJSHACFSjvNabZY57FhQjafxm5hIeWy0YwA= at alice.lan:51820, tunnel ip fd5a:7a7f:9c3e:0:5c0e:7a19:2bd4:8f31
nothing will handshake until the hub admits this machine

interface: wg0
  public key: X3BOLJ7vKtSJJEXDlr7Cl2tr9Sgxae5x0hsF+D7bY18=
  listening port: 51820

peer: Xz4BX2dBPJSHACFSjvNabZY57FhQjafxm5hIeWy0YwA=
  endpoint: 192.168.77.11:51820
  allowed ips: fd5a:7a7f:9c3e:0:5c0e:7a19:2bd4:8f31/128
  transfer: 0 B received, 148 B sent
  persistent keepalive: every 5 seconds
```

The `allowed ips` line is the address bob derived from alice's key, the
same one alice put on `wg0` at boot and the same one `$AT` holds. Bob is
sending handshake initiations every few seconds and alice is dropping them
on the floor, because alice's `wg0` has no peers. Try the tunnel:

<!-- @demo point bob.tunnel -->

```bash
./lab.sh bob python follow.py "$CH" "$AT"
```

```text
no route to fd5a:7a7f:9c3e:0:5c0e:7a19:2bd4:8f31: ConnectTimeout (is there a tunnel?)
```

Not refused: unreachable. Before admission, alice's private side does not
exist on the network as far as bob can tell.

> 🔑 **Key concept: the subscriber's side is yours to build.** FastATP
> ships no pairing method for the reader and no protocol for asking to be
> admitted. That is deliberate, for now. The library owns the hub's half,
> where enforcement lives, and leaves how a reader finds a hub, asks for
> access, and gets an answer to the app builder. A bulletin board of DIDs,
> an invite link that carries one, a form on a website, a message in some
> other app: any of these works, because the whole ask is one sentence
> said outside the protocol, "I am knocking at your tunnel, and this is my
> DID." Everything the hub needs to answer is already published under that
> DID, and answering is writing one record. The market for that layer is
> meant to emerge, not be fixed by the library.

<!-- @demo point bob.tunnel -->

<!-- @demo chapter 4 "Admission and eviction" -->
<!-- @demo blurb Alice admits bob and the tunnel opens. Eve knocks and hears nothing. Alice evicts bob mid-session, and then the wire shows what it saw of all of it. -->

### Alice admits bob

Admission is one call with one argument, on alice's machine:

```bash
./lab.sh alice curl -s -X POST localhost/admit -H 'content-type: application/json' -d '{"did":"did:web:bob.lan"}'
./lab.sh alice curl -s -X POST localhost/drop -H 'content-type: application/json' \
  -d '{"repo":"channel","text":"bob, this one is just for you"}'
```

```json
{"admitted":"did:web:bob.lan","tunnel_ip":"fd5a:7a7f:9c3e:0:e2a7:40c6:91b5:d7c","channel":"did:key:zQ3shrSrkz..."}
{"uri":"at://did:key:zQ3shrSrkz.../com.example.tutorial.note/3mz23jmnmz6iw","cid":"bafyreibqpg...","rev":"3mz23jmnnjwkm","seq":4}
```

Alice resolved `did:web:bob.lan`, read bob's tunnel identity out of *bob's*
DID document, wrote a capability record into the channel, and added bob's
key to alice's kernel interface. Look at both ends. Give it five seconds for
bob's next keepalive to complete the handshake:

<!-- @demo point alice.tunnel bob.tunnel -->

<!-- @demo wait 6 -->

```bash
./lab.sh alice wg show wg0
./lab.sh bob ping -c1 "$AT"
```

```text
interface: wg0
  public key: Xz4BX2dBPJSHACFSjvNabZY57FhQjafxm5hIeWy0YwA=
  listening port: 51820

peer: X3BOLJ7vKtSJJEXDlr7Cl2tr9Sgxae5x0hsF+D7bY18=
  endpoint: 192.168.77.12:51820
  allowed ips: fd5a:7a7f:9c3e:0:e2a7:40c6:91b5:d7c/128
  latest handshake: 7 seconds ago
  transfer: 212 B received, 92 B sent

1 packets transmitted, 1 received, 0% packet loss
```

Notice what alice's side says: this key, and only this key, may send
packets claiming the one address its key derives. That line is why a
source address is an identity inside the tunnel, and why nobody had to
agree on the address: it was never chosen.

<!-- @demo point alice.tunnel -->

> 🔑 **Key concept: authorization in FastATP is declarative.** Who may read
> the channel is not a setting in the process or a rule in a config file. It
> is the set of live `Capability` records in the channel's repo: there is a
> record for bob, or there is not. Everything else is derived from that set.
> The kernel's peer list is meant to be a cache of it, and in production
> `WireGuardReconciler.reconcile()` makes the two equal on every run: it
> adds peers the records call for, removes peers no record explains, and
> deletes records whose `expires_at` has passed. The lab's `/admit` and
> `/evict` do the two steps by hand so you can watch them. Three things fall
> out for free. The access list is a signed, readable record set, so an
> audit is a read. A reboot that lost the kernel state is repaired by one
> reconcile, because the repo was the truth all along. A time-boxed
> admission is one field on the record, not a cron job. The one thing you
> do not get is an instant cutoff: deleting the record is the decision, and
> nothing changes in the kernel until something reconciles. `/evict` does
> both in one request, which is why bob's packets stop within a second.

<!-- @demo point alice.repo alice.tunnel -->

> ⚠️ **Important: for private repos to work in production, three things
> must be true on the hub's host.** First, WireGuard's UDP port (51820 here) must be
> reachable by the peers you admit: open it in the host firewall, forward
> it through NAT if there is one, and publish the reachable address in the
> `wg://` endpoint of your DID document. Second, the peer process must be
> able to run `wg set`, which means root or `CAP_NET_ADMIN`; the lab grants
> `NET_ADMIN` to each machine. Third, the sync surface must listen
> *directly* on the tunnel interface with nothing in front of it. A reverse
> proxy, a load balancer, or a Docker port mapping rewrites the source
> address, and the doorman then refuses everyone. FastATP logs a warning
> when it sees that pattern. The public repo can sit behind whatever you
> like; the gated one cannot.

<!-- @demo point alice.tunnel bob.tunnel -->

Bob reads the channel through it:

```bash
./lab.sh bob python follow.py "$CH" "$AT"
```

```text
checkpoint: rev 3mz23jmnnjwkm, 2 records, verified; stream at seq 4
   com.example.tutorial.note/3mz23jmnmz6iw: Note(text='bob, this one is just for you', created_at='2026-10-29T20:17:41.477366Z')
   dev.fastatp.auth.capability/3mz23jmkvekiw: Capability(subject='did:web:bob.lan', actions=['subscribe'], ..., wg_public_key='X3BOLJ7v...')
tailing did:key:zQ3shrSrkz... after seq 4, via fd5a:7a7f:9c3e:0:5c0e:7a19:2bd4:8f31
```

Bob sees the private note and, because capabilities are ordinary records,
bob also sees the record that admitted them. <!-- @demo terminal -->Leave this follow running; it
matters in a moment.<!-- @demo /terminal -->

<!-- @demo point bob.terminal -->

The same server, asked what it hosts, answers according to the wire the
question arrives on:

```bash
./lab.sh bob curl -s "[$AT]/xrpc/com.atproto.sync.listRepos"      # bob, through the tunnel: two repos
./lab.sh bob curl -s alice.lan/xrpc/com.atproto.sync.listRepos     # bob, over the LAN: one repo
./lab.sh eve curl -s alice.lan/xrpc/com.atproto.sync.listRepos     # eve: one repo
```

Bob over the LAN is nobody, same as eve. The channel is not hidden from
"eve"; it is hidden from everyone who is not arriving through an admitted
tunnel.

<!-- @demo point bob.terminal eve.terminal -->

### Eve tries

Eve can pair exactly as bob did, because alice's document is public:

```bash
./lab.sh eve python pair.py did:web:alice.lan
./lab.sh eve ping -c1 -W3 "$AT"
./lab.sh eve python follow.py "$CH" "$AT"
```

```text
tunnel peer set: did:web:alice.lan is Xz4BX2dBPJSHACFSjvNabZY57FhQjafxm5hIeWy0YwA= at alice.lan:51820, tunnel ip fd5a:7a7f:9c3e:0:5c0e:7a19:2bd4:8f31
1 packets transmitted, 0 received, 100% packet loss
no route to fd5a:7a7f:9c3e:0:5c0e:7a19:2bd4:8f31: ConnectTimeout (is there a tunnel?)
```

Eve's handshakes are dropped, eve's pings vanish, and the channel does not
exist on eve's network. Eve cannot learn it is there. Nothing about this
depends on eve behaving; eve can send anything at all to
alice's port 51820 and the kernel discards it before any of alice's code
runs.

<!-- @demo point eve.terminal alice.tunnel -->

### Alice evicts bob

Bob's follow from earlier is still running in its terminal. <!-- @demo terminal -->In another:<!-- @demo /terminal -->

```bash
./lab.sh alice curl -s -X POST localhost/evict -H 'content-type: application/json' -d '{"did":"did:web:bob.lan"}'
./lab.sh alice curl -s -X POST localhost/drop -H 'content-type: application/json' -d '{"repo":"channel","text":"after eviction"}'
```

```json
{"evicted":"did:web:bob.lan","deleted":"dev.fastatp.auth.capability/3mz23jmkvekiw"}
```

Watch bob's terminal: nothing arrives. The second private note was
committed and a frame went out on the channel's firehose, but the packets
carrying it to bob's address no longer match any peer on alice's
interface, so the kernel dropped them. Bob's WebSocket is not closed with
an error; it goes silent, and bob's keepalives go unanswered until the
connection times out. <!-- @demo terminal -->Ctrl-C it, and confirm:<!-- @demo /terminal -->

<!-- @demo point bob.terminal -->

<!-- @demo wait 5 -->
<!-- @demo stop bob -->

```bash
./lab.sh alice wg show wg0 peers            # prints nothing
./lab.sh bob ping -c1 -W3 "$AT"             # 100% packet loss
./lab.sh bob python follow.py "$CH" "$AT"
```

```text
no route to fd5a:7a7f:9c3e:0:5c0e:7a19:2bd4:8f31: ConnectTimeout (is there a tunnel?)
```

Presence of the record was admission. Absence is revocation. There is no
token to expire and no session to invalidate, because neither ever
existed, and there is no live connection to hunt down, because the tunnel
it rode on is gone.

> 🔑 **Key concept: the record decides, the kernel enforces.** The
> capability record is signed data in the channel's own repo, so every
> admitted reader can see who else is admitted and audit it later. A record
> on its own lets no packet through. The kernel peer entry is what actually
> moves or drops packets, and on its own nothing signed says why it is
> there. Admission
> writes the record first and then opens the tunnel; eviction deletes the
> record first and then closes it. If the two ever disagree, the record
> wins and the kernel is brought back in line, which is the job
> `WireGuardReconciler` does for you in production.

<!-- @demo point alice.tunnel diagram -->
<!-- @demo end -->

### What is real here and what is not

| Real | Stand-in |
| --- | --- |
| Three network stacks, three disks, three keys, three hostnames | They are containers on one computer, not three boxes |
| The LAN: routed IP between machines, DNS for `*.lan` | A private bridge inside Docker, not your office network |
| WireGuard: kernel interfaces, real handshakes, cryptokey routing | Nothing |
| Admission and eviction changing the kernel's peer list | Nothing |
| DID documents fetched over the network by `WebResolver` | Fetched over `http`, because there is no CA on this LAN |
| A gated channel behind the doorman, with its capability records | Its key and records live in memory, so a reboot makes a new channel and forgets every admission |
| The tap: a passive observer of every frame on the LAN | Nothing; an ISP or a shared Wi-Fi sees exactly this |

In production, the DID document is a static file on `https://` under a
domain you own, the tunnel endpoint in it is a public address, and
`WireGuardReconciler` from `fastatp.authz` owns `wg0` instead of the two
direct calls in `/admit` and `/evict`: it writes the same capability
record, keeps the kernel peer list equal to the set of live capabilities
on a timer, sweeps expired grants, and leaves a signed revocation record
behind. `examples/wireguard_authz.py` in the fastatp repo runs that loop,
and `tests/test_wireguard_rootless_netns.py` proves it against a real
kernel interface.

`/admit` writes the same record the reconciler's `grant` writes: the
subject and the WireGuard key it was admitted with, read once from its DID
document, and nothing else. The doorman derives the address from that key
on every read, so there is no address anywhere that could go stale or lie.
If bob ever rotates the WireGuard key, bob is a stranger until alice admits
bob again; that is a decision for alice, not something the reconciler does
on its own. The doorman's one question, "which DID is this connection?",
is also a seam: `create_server(identity_resolver=...)` takes any callable
from a connection to a DID, for hosts whose network already knows who its
addresses are.

## Part 4: everything at once, on one screen

<!-- @demo start -->
<!-- @demo chapter 2 "A verified follower" -->
<!-- @demo open wire -->

Everything so far was seen by one party at a time: alice, bob or eve, each
in their own terminal. The last view is the one none of them has: the
cable between them, as seen by an observer who sees every frame on the
LAN, holds no key, and is admitted to nothing. On the internet that is your
ISP, the shared Wi-Fi, or anyone on the path between two peers.

<!-- @demo point wire -->
<!-- @demo end -->

<!-- @demo terminal -->Start from a clean lab, so the demo can replay the guide from the
first minted identity:<!-- @demo /terminal -->

```bash
./lab.sh clean
./lab.sh conductor
```

```text
the conductor is up: open http://localhost:8080
```

<!-- @demo terminal -->Open <http://localhost:8080> and press **Start**.<!-- @demo /terminal --> The page has three
parts: one frame per machine, the diagram, and the wire.

| On the screen | What it is |
| --- | --- |
| One frame per machine | That machine's own view: its terminal, its public repo (verified, as any reader would), and its `wg0` peers |
| The diagram | The whole picture, which no single machine has: who follows whom, over which wire, and which tunnels are open |
| The wire | Every packet the machines put on the LAN, as an outsider sees it |

The conductor plays the guide in four chapters of a few minutes each, in
order or picked from the menu at the top, on fresh machines, one step for
each press of **Next**: the guide's own words as a caption, or one command
on one machine, whose terminal lights up while it runs. At every key
concept it stops and points at what the concept is about; **Got it**
carries on, and is the next press of **Next**. **Back** shows an earlier
caption or concept again; the machines stay where they are, because every
step really ran.



The score is this file. Open `GETTING_STARTED.md` as plain text and you
will find lines such as `<!-- @demo stop bob -->` between the paragraphs.
They are HTML comments, so they do not render here. They cover what the prose
says in words ("Ctrl-C it", "give it five seconds"). The commands
themselves come from the code blocks, unchanged: `./lab.sh bob ...` is bob's
line. The first cue, `<!-- @demo machines alice bob eve -->` under "What you
are building", names the cast.

<!-- @demo start -->
<!-- @demo chapter 2 "A verified follower" -->
<!-- @demo open wire -->

### The public side is public

When bob follows alice in Part 2, the wire's `http` rows are the whole
conversation, in the clear: `GET /.well-known/did.json`, alice's DID
document coming back, `GET /xrpc/com.atproto.sync.getRepo`, and the CAR
with `hello, LAN` readable inside it.

<!-- @demo point wire -->

> 🔑 **Key concept: signed is not secret.** The public repo is signed so that
> anyone can check *who wrote it* and that *nobody changed it*. It is not
> encrypted, because it is public. The observer can read every record, and
> can see who is reading whom. What the observer cannot do is change a
> byte: the record's CID and the commit signature would no longer match,
> and bob's `follow.py` would refuse the checkpoint. In production this
> traffic rides TLS, which hides the bytes from the path but still shows
> the hostname, the sizes and the timing, and the records were public
> anyway. The protocol's promise for public data is authenticity, not
> confidentiality.

<!-- @demo point wire -->

The DID document deserves one more word, because it is the exception:

> ⚠️ **Important: this is why `did:web` requires HTTPS.** A *passive* observer reading
> the DID document learns nothing it could not fetch itself. An *active*
> one on the path could rewrite it in flight and hand bob a different
> public key, and every signature check bob does afterwards would be
> against the attacker's key. Signatures cannot protect the document that
> says which key to trust. The lab's one stand-in, plain `http` in
> `dids.py`, is exactly this hole, and it is acceptable here only because
> the LAN is a bridge inside your own computer. Never carry it to a real
> network.

<!-- @demo point wire -->

<!-- @demo close wire -->
<!-- @demo chapter 4 "Admission and eviction" -->
<!-- @demo open wire -->

### The private side is a shape

Now the `WireGuard` rows from Part 3. When bob pairs, a 148-byte
`handshake initiation` goes out every five seconds and nothing comes back.
The moment alice admits bob, a 92-byte `handshake response` answers, the
diagram's tunnel turns solid, and from then on every packet between them is
`transport data`: the inner packet plus 32 bytes, padded to a multiple
of 16. That is all the structure there is. No row of it says `did:key`,
`getRepo`, `HTTP` or the note's text. Eve's rows are initiations every five
seconds, never a response. The observer can tell that eve is knocking and
alice is not answering, a fact eve only learns from the silence.

<!-- @demo point wire -->

> 🔑 **Key concept: what a network sees.** For the private side, the observer
> gets the endpoints, the fact that it is WireGuard, when a session began,
> and the sizes and timing of what crossed. A patient observer could guess
> "a checkpoint, then a small event" from the shape above. That is
> metadata, and it is all there is. Content, identities, and even the
> existence of the gated repo are not on the wire. For the public side,
> everything is on the wire, by design, and the protection is that none of
> it can be forged.

<!-- @demo point wire diagram -->

| Flow | The observer sees | The observer cannot |
| --- | --- | --- |
| DID document fetch | All of it (hostname, sizes and timing under TLS) | Alter it undetected, *if* it is HTTPS |
| Public repo read | Who reads whom, and every record | Forge or alter a record |
| Private channel | Endpoints, timing, sizes, that it is WireGuard | Learn the channel's DID, contents, or inner protocol |
| Eve's attempts | That eve knocks and gets nothing | |

Those are two different questions, who wrote this and who may read it,
and FastATP answers them separately:

> 🔑 **Key concept: authentication is a signature, authorization is a
> record, and neither needs a shared secret.** *Authentication* asks "who
> said this?" Every record answers it itself: it is signed, and any reader
> checks the signature against the public key in the author's DID
> document. No login, no session, no token, no server to ask. That is why
> the public side can be read by anyone and still not be forged.
> *Authorization* asks "who may read this?" The public repo answers
> "anyone", on purpose. The gated channel answers declaratively with a `Capability`
> record, and the tunnel enforces it. Nowhere in this guide did two
> parties share a password, an API key or a bearer token, and no identity
> provider or auth server sat between them. Each machine holds only its
> own private keys, and everything it hands to anyone else is public. That
> is deliberate: a secret you share is a secret someone else can leak, and
> a third party that decides access is a third party that can revoke it.
> WireGuard is the authorization plane FastATP ships with, not the only
> one it accepts. The simplest alternative involves no FastATP code at
> all: put the peer on your VPN, serve its repos as public, and let the
> network admin's ACLs decide who can reach it. Everything is "public" to
> whoever the ACLs let through, and FastATP never knows the ACLs exist.
> On Tailscale that is still WireGuard underneath; the difference is that
> access is decided centrally by the tailnet admin instead of peer to peer
> by records. If you want records to decide per repo, the doorman asks one
> question, "which DID is this connection?", through the
> `identity_resolver` you pass to `create_server(...)`, and a tailnet or a
> corporate VPN can answer it by mapping its addresses to DIDs. An OAuth
> gateway can answer it too, by vouching for the caller in a header,
> though then you are trusting that gateway, which is exactly the third
> party this design avoids by default. Authentication does not change with
> any of them: records are still signed and still checked by the reader.

<!-- @demo point diagram -->

None of this is free, and the price is worth stating plainly:

> 🔑 **Key concept: the price is software on every end.** Every party in
> this guide ran a symmetric peer: a process holding a signing key to say
> who it is, a pen kept cold to recover it, and, to be admitted anywhere
> private, a tunnel key of its own. Shared secrets are everywhere because
> they ask for almost nothing. The user carries a string (a password, a
> token, a cookie) and the string travels the wire. Nothing has to run on
> the user's side except a browser, which everyone already has and nobody
> counts. FastATP asks for a peer on each end instead. For devices,
> services and people who run their own box, that is a process like
> `peer.py`. For everyone else, FastATP ships an experimental browser peer
> that runs in the tab they already have open; the browser app tutorial,
> later in this series, is the guide to it.

<!-- @demo close wire -->

> 🔑 **Key concept: the demo is backstage, and only the typing is done for
> you.** The conductor runs each command of this guide on the machine the
> guide says, in the guide's order, and nothing else. The records,
> signatures, tunnels and refusals you watch are the same ones you made by
> hand. The demo talks to the machines on a separate network, so the LAN,
> and the wire view, carry exactly the traffic you would see without it.
> The wire view is each machine reporting the packets it sends on the LAN,
> which together are what a tap on the switch would see.

<!-- @demo point controls -->

> 💡 **Did you know: the engine playing this demo is FastATP too.** Each
> machine runs a second peer backstage, and so does the conductor. The
> conductor turns this guide into a score and signs every step into its own
> repo, with the step's words, its targets and the code on show in the
> record, addressed to one machine. That machine follows the conductor,
> performs the step, and signs what happened into its own repo, which the
> conductor follows before it moves on. Every command you just watched was
> asked for, and answered, in signed records. The demo engine tutorial,
> next, takes the conductor apart and has the machines play a scale.

<!-- @demo end -->

### The raw tap, if you want it

The wire view is a summary. For raw packets, `./lab.sh tap` runs `tcpdump`
attached to the LAN's switch from outside, as nobody: `./lab.sh tap -A
port 80` prints the public side as text, and `./lab.sh tap -c 50 -w
/state/wire.pcap udp port 51820` saves a capture to `lab/state/wire.pcap` for
Wireshark, whose WireGuard dissector labels every packet and still cannot
open one. On macOS and Windows the tap needs a Docker Desktop recent enough to
support host networking.

## What you just ran, in "Bluesky" terms

| In this tutorial | On the live network |
| --- | --- |
| `peer.py` on `alice.lan` | Your PDS on your hostname. Same routes. |
| `state/alice.lan/` | The state dir on your peer box. Back it up, keep the pen cold. |
| `alice.lan/.well-known/did.json` | A static file on `https://<your domain>/`. |
| `/drop` | Your posting UI, whatever it is. Loopback or VPN only. |
| `follow.py` | What a relay does to you, and what your browser does to everyone you follow. |
| `tail.py` | Roughly what Jetstream shows you: the wire, unverified. |
| `verify.py` | Anyone, anywhere, checking one of your records against your DID document. |
| The gated channel, through `wg0` | Agent-to-agent and private workflows. Never announced to a relay. |
| `com.example.tutorial.note` | `app.bsky.feed.post`, which is just another lexicon in your repo. |

## Things to try before moving on

- Drop a note longer than 300 graphemes. The `/drop` call returns a 500,
  `./lab.sh logs alice` shows `string exceeds 300 graphemes`, and
  `getLatestCommit` shows the `rev` did not move. The constraint is
  enforced before anything is signed.
- Edit the last line of `verify.py` to pass bob's DID as
  `expected_signer`, run `./lab.sh up` to rebuild, and run it against one
  of alice's records. You get `SignerMismatch: expected signer
  'did:web:bob.lan', proof signed by 'did:web:alice.lan'`. The proof names
  its author, and the check is against the key in the author's DID
  document.
- Admit eve as well (`{"did":"did:web:eve.lan"}`), drop a second private
  note, read the channel from both machines through the tunnel, then evict
  both. Watch `wg show wg0` on alice as the peer list follows the
  capability records.
- Change `Note` to add a required field, `./lab.sh up` to rebuild, and
  run `follow.py` against alice again. Old records no longer parse. Make
  the field optional and they do. That is schema evolution on a
  present-state repo, and it is your problem to manage, not the protocol's.
- Reboot bob (`./lab.sh restart bob`). Bob's tunnel comes back up with the
  same key but no peers; bob has to `pair.py` again, and if alice still
  holds the capability, bob is readmitted without alice doing anything. The
  record outlived bob's kernel state. That is the half the reconciler would
  automate on alice's side too.

## Where this leads

You can orchestrate workloads with signed records too. Everything the
conductor did in Part 4, cueing three machines and waiting for each one's
signed answer, was asked for and answered in records, with no connection
held and no secret shared. The next tutorial looks under the hood at how
these tutorials get orchestrated by FastATP: the demo engine, taken apart,
which is the same request-and-answer pattern as an agent asking a tool for
work, with no model in the way yet. After it come Hoot, where the agents
are real, and the browser app, where a person reaches an app without ever
sending a secret across the wire. Each guide builds on the one before it.

The next step up from here is `examples/` in this repository, in this
order: `two_peers.py` (what you just did, in one script), `workorder.py`
(request and reply as a framework, with `PeerApp` dispatching your
lexicons to handlers), `board_rendezvous.py` (a private reply channel
opened on demand and torn down by TTL), `wireguard_authz.py` (the
reconciler), and `bluesky_peer/` (the same `peer.py` shape pointed at the
real network).

## Cleanup

```bash
./lab.sh clean
```

That stops the three machines, removes the LAN, and deletes `state/`. The
image stays cached so the next `./lab.sh up` is fast; `docker image rm
fastatp-lab-alice` removes it. Nothing outside `lab/` was touched.
