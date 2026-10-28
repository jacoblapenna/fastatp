"""One-shot posting CLI: commit a profile and/or post to the durable repo.

Run this while peer.py is STOPPED (both open the same durable state; sqlite
+ the head file are single-writer). Commits made here are picked up by
relays when they next crawl (`getRepo`) — a relay does not need to witness
the live frame. Start peer.py again afterwards.

The pen never leaves the state dir; there is no login because there is
nothing to log into — you ARE the keyholder.

Usage:
  python post.py --domain alice.example.com --state ./state \
      --profile "Alice" --text "hello from a sovereign peer" [--image cat.png --alt "a cat"]
"""

from __future__ import annotations

import argparse
import mimetypes
from pathlib import Path

import records

from fastatp.identity import Identity
from fastatp.repo.blobs import SqliteBlobStore
from fastatp.tid import TidClock


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--profile", help="set/replace the profile displayName")
    ap.add_argument("--description", default="a sovereignly hosted FastATP peer")
    ap.add_argument("--text", help="post this text")
    ap.add_argument("--image", help="attach this image file to the post")
    ap.add_argument("--alt", default="", help="alt text for the image")
    args = ap.parse_args()

    state = Path(args.state)
    identity = Identity.bootstrap(domain=args.domain, state_dir=state)
    identity.repo.blobs = SqliteBlobStore(state / "blobs.sqlite")

    if args.profile:
        identity.repo.put_record(
            "app.bsky.actor.profile", "self", records.profile(args.profile, args.description)
        )
        print(f"profile  : displayName={args.profile!r}")

    if args.text:
        image_blob = None
        if args.image:
            data = Path(args.image).read_bytes()
            mime = mimetypes.guess_type(args.image)[0] or "application/octet-stream"
            image_blob = identity.repo.add_blob(data, mime).model_dump(by_alias=True)
            print(f"blob     : {image_blob['ref']['$link']} ({mime}, {len(data)} bytes)")
        rkey = TidClock().now()  # posts use TID record keys
        event = identity.repo.put_record(
            "app.bsky.feed.post",
            rkey,
            records.post(args.text, image_blob=image_blob, image_alt=args.alt),
        )
        print(f"post     : at://{identity.did}/app.bsky.feed.post/{rkey}")
        print(f"rev      : {event.rev}")

    if not args.profile and not args.text:
        print("nothing to do: pass --profile and/or --text")


if __name__ == "__main__":
    main()
