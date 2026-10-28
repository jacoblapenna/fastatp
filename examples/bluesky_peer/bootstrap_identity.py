"""Bootstrap a Bluesky-visible FastATP identity and emit its STATIC artifacts.

One subdomain = one atproto-visible identity. This script
mints (or reloads) the durable identity for ``did:web:<domain>`` and writes
the three static files the network needs — host them anywhere (a CDN page
survives your peer being down; the DID doc's serviceEndpoint may point at
any https host):

  <out>/.well-known/did.json         -> the pretty document (did:web resolution)
  <out>/.well-known/atproto-did      -> the handle file (handle -> DID)
  <out>/<slug>/did.json              -> the twin's current document (chain head)
  <out>/<slug>/chain.json            -> the twin's rotation-signed chain —
                                        FastATP's continuity resolver fetches
                                        exactly this path; the network sees
                                        only the pretty DID

Serve the whole <out> tree as the site root of https://<domain>/.

The did.json here is the *atproto-visible* pretty document: same signing key
as the chain head, ``alsoKnownAs`` carrying both the ``at://<handle>`` alias
(the handle IS the domain) and the continuity twin, and an
``AtprotoPersonalDataServer`` service entry pointing at YOUR peer's public
URL (a tunnel hostname works; identity is not tied to the tunnel vendor).

The signing pen is minted into ``--state`` and NEVER leaves it. The rotation
pen (recovery authority) is printed ONCE — store it colder than this box.

Usage:
  python bootstrap_identity.py --domain alice.example.com \
      --pds https://peer.example.com --state ./state --out ./static
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastatp import build_did_document
from fastatp.identity import Identity, _key_bytes  # example-only: printing the recovery pen


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domain", required=True, help="the handle/subdomain, e.g. alice.example.com")
    ap.add_argument("--pds", required=True, help="public https URL of YOUR peer (tunnel hostname)")
    ap.add_argument("--state", required=True, help="durable identity state dir (keys live here)")
    ap.add_argument("--out", required=True, help="output dir for the static artifacts")
    args = ap.parse_args()

    identity = Identity.bootstrap(domain=args.domain, state_dir=args.state)
    assert identity.chain is not None  # did:web bootstrap always builds a chain

    # the atproto-visible pretty document: handle alias + twin alias + PDS
    # service entry, same signing key as the chain head (the continuity
    # resolver enforces that match)
    doc = build_did_document(
        did=identity.did,
        signing_key=identity.signing_key.public_key(),
        also_known_as=[f"at://{args.domain}", identity.chain.twin_did],
        pds_endpoint=args.pds,
    )

    out = Path(args.out)
    slug = identity.chain.twin_did.rsplit(":", 1)[-1]
    (out / ".well-known").mkdir(parents=True, exist_ok=True)
    (out / slug).mkdir(parents=True, exist_ok=True)
    (out / ".well-known" / "did.json").write_text(json.dumps(doc.to_json_dict(), indent=2) + "\n")
    (out / ".well-known" / "atproto-did").write_text(identity.did + "\n")
    (out / slug / "did.json").write_text(
        json.dumps(identity.chain.head.to_json_dict(), indent=2) + "\n"
    )
    (out / slug / "chain.json").write_text(json.dumps(identity.chain.to_json()) + "\n")

    print(f"identity : {identity.did}")
    print(f"handle   : {args.domain}  (serve the .well-known/atproto-did file, or")
    print(f"           publish DNS TXT _atproto.{args.domain} -> did={identity.did})")
    print(f"twin     : {identity.chain.twin_did}")
    print(f"artifacts: serve {out}/ as the site root of https://{args.domain}/")
    if identity.rotation_private_key is not None:
        # printed exactly once at mint; Identity does not persist it
        pen = _key_bytes(identity.rotation_private_key).hex()
        print("\nROTATION PEN (hex, shown ONCE — store cold, never on this box):")
        print(f"  {pen}")


if __name__ == "__main__":
    main()
