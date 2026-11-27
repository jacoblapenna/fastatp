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
replacing it. The host can drop or delay a push; it cannot forge one.

Adapted from examples/hosted_peer/builder.py in this repository, which
runs the same pattern end to end; this is lab code, not library code.
"""

import asyncio
import socket
from pathlib import Path

import peer  # this machine's peer, exactly as guide one left it
import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from fastatp import DIDServer, crypto
from fastatp.browser import browser_peer_assets
from fastatp.crypto import PublicKey
from fastatp.did.document import DIDDocument
from fastatp.exceptions import FastATPError, StaleCheckpoint
from fastatp.repo.repo import Repo
from fastatp.server.browser_gate import BrowserGate
from fastatp.server.peer_server import RepoHandle

APP = Path(__file__).resolve().parent / "geese"  # the app: a page and the module it runs

chains: dict[str, dict] = {}  # slug -> a hosted identity's continuity chain, as shelved
hosted: dict[str, RepoHandle] = {}  # DID -> the repo this host keeps for it


def head_key(did: str) -> PublicKey:
    """The identity's current signing key, from the newest page of its shelved chain.

    Read again on every use, so a rotated pen takes effect at once."""
    entry = chains.get(did.rsplit(":", 1)[-1])
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


async def signup(request: Request) -> Response:
    """A browser's first repo: 'keep this for me'. The chain must be shelved first."""
    did = request.query_params.get("did", "")
    try:
        key = head_key(did)
    except KeyError:
        return JSONResponse({"error": "NotShelved"}, status_code=404)
    repo = Repo(did, None)  # frozen: this host has no pen for it, so it can never write it
    try:
        await repo.import_car(await request.body(), verify_key=key)
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
    try:
        # The signature must be the current pen's, and the state newer than the
        # one held: a signed state can be replayed, and must never roll back.
        await handle.repo.import_car(
            await request.body(), verify_key=head_key(did), newer_than=handle.repo.rev
        )
    except StaleCheckpoint as e:
        return JSONResponse({"error": "Stale", "message": str(e)}, status_code=409)
    except FastATPError as e:
        return JSONResponse({"error": "BadCar", "message": str(e)}, status_code=400)
    await handle.emit_sync()  # followers see the new state on the firehose
    return JSONResponse({"accepted": handle.repo.rev})


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
