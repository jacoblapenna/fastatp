"""The peer process: one PUBLIC persona repo + gated internal repos, one server.

This is the segmentation rule demonstrated, not just documented:

- the persona repo (``did:web:<domain>``) is PUBLIC — a Bluesky-visible repo
  is by definition ungated; the relay's read path is unauthenticated;
- internal agents (here: the curator) are slug-DID twins hosted GATED on the
  SAME process — the doorman admits only capability holders, and these DIDs
  are never announced to any relay.

Run it behind any https tunnel (Cloudflare Tunnel / Tailscale Funnel — the
DID doc's serviceEndpoint may point at any https host). WebSockets pass
through both. There is no login surface anywhere in this file: writes are
in-process (the pen lives here); reads are open (public repo) or
capability-gated (internal repos).

Usage:
  python peer.py --domain alice.example.com --state ./state [--port 8100]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fastatp.identity import Identity
from fastatp.repo.blobs import SqliteBlobStore


def build_server(domain: str, state: str | Path, *, identity_resolver=None, **kwargs):
    """The composed server: persona identity + blob store, ready to host.

    Returns ``(identity, server)``. The gated curator repo is registered by
    the pipeline when it starts (see pipeline/demo.py) — a peer with no
    internal agents hosts just the public persona repo.
    """
    state = Path(state)
    identity = Identity.bootstrap(domain=domain, state_dir=state)
    # the media capability is optional wiring — a persona posts
    # images, so this peer wires a durable blob store into its repo
    identity.repo.blobs = SqliteBlobStore(state / "blobs.sqlite")
    server = identity.create_server(identity_resolver=identity_resolver, **kwargs)
    return identity, server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--host", default="127.0.0.1", help="bind address (tunnel origin)")
    ap.add_argument("--port", type=int, default=8100)
    args = ap.parse_args()

    identity, server = build_server(args.domain, args.state)
    print(f"hosting {identity.did} (public) on {args.host}:{args.port}")
    print("firehose: /xrpc/com.atproto.sync.subscribeRepos")
    server.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
