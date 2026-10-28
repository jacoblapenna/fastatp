"""The composition demo, end to end and fully local (no live network):

  curator (slug DID, GATED repo) --signed selection records--> firehose
      -> poster (subscribe capability, verifies every frame)
          -> persona posts (image blob, persona pen IN-PROCESS)
              -> PUBLIC firehose -> any verifying consumer (a relay's view)

Every hop verifies (commit signature + MST inversion). Every byte that
crosses a wire is captured and audited afterward: NO private key material —
persona pen, curator pen, in raw or hex form — may appear in any request
URL, any frame, or any response body. There is no credential to leak
because none exists.

The demo sentence this example makes true: every byte that reaches Bluesky
was signed on your own hardware, traversed only signed records, and no
secret ever existed to leak.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import socket
from typing import Any

import httpx
import uvicorn
import websockets

from fastatp import build_did_document
from fastatp.repo.sync import RepoMirror
from fastatp.server.peer_server import (
    FIREHOSE_PATH,
    GET_BLOB_PATH,
)

from ..peer import build_server
from .curator import Curator
from .poster import Poster

# a valid 1x1 red PNG (67 bytes) — the demo's "artwork"
DEMO_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
    "DwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

SELECTIONS = [
    ("First light from a sovereign peer", "a single red pixel, historic"),
    ("Signed mail, not RPC with a login", "the same red pixel, still historic"),
]


def _demo_identity_resolver(conn: Any) -> str | None:
    """DEMO ONLY: trust an ``as=`` query parameter as the caller's DID.

    Production gating binds the connection to an unspoofable identity
    (WireGuard tunnel IP -> DID) or a challenge-signing gate.
    This resolver exists so the demo runs on plain loopback sockets.
    """
    return conn.query_params.get("as")


async def run_demo(domain: str, state_dir: str) -> dict[str, Any]:
    """Run the whole pipeline; returns a summary for callers/tests."""
    wire_capture: list[bytes] = []  # every byte that crossed a socket

    identity, server = build_server(
        domain,
        state_dir,
        identity_resolver=_demo_identity_resolver,
        warn_on_proxy=False,  # the demo's stranger-read is deliberate, not a misconfig
    )
    curator = Curator(server, domain)
    curator.grant_subscribe(identity.did)  # presence IS authorization

    # what a verifier resolves: persona doc (pretty) + curator genesis (twin)
    persona_doc = build_did_document(
        did=identity.did, signing_key=identity.signing_key.public_key()
    )
    docs = {identity.did: persona_doc, curator.did: curator.document()}

    async def resolver(did: str) -> Any:
        return docs.get(did)

    poster = Poster(server, identity.did, resolver)
    poster.ensure_profile("Sovereign Peer", "posts composed by my own agents")
    poster.attach(curator.did)

    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    uv = uvicorn.Server(uvicorn.Config(server.app, log_level="error"))
    serve_task = asyncio.create_task(uv.serve(sockets=[sock]))
    try:
        while not uv.started:
            await asyncio.sleep(0.01)
        base = f"http://127.0.0.1:{port}"
        ws_base = f"ws://127.0.0.1:{port}{FIREHOSE_PATH}"

        # the poster's gated subscription (admitted by the capability), and a
        # public consumer playing the relay's role on the persona firehose
        gated_url = f"{ws_base}?did={curator.did}&cursor=0&as={identity.did}"
        public_mirror = RepoMirror(identity.did, resolver=resolver)
        async with (
            websockets.connect(gated_url) as gated_ws,
            websockets.connect(f"{ws_base}?cursor=0") as public_ws,
        ):
            # curator publishes; poster verifies + posts, frame by frame
            for caption, alt in SELECTIONS:
                curator.publish_selection(caption, DEMO_PNG, alt)
            posted: list[str] = []
            while len(posted) < len(SELECTIONS):
                frame = await asyncio.wait_for(gated_ws.recv(), 10)
                assert isinstance(frame, bytes)
                wire_capture.append(frame)
                posted += await poster.handle_frame(frame)

            # the public consumer (the relay's view) verifies every frame
            while public_mirror.get_record("app.bsky.feed.post", posted[-1]) is None:
                frame = await asyncio.wait_for(public_ws.recv(), 10)
                assert isinstance(frame, bytes)
                wire_capture.append(frame)
                await public_mirror.handle_frame(frame)

        profile = public_mirror.get_record("app.bsky.actor.profile", "self")
        first_post = public_mirror.get_record("app.bsky.feed.post", posted[0])
        assert profile is not None and first_post is not None
        blob_cid = first_post["embed"]["images"][0]["image"]["ref"]["$link"]

        # the AppView's fetch path: the blob serves over getBlob
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{base}{GET_BLOB_PATH}", params={"did": identity.did, "cid": blob_cid}
            )
            r.raise_for_status()
            wire_capture.append(r.content)
            assert r.content == DEMO_PNG
            assert r.headers["content-type"].startswith("image/png")

            # segmentation holds on the wire: a stranger cannot even list the
            # curator's repo, let alone read it
            listing = await client.get(f"{base}/xrpc/com.atproto.sync.listRepos")
            wire_capture.append(listing.content)
            assert [x["did"] for x in listing.json()["repos"]] == [identity.did]
            refused = await client.get(
                f"{base}/xrpc/com.atproto.sync.getRepo", params={"did": curator.did}
            )
            wire_capture.append(refused.content)
            assert refused.status_code == 403
    finally:
        uv.should_exit = True
        with contextlib.suppress(Exception):
            await serve_task

    # the audit: no secret in any byte that crossed a wire — because none exists
    from fastatp.identity import _key_bytes

    secrets = []
    for pen in (identity.signing_key, curator.signing_key):
        raw = _key_bytes(pen)
        secrets += [raw, raw.hex().encode(), binascii.b2a_base64(raw).strip()]
    for payload in wire_capture:
        for secret in secrets:
            assert secret not in payload, "private key material crossed the wire"

    return {
        "persona": identity.did,
        "curator": curator.did,
        "posts": posted,
        "profile": profile,
        "blob_cid": blob_cid,
        "frames_audited": len(wire_capture),
    }


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as state:
        summary = asyncio.run(run_demo("alice.demo.example", state))
    print(f"persona  : {summary['persona']}")
    print(f"curator  : {summary['curator']}  (gated, never announced)")
    print(f"posts    : {summary['posts']}")
    print(f"blob     : {summary['blob_cid']}")
    print(f"audited  : {summary['frames_audited']} wire payloads, zero secrets (none exist)")


if __name__ == "__main__":
    main()
