"""The example builder: hosted sovereign peers from shipped primitives ONLY.

This is edge/example code, not library code (PHILOSOPHY.md: the substrate
ships primitives; builders compose them). It wires together:

- a neutral **shelf** (DIDServer + shelf intake): serves the
  users' twin-only DID documents and continuity chains,
- a **PeerServer** with the **browser gate** installed: hosts each
  user's gated repo with **owner pull** on, so the pen that writes a repo
  (the chain head's pen, re-read on every knock) and the identity itself
  (a device speaking for it through a live delegation record) read it back
  with no capability record; strangers are refused,
- two tiny example routes — the builder's own edge business logic:
  * ``POST /signup?did=`` — connection zero's second half: accept a genesis
    repo CAR, verify it against the chain head key **from the shelf**, and
    host it gated. Shelf rental, not account creation.
  * ``POST /push-car?did=`` — the example "router": a device pushes its full
    pen-signed CAR; the host re-verifies against the CURRENT chain head key
    (a ``did:web`` main repo) or the DID's own key (a ``did:key`` device
    repo, hosted on first push), refuses a checkpoint whose rev is not
    newer than the hosted head (a replay), and replaces the hosted state. The host can
    drop or delay, never forge — every byte it holds is signed by a key it
    does not have.

At no point does the builder hold a user secret or sign user content: hosted
repos carry a throwaway local key that never signs (imports replace state
wholesale), and reads are checkpoint CARs. A production router would also
emit sync frames per import so firehose subscribers see updates live; this
example keeps to checkpoint reads for clarity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from fastatp import Peer, PeerServer, crypto
from fastatp.crypto import PublicKey
from fastatp.did.document import DIDDocument
from fastatp.exceptions import FastATPError
from fastatp.repo.repo import Repo
from fastatp.server.browser_gate import BrowserGate
from fastatp.server.did_server import DIDServer
from fastatp.server.peer_server import RepoHandle


@dataclass
class ExampleBuilder:
    """Everything a hosted-peers builder runs, built from shipped parts."""

    domain: str
    shelf: DIDServer
    peers: PeerServer
    gate: BrowserGate
    chain_store: dict[str, dict]
    handles: dict[str, RepoHandle] = field(default_factory=dict)

    def head_key(self, twin_did: str) -> PublicKey:
        """The identity's CURRENT signing key, from its shelved chain head.

        Re-resolved on every use so a rotation (a longer chain POSTed to the
        shelf) takes effect immediately — the router never pins a stale pen.
        """
        slug = twin_did.rsplit(":", 1)[-1]
        entry = self.chain_store.get(slug)
        if entry is None:
            raise KeyError(f"{twin_did} is not shelved here")
        head = DIDDocument.from_json_dict(entry["chain"][-1]["document"])
        return head.get_verification_method().public_key()

    def owner_pen(self, repo: Repo) -> str | None:
        """The owner-pull rule: which pen writes this repo *right now*, as a
        ``did:key``, from the chain head rather than the hosted head commit —
        so a freshly recovered pen pulls before it has pushed anything, and
        the old pen is out the moment the longer chain lands."""
        try:
            return crypto.encode_did_key(self.head_key(repo.did))
        except (KeyError, FastATPError):  # not shelved, or a head we cannot read: nobody's owner
            return None


def make_builder(domain: str) -> ExampleBuilder:
    chain_store: dict[str, dict] = {}

    async def resolver(slug: str) -> DIDDocument | None:
        entry = chain_store.get(slug)
        if entry is None:
            return None
        return DIDDocument.from_json_dict(entry["chain"][-1]["document"])

    async def chain_resolver(slug: str) -> dict | None:
        return chain_store.get(slug)

    def shelf_store(slug: str, chain) -> None:
        chain_store[slug] = chain.to_json()

    shelf = DIDServer(domain, resolver, chain_resolver=chain_resolver, shelf_store=shelf_store)

    peers = PeerServer(Peer(did=f"did:web:{domain}", signing_key=crypto.generate_keypair("k256")))
    gate = BrowserGate()
    gate.install(peers)

    builder = ExampleBuilder(
        domain=domain, shelf=shelf, peers=peers, gate=gate, chain_store=chain_store
    )

    async def signup(request: Request) -> Response:
        """Connection zero, host side: 'host this repo I give you'."""
        twin_did = request.query_params.get("did")
        if not twin_did:
            return JSONResponse({"error": "MissingDid"}, status_code=400)
        if twin_did in builder.handles:
            return JSONResponse({"error": "AlreadyHosted"}, status_code=409)
        try:
            key = builder.head_key(twin_did)  # must be shelved FIRST
        except KeyError:
            return JSONResponse({"error": "NotShelved"}, status_code=404)
        car = await request.body()
        # throwaway local key: it never signs — imports replace state wholesale
        repo = Repo(twin_did, crypto.generate_keypair("k256"))
        try:
            repo.import_car(car, verify_key=key)
        except FastATPError as exc:
            return JSONResponse({"error": "BadCar", "message": str(exc)}, status_code=400)
        try:
            # register() refuses a duplicate DID atomically — it, not the
            # early fast-path check above, is the uniqueness authority (two
            # concurrent signups both pass the check across the body await;
            # only one can register)
            # gated (the default) with owner pull: the chain head's pen, and
            # the identity through its delegations, read it back; nobody else
            builder.handles[twin_did] = peers.register(repo, owner_pull=builder.owner_pen)
        except ValueError:
            return JSONResponse({"error": "AlreadyHosted"}, status_code=409)
        return JSONResponse({"hosting": twin_did}, status_code=201)

    async def push_car(request: Request) -> Response:
        """The example router: pen-signed state in, verified, re-hosted.

        Two kinds of repo arrive here. The identity's main repo (``did:web``)
        is verified against the CURRENT chain head from the shelf. A device
        repo (``did:key``) is its own pen's repo — each device writes its own
        — so it is verified against the DID itself, and the first push hosts
        it, gated with owner pull on (the default rule: the DID's key
        verifies the head), so only that pen can read it back.
        """
        did = request.query_params.get("did") or ""
        car = await request.body()
        if did.startswith("did:key:"):
            try:
                key = crypto.decode_did_key(did)
            except (FastATPError, ValueError):
                return JSONResponse({"error": "BadDid"}, status_code=400)
            handle = builder.handles.get(did)
            if handle is None:
                repo = Repo(did, crypto.generate_keypair("k256"))  # throwaway, never signs
                try:
                    repo.import_car(car, verify_key=key)
                except FastATPError as exc:
                    return JSONResponse({"error": "BadCar", "message": str(exc)}, status_code=400)
                try:
                    builder.handles[did] = peers.register(repo, owner_pull=True)
                except ValueError:
                    return JSONResponse({"error": "AlreadyHosted"}, status_code=409)
                return JSONResponse({"hosting": did}, status_code=201)
        else:
            handle = builder.handles.get(did)
            if handle is None:
                return JSONResponse({"error": "NotHosted"}, status_code=404)
            try:
                key = builder.head_key(did)
            except KeyError:
                return JSONResponse({"error": "NotShelved"}, status_code=404)
        try:
            incoming, _entries, _blocks = Repo.read_car(car, verify_key=key)
        except FastATPError as exc:
            return JSONResponse({"error": "BadCar", "message": str(exc)}, status_code=400)
        # a signed checkpoint can be replayed; an older one must never roll the
        # hosted state back. Revs rise strictly across commits, so the head
        # must move forward. (A durable router would also persist this.)
        if incoming.rev <= handle.repo.rev:
            return JSONResponse(
                {"error": "Stale", "message": f"hosted rev {handle.repo.rev} is not older"},
                status_code=409,
            )
        try:
            handle.repo.import_car(car, verify_key=key)
        except FastATPError as exc:
            return JSONResponse({"error": "BadCar", "message": str(exc)}, status_code=400)
        return JSONResponse({"accepted": handle.repo.rev})

    peers.app.router.routes.append(Route("/signup", signup, methods=["POST"]))
    peers.app.router.routes.append(Route("/push-car", push_car, methods=["POST"]))
    return builder


def main() -> None:  # pragma: no cover - runnable demo
    import asyncio

    import uvicorn

    builder = make_builder("shelf.example")

    async def serve() -> None:
        shelf = uvicorn.Server(uvicorn.Config(builder.shelf.app, port=8100, log_level="info"))
        peers = uvicorn.Server(uvicorn.Config(builder.peers.app, port=8101, log_level="info"))
        print("shelf:  http://127.0.0.1:8100   (DID docs, chains, shelf intake)")
        print("peers:  http://127.0.0.1:8101   (gated repos, browser gate, signup)")
        await asyncio.gather(shelf.serve(), peers.serve())

    asyncio.run(serve())


if __name__ == "__main__":  # pragma: no cover
    main()
