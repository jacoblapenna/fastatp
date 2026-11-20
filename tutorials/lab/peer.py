"""One sovereign peer, one machine: python peer.py <domain>

Mints (or reloads) did:web:<domain> in ./<domain>/, serves its DID document on
port 80, hosts its repo on the atproto sync surface, and takes local commits on
/commit. Its WireGuard public key is published in the DID document, and admitting a
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
        # replace the signing key if it ever leaks. FastATP hands it over once,
        # here, in memory, and neither logs it nor writes it to disk. Printing it
        # is this lab's own choice, a stand-in for copying it to cold storage.
        print(f"[{domain}] minted {identity.did}")
        print(
            f"[{domain}] ROTATION PEN (printed by this lab, never by FastATP; cold storage for real):"
        )
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
            Route("/commit", commit, methods=["POST"]),
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


async def commit(request: Request) -> JSONResponse:
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
    #
    # One DID holds one live capability. Admitting someone who is already in
    # replaces their record, so /evict always has exactly one to delete. If
    # they changed tunnel keys since, the key they were admitted with before
    # is shut out here too.
    earlier = live_capabilities(channel.repo).get(body["did"])
    cap = Capability(subject=body["did"], actions=["subscribe"], wg_public_key=info.public_key)
    await channel.put_record(Capability.nsid, generate_tid(), cap)
    if earlier is not None:
        earlier_rkey, earlier_cap = earlier
        await channel.delete_record(Capability.nsid, earlier_rkey)
        if earlier_cap.wg_public_key != info.public_key:
            close_tunnel(earlier_cap.wg_public_key)
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
    # subject, the capability still in force. /admit keeps that to one per
    # DID, and a deleted capability is gone from the tree, so it does not come
    # back.
    live = live_capabilities(channel.repo).get(body["did"])
    if live is None:
        # No record. Either they were never admitted, or an earlier /evict
        # deleted the record and stopped before it closed the tunnel. Their
        # document says which key to look for, so asking again finishes the job.
        key = parse_wireguard_service(await resolve(body["did"])).public_key
        if key not in wg.peers():
            return JSONResponse(
                {"error": f"{body['did']} holds no live capability"}, status_code=404
            )
        close_tunnel(key)
        return JSONResponse({"evicted": body["did"], "deleted": "already deleted"})
    rkey, cap = live
    # The same two steps as admit, undone in the same order: the decision
    # first, then the enforcement. If this stops between the two, what is
    # left is an open tunnel no record explains, which the next /evict (or,
    # in production, the next reconcile) closes. The other order could leave
    # a record with no tunnel, and a reconcile would open it again. Removing
    # the peer also ends any session in flight. Their next packet is dropped
    # unanswered.
    await channel.delete_record(Capability.nsid, rkey)
    close_tunnel(cap.wg_public_key)
    return JSONResponse({"evicted": body["did"], "deleted": f"{Capability.nsid}/{rkey}"})


def close_tunnel(key: str) -> None:
    """Remove a kernel peer, unless a live capability still names its key.

    The records decide. Two DIDs admitted with the same tunnel key share one
    kernel peer, and it closes when the last of their records is gone."""
    if all(cap.wg_public_key != key for _, cap in live_capabilities(channel.repo).values()):
        wg.remove_peer(key)


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
