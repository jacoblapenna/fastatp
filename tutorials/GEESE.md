# Geese: signing up and logging in with no secret on the wire

Geese is a small dating app. You sign up to it from your own browser, with
no email and no password, log in again later, add a second device, and take
that device away again. Nothing secret crosses the wire at any point, and
you never host anything: Geese keeps your data, and still can't write a byte
of it in your name.

Most apps fuse three things into a login: the secret, the phone-book entry,
and the mailbox. FastATP keeps them apart. Your key, the pen, is made inside
your browser and never leaves it, in any form. Your name and your repo are
public or self-checking data, so an app can keep them for you without ever
holding power over them.

This guide builds on [Getting Started](GETTING_STARTED.md), for repos, DIDs
and the doorman, and plays on [the conductor](CONDUCTOR_AND_ORCHESTRA.md).
[Hoot](HOOT.md) is not needed.

> 🤖 **Coding agents:** you can run this whole guide yourself, headless, and
> read what happened. [AGENTS.md](AGENTS.md) shows how; the score is `GEESE`.
> The viewer's turns in the app are taken by `tools/hands.py`, which needs a
> Chrome or Chromium on the computer.

## What you are building

<!-- @demo machines geese -->
<!-- @demo peer geese geese.py -->
<!-- @demo app geese /app/ -->
<!-- @demo start -->
<!-- @demo chapter 1 "Sign up" -->
<!-- @demo blurb Your browser makes a key that never leaves it, and Geese keeps your account without ever holding it. -->

One machine, Geese, the app's host, and you. The app is on the left, in this
browser, exactly as a person would meet it. Everything to its right is
backstage, and a real app shows you none of it.

<!-- @demo point app -->

<!-- @demo end -->

<!-- @demo start -->

> 🔑 **Key concept: the words in this guide, one line each.**
>
> - **Pen**: your private key. It signs everything you write, and it never
>   leaves the device that made it.
> - **Repo**: one signed tree of records, owned by one DID. Only its pen
>   writes to it.
> - **Record**: one typed entry in a repo.
> - **DID**: a permanent name for whoever signs a repo. Yours will be
>   `did:web:geese.lan:` and a hash.
> - **Host**: the machine that keeps your repo and serves it. Geese is yours.
>   It can't write in it.
> - **Signature**: bytes only the pen could have made. Anyone with its
>   public half can check them.
> - **Device**: a browser with a pen of its own, let in by a record in your
>   repo.

Nothing here is a password, a token or a session. Every step is something
signed, and something checked.

In four chapters:
1. **Sign up.** Your browser makes your pen and your repo, and Geese keeps
   the repo.
2. **Log in.** You come back, and Geese asks for a signature, not a password.
3. **A second device.** A new device is nobody until a record says it's you,
   and nobody again when the record goes.
4. **What it's for.** None of it built: what signed records let an app
   builder compose, and why that's where the value is.

<!-- @demo glossary -->
The rest of the guide's words, each one line. On the conductor's screen a
dotted word shows its line when you hover it.

- **Paper key**: the one secret you keep yourself, shown once at sign-up. It
  can put your identity onto a new pen when every device is gone.
- **Chain**: your DID's history, page by page: which pen speaks for you now,
  and which paper key may change that.
- **Shelf**: where Geese keeps each identity's chain, so anyone can resolve
  its DID.
- **Challenge**: a one-time number Geese hands out, for a pen to sign.
- **Delegation**: a record in your repo saying "this pen speaks for me".
- **Doorman**: the part of a host that decides who may read a repo, by
  the records in it.
- **WebCrypto**: the browser's own cryptography. A key it makes
  non-extractable can sign, and can't be read out, even by the page.
- **IndexedDB**: a browser's storage for one site, kept on that device.
- **CAR**: a whole repo as one file of signed blocks.
- **Backstage**: what the host sees, shown beside the app here only.
<!-- @demo /glossary -->

<!-- @demo end -->

## Setup

You need what Getting Started needed: Docker with the compose plugin.
This guide needs no WireGuard of its own, and a browser on this computer.

This guide runs on the conductor:

```bash
cd tutorials/lab              # from the repository root
./lab.sh clean                # start from fresh identities
./lab.sh conductor GEESE
```

<!-- @demo terminal -->Then open <http://localhost:8080> and press
**Start**. Use `localhost` or `127.0.0.1`, not this computer's address on
your network: the browser makes keys only for a secure page, and plain
`http://` counts as secure only on this computer.<!-- @demo /terminal -->

One machine boots, Geese, and it runs one program. Three files are new:

```text
tutorials/
├── GEESE.md             this guide
└── lab/
    ├── geese.py         Geese's host: peer.py, plus a home for people who sign up from a browser
    └── geese/
        ├── index.html   the app: sign up, log in, your devices
        └── geese.js     what the app does, a few lines a verb
```

`geese.py` is below in full. The app's two files are in
[`lab/geese/`](lab/geese/), and are worth reading: each verb (sign up, log
in, let a device in, take it out) is a few lines, and none of them sends a
password, a token or a session.

### `geese.py`: Geese's host

The host: Getting Started's `peer.py`, imported unchanged, plus a shelf for
each identity's chain, two routes that take a repo signed in a browser, and
the app.

```python
"""Geese, the app: this machine's peer.py, plus a home for people who sign up from a browser.

python geese.py geese.lan

Runs everything peer.py runs, unchanged: it imports it. Beside it, it hosts
identities whose key, the pen, never leaves the browser that minted it:

- a shelf: each identity's DID document and its continuity chain, served at
  http://geese.lan/<slug>/did.json, so did:web:geese.lan:<slug> resolves;
- POST /signup: a browser's first repo, checked against the key on the shelf
  and hosted, gated, readable by its own pen;
- POST /push-car: every later state of that repo, signed in the browser,
  checked against the same key, and announced on the firehose;
- the app itself, at /app/, and the browser peer it runs, at /fastatp/.

This host never holds a pen and never signs for anyone it hosts. A hosted
repo is frozen here: it changes only by a whole repo, signed elsewhere,
replacing it. The host can drop or delay a push; it cannot forge one. It
does serve the app's code, though, and code it serves runs beside the pen:
the pen's bytes can't be read out, but that code could ask it to sign. A
real app keeps those two apart, or pins its code where users can check it.

Adapted from examples/hosted_peer/builder.py in this repository, which
runs the same pattern end to end; this is lab code, not library code.
"""

import asyncio
import socket
from pathlib import Path

import peer  # this machine's peer, exactly as guide one left it
import uvicorn
from dids import resolver
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from fastatp import DIDServer, RepoMirror, crypto
from fastatp.browser import browser_peer_assets
from fastatp.crypto import PublicKey
from fastatp.did.document import DIDDocument
from fastatp.exceptions import FastATPError, StaleCheckpoint
from fastatp.repo.repo import Repo
from fastatp.server.browser_gate import BrowserGate
from fastatp.server.peer_server import RepoHandle

APP = Path(__file__).resolve().parent / "geese"  # the app: a page and the module it runs

CAR_LIMIT = 8 * 1024 * 1024  # a whole repo from a browser; anything bigger is refused unread
chains: dict[str, dict] = {}  # slug -> a hosted identity's continuity chain, as shelved
hosted: dict[str, RepoHandle] = {}  # DID -> the repo this host keeps for it


def head_key(did: str) -> PublicKey:
    """The identity's current signing key, from the newest page of its shelved chain.

    Read again on every use, so a rotated pen takes effect at once. Only a
    DID on this host's own name: the shelf keeps chains by slug alone."""
    prefix = f"did:web:{peer.domain}:"
    slug = did.removeprefix(prefix)
    entry = chains.get(slug) if did.startswith(prefix) and ":" not in slug else None
    if entry is None:
        raise KeyError(f"{did} is not shelved here")
    head = DIDDocument.from_json_dict(entry["chain"][-1]["document"])
    return head.get_verification_method().public_key()


def owner_pen(repo: Repo) -> str | None:
    """Which pen may read this repo back with no capability: the one its chain names now."""
    try:
        return crypto.encode_did_key(head_key(repo.did))
    except (KeyError, FastATPError):
        return None  # not shelved, or a chain this host cannot read: nobody's


async def shelved_document(slug: str):
    """A did.json request: a hosted identity's newest document, or this machine's own."""
    if slug in chains:
        return DIDDocument.from_json_dict(chains[slug]["chain"][-1]["document"])
    return await peer.did_resolver(slug)


async def shelved_chain(slug: str):
    return chains.get(slug) or await peer.chain_resolver(slug)


def shelve(slug: str, chain) -> None:
    """The shelf takes a chain only if it is valid and extends what it has; this keeps it."""
    chains[slug] = chain.to_json()


async def car_body(request: Request) -> bytes | None:
    """The request's body, a CAR, or None once it passes CAR_LIMIT: read no further."""
    if int(request.headers.get("content-length") or 0) > CAR_LIMIT:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > CAR_LIMIT:
            return None
    return bytes(body)


TOO_BIG = JSONResponse({"error": "TooLarge", "message": f"over {CAR_LIMIT} bytes"}, status_code=413)


async def signup(request: Request) -> Response:
    """A browser's first repo: 'keep this for me'. The chain must be shelved first."""
    did = request.query_params.get("did", "")
    try:
        key = head_key(did)
    except KeyError:
        return JSONResponse({"error": "NotShelved"}, status_code=404)
    if (car := await car_body(request)) is None:
        return TOO_BIG
    repo = Repo(did, None)  # frozen: this host has no pen for it, so it can never write it
    try:
        await repo.import_car(car, verify_key=key)
        hosted[did] = await peer.server.register(repo, owner_pull=owner_pen)
    except FastATPError as e:
        return JSONResponse({"error": "BadCar", "message": str(e)}, status_code=400)
    except ValueError:  # register() is the one that decides: two signups, one host
        return JSONResponse({"error": "AlreadyHosted"}, status_code=409)
    await hosted[did].emit_sync()  # the first state, announced like every later one
    print(f"[{peer.domain}] hosting {did}, signed up from a browser")
    return JSONResponse({"hosting": did}, status_code=201)


async def push_car(request: Request) -> Response:
    """A newer state of a hosted repo, signed in the browser: checked, kept, announced."""
    did = request.query_params.get("did", "")
    handle = hosted.get(did)
    if handle is None:
        return JSONResponse({"error": "NotHosted"}, status_code=404)
    if (car := await car_body(request)) is None:
        return TOO_BIG
    try:
        # The signature must be the current pen's, and the state newer than the
        # one held: a signed state can be replayed, and must never roll back.
        await handle.repo.import_car(car, verify_key=head_key(did), newer_than=handle.repo.rev)
    except StaleCheckpoint as e:
        return JSONResponse({"error": "Stale", "message": str(e)}, status_code=409)
    except FastATPError as e:
        return JSONResponse({"error": "BadCar", "message": str(e)}, status_code=400)
    await handle.emit_sync()  # followers see the new state on the firehose
    return JSONResponse({"accepted": handle.repo.rev})


async def mine(request: Request) -> JSONResponse:
    """/mine: the repos this host keeps for people who signed up, as the host
    sees them. Only this machine may ask; the demo shows it backstage. Each is
    read back like any reader would, signature and every hash checked: the
    host can show them, and can't have written a byte of them."""
    if (denied := peer.local_only(request)) is not None:
        return denied
    found = []
    resolve = resolver()  # the identities' documents, as anyone resolves them
    for did, handle in list(hosted.items()):  # a sign-up may land while this reads
        mirror = RepoMirror(did, resolver=resolve)
        await mirror.load_checkpoint(handle.repo.export_car())
        records = [
            {"path": path, "cid": cid.encode(), "value": mirror.get_record(*path.split("/"))}
            for path, cid in sorted(mirror.entries.items())
        ]
        name = did.rsplit(":", 1)[-1][
            :8
        ]  # a name a person would never choose: a slice of the key's hash
        found.append({"name": name, "did": did, "readers": ["its own pen"], "records": records})
    return JSONResponse(found)


async def main() -> None:
    app = await peer.boot()
    gate = BrowserGate()  # a browser reads its own repo by signing a one-time challenge
    gate.install(peer.server)
    shelf = DIDServer(
        domain=peer.domain,
        resolver=shelved_document,
        chain_resolver=shelved_chain,
        shelf_store=shelve,
    )
    # Ahead of peer.py's routes: the shelf answers every did.json, and hands
    # this machine's own back to peer.py.
    app.router.routes[0:0] = [
        *shelf.app.routes,
        gate.route(),
        Route("/signup", signup, methods=["POST"]),
        Route("/push-car", push_car, methods=["POST"]),
        Route("/mine", mine),
        Route("/", lambda request: RedirectResponse("/app/")),
        Mount("/app", StaticFiles(directory=APP, html=True)),
        Mount("/fastatp", browser_peer_assets()),
    ]
    sockets = [peer.listen(socket.AF_INET, "0.0.0.0"), peer.listen(socket.AF_INET6, "::")]
    print(
        f"[{peer.domain}] serving on http://{peer.domain} (LAN)"
        f" and http://[{peer.tunnel_ip}] (tunnel)"
    )
    print(f"[{peer.domain}] Geese: sign up from a browser at http://{peer.domain}/app/")
    await uvicorn.Server(uvicorn.Config(app, port=80, log_level="warning")).serve(sockets=sockets)


if __name__ == "__main__":
    asyncio.run(main())
```

Read `signup` and `push_car` slowly. Both take a whole repo from the
browser, check it against the pen your chain names now, and keep it. The
repo Geese keeps is frozen: Geese has no pen for it, so it can only replace
it with a newer one, signed elsewhere.

## Part 1: Sign up

<!-- @demo start -->

Geese is one machine, and everything your browser runs comes from it: the
page, and the browser peer the page runs.

<!-- @demo ask What does your browser have to install to sign up to Geese? -->

```bash
./lab.sh geese curl -s localhost/fastatp/
```

```json
{"version":"...","files":["fastatp_wasm.d.ts","fastatp_wasm.js","fastatp_wasm_bg.wasm","fastatp_wasm_bg.wasm.d.ts"]}
```

Nothing. That is the whole browser peer, WebAssembly and its JavaScript,
served by Geese from inside the fastatp package, the same version as Geese's
own.

<!-- @demo ask When you press Sign up, what will your browser send Geese: a password, a key, or something else? -->
<!-- @demo code geese.py "async def signup(" -->

Tick **keep me signed in on this device**, then press **Sign up**.
<!-- @demo you app POST /signup -->
<!-- @demo do app tick #keep -->
<!-- @demo do app click #signup -->

Look at what crossed the wire: your DID's chain, to the shelf, and your first
repo, to sign-up. Both are signed. Neither is secret.

<!-- @demo point app.wire -->

![the pen stays in the browser; only signed things go to Geese, which keeps the repo and the chain and holds no pen](sketches/pen-stays.svg)

> 🔑 **Key concept: your pen never leaves this browser.** Your browser made
> the pen as a WebCrypto key marked non-extractable: not even the page's own
> code can read its bytes. The pen signed your chain and your first repo,
> and Geese checked both against the pen's public half. Geese keeps your
> repo and serves it, and can't write a byte of it, because it has no pen.
> It can hold your data back, or serve an old copy. It can't forge you.

<!-- @demo point app -->

Backstage, the repo Geese now keeps for you, read back and checked the way any
reader would. A real app shows you none of this.

<!-- @demo point geese -->

> ⚠️ **Important: "keep me signed in" keeps the pen.** Ticked, the app saves
> the pen's handle in this browser's IndexedDB. The bytes still can't be
> read, but the handle can sign, so this browser is you until the pen is let
> go. Treat it with at least the care apps give "keep me signed in" today:
> on a shared computer, whoever sits down next is you. Unticked, the pen
> lives only in the tab, and closing it burns the pen. That choice is the
> app's, never the library's: fastatp stores no pen anywhere.

> 🔑 **Key concept: the paper key is the way back with no device.** It was
> shown once, and nothing keeps or sends it. With every device lost, it
> signs a new page of your chain naming a new pen, and Geese checks every
> push against the newest page, so the old pen is locked out. It can't be a
> short code, because nobody can let you in: only a key can sign that page,
> so the paper key is one. A builder can ask for it back before moving on,
> checked in the browser against the chain with nothing sent, spell it as
> 24 words, or print several cards, any one of which recovers. Lose every
> device and the paper, and the account is gone.

![the paper key signs a new page of the chain naming a new pen; the old pen is locked out](sketches/paper-key.svg)

<!-- @demo point app -->

<!-- @demo chapter 2 "Log in" -->
<!-- @demo blurb You come back, and Geese asks for a signature, not a password. Nothing is kept between visits but the pen in your browser. -->

## Part 2: Log in

Come back later: here, that is reloading this browser. The pen is still in
it, because you ticked keep me signed in.

<!-- @demo ask Geese keeps no session and no password for you. What can your browser show it to get your repo back? -->
<!-- @demo code geese.py "def owner_pen(" -->

Reload the app with ↻ at the top of its panel (reloading the whole page
works too), then press **Log in**.
<!-- @demo you app GET /xrpc/com.atproto.sync.getRepo -->
<!-- @demo do app reload -->
<!-- @demo do app click #login -->

Two requests: one for a challenge, and one for your repo, with your DID,
your pen's public name, the challenge, and a signature over them.

<!-- @demo point app.wire -->

![Geese hands out a one-time challenge; the pen signs it; Geese checks it and sends the repo; the challenge is spent](sketches/login-knock.svg)

> 🔑 **Key concept: a login is one signed read.** Geese handed out a
> one-time challenge. Your pen signed it, with your DID, and Geese checked
> the signature, checked that this pen is the one your chain names now, and
> sent your repo. The challenge is spent, so the same request a second time
> gets nothing. No session was made: the next visit signs a new challenge.

> 🔑 **Key concept: the host can withhold, never forge.** Your repo came
> back signed by your pen, and the browser checked that signature before
> using it. A host that went wrong could send nothing, or an old copy. It
> could not send words you never signed. One honest limit: Geese also
> serves the app's code, and that code runs beside your pen. It can't read
> the pen out, but code a bad host served could ask it to sign. A real app
> keeps the two apart, or pins its code where you can check it.

<!-- @demo point app -->

<!-- @demo chapter 3 "A second device" -->
<!-- @demo blurb A new device gets a key of its own. It is nobody until a record in your repo says it is you, and nobody again when the record goes. -->

## Part 3: A second device

A second device is the same app, with storage of its own. On this computer,
`127.0.0.1` beside `localhost` is another site to the browser, so it keeps
its own keys, as a phone would.

<!-- @demo open phone -->

<!-- @demo ask The second device makes a key Geese has never seen. Will Geese let it in? -->

On the second device, choose **I already have an account**, paste your
account from this browser, press **Make this device's key**, then **Log
in**. Don't let it in from this browser yet: first see what Geese says.
<!-- @demo you phone GET /xrpc/com.atproto.sync.getRepo 404 -->
<!-- @demo do phone click #existing -->
<!-- @demo do phone copy #account app #did -->
<!-- @demo do phone click #mint -->
<!-- @demo do phone click #login2 -->

> 🔑 **Key concept: holding a key is not being anyone.** The second device
> signed the challenge, and the signature was good. But no record in your
> repo names its pen, so Geese answers as if there were nothing there: not
> found. A valid signature from a pen nobody vouched for gets in nowhere.

<!-- @demo point phone.wire -->

<!-- @demo ask What does your first browser have to do to let the second one in? -->
<!-- @demo code geese.py "async def push_car(" -->

In this browser, paste the second device's key under **Your devices**, and
press **Let this device in**.
<!-- @demo you app POST /push-car -->
<!-- @demo do app copy #device phone #mypen -->
<!-- @demo do app click #add -->

![your repo holds a record naming the second device's pen; with it the device reads as you, without it as nobody](sketches/device-record.svg)

> 🔑 **Key concept: a device is let in by a record.** Your browser signed one
> record into your repo, a delegation saying "this pen speaks for me", and
> pushed the whole repo to Geese, which checked it against your pen. No key
> moved between devices: the second one keeps its own, and only its public
> name crossed.

<!-- @demo point geese -->

On the second device, press **Log in** again.
<!-- @demo you phone GET /xrpc/com.atproto.sync.getRepo 200 -->
<!-- @demo do phone click #login2 -->

Its own pen's name, your DID as the one it speaks for, a fresh challenge and
its signature. Geese found the record, so this pen reads as you.

<!-- @demo point phone.wire -->

<!-- @demo ask To lock the second device out, what has to change? -->

In this browser, press **Remove** beside the second device.
<!-- @demo you app POST /push-car -->
<!-- @demo do app click #devices button -->

On the second device, press **Log in** once more.
<!-- @demo you phone GET /xrpc/com.atproto.sync.getRepo 404 -->
<!-- @demo do phone click #login2 -->

> 🔑 **Key concept: absence is revocation.** The record is gone, so the pen
> speaks for nobody. There is no token to expire, no list of revoked keys to
> send around, and no log-out route. The next request the second device
> signs is checked against your repo as it is now.

<!-- @demo point phone -->

<!-- @demo chapter 4 "What it's for" -->
<!-- @demo blurb Nothing more is built. What signed records let an app builder compose, and why that is where the value is. -->

## Part 4: What it's for

That is the whole of sign-up and log-in. The rest of a dating app is not
built here. One more turn first, then what all this lets a builder compose.

<!-- @demo ask How much does a brand-new account cost to make? -->

Reload the second device, and press **Sign up** there: a new account, with
nothing kept on that device.
<!-- @demo you phone POST /signup -->
<!-- @demo do phone reload -->
<!-- @demo do phone click #signup -->

![two accounts: one with a history of records, one brand new and empty](sketches/fresh-account.svg)

> 🔑 **Key concept: an account is free, and a new one looks new.** It took a
> second and cost nothing. On its own, a signature proves who wrote a record
> and does nothing about spam. What it does give is history no one can fake,
> and that is what incentives can be built on: a burned account
> can be swapped for a fresh one, and the fresh one has nothing behind it.
> A reset is visible, not impossible.

> 🔑 **Key concept: privacy is composable, and it can pay for
> authenticity.** Signed records make spam and fake accounts costly, and
> real human interaction cheaper, and the market emerges from those
> incentives. None of this is built here; it is what a dating app could
> compose. How often someone reaches out could be public while whom they
> reach stays private, so ten times anyone's normal rate would be visible to
> all. Free accounts with scarce reach-outs would make a bot expensive.
> Reports could be signed records anyone can count. What's good for the
> goose: each person's behaviour adds up to a safer place for everyone.

![what each person does is countable; who they do it to stays private](sketches/incentives.svg)

> 🔑 **Key concept: code is cheap; the incentives are the value.** Code is
> becoming cheaper and cheaper. The real value is the incentives the system
> designer creates to create the market. Good behaviour emerges from them,
> the way safety emerges at rush hour: every driver has a stake, picks their
> own risk, and reads the signals of the others. Signed records are what
> make those signals checkable.

That is the series so far: keys that stay home, records that say who may do
what, and an app that keeps your data without holding power over it.

<!-- @demo end -->

## Things to try

<!-- @demo terminal -->

- Untick **keep me signed in** and sign up, then reload: the pen is gone with
  the tab, and only the paper key could bring the account back.
- Open the browser's developer tools on the app's own page,
  <http://localhost:8080/geese.lan/app/>, and look in IndexedDB: the pen is
  a `CryptoKey` whose bytes can't be read.
- Let a third origin in: open the app at your computer's other name in a
  private window, and try to log in as your account. It is refused until a
  record names its pen.

<!-- @demo /terminal -->

## Where this leads

[`docs/HOSTED-PEERS.md`](../docs/HOSTED-PEERS.md) is the design this guide
plays: persistence, the paper-key ceremony, recovery onto a new pen, and
host-blind data. [`examples/hosted_peer/`](../examples/hosted_peer/) runs
the same pattern with a wire audit: every byte the client sends is written
to disk, and the test searches it for any piece of a secret.

## Cleanup

```bash
./lab.sh clean
```
