"""One-shot com.atproto.sync.requestCrawl: announce YOUR host to A relay.

The relay hostname is EXPLICIT OPERATOR CONFIGURATION — FastATP ships no
relay hostname, not even as a default here (the anti-registry
rule extended to relays). You choose whom to announce to.

The request is an unauthenticated POST of ``{"hostname": ...}``; the relay
then crawls your peer's public repo(s) over the same unauthenticated sync
surface. No account, no token, no secret exists in this exchange.

Mind the operational envelope (see README): early-access federation
rate-limits self-hosted hosts (~10 crawled accounts, ~1500 evt/hr). Never
point agent swarms at a public relay — internal repos stay gated and
unannounced.

Usage:
  python request_crawl.py --relay <relay-host-you-chose> --hostname peer.example.com
"""

from __future__ import annotations

import argparse

import httpx


def request_crawl(relay: str, hostname: str) -> dict:
    if not relay.startswith(("http://", "https://")):
        relay = f"https://{relay}"
    response = httpx.post(
        f"{relay}/xrpc/com.atproto.sync.requestCrawl",
        json={"hostname": hostname},
        timeout=30,
    )
    response.raise_for_status()
    return {"status": response.status_code}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--relay", required=True, help="relay host — YOUR choice; no default exists")
    ap.add_argument("--hostname", required=True, help="your peer's public hostname")
    args = ap.parse_args()
    result = request_crawl(args.relay, args.hostname)
    print(f"requestCrawl accepted by {args.relay}: {result}")


if __name__ == "__main__":
    main()
