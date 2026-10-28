"""Recovery path 1: rotate the signing pen, keep the identity.

Turns the page with the COLD rotation pen: mints a fresh signing key, appends
a rotation-signed document to the continuity chain (``Identity.rotate`` checks
the pen against the current head's ``rotationKeys`` before anything mutates),
persists the new key + chain, and re-signs the repo head. Same DID, same twin,
same record CIDs — every strong ref anyone holds to your records still
matches; continuity verifiers see a legitimate, pre-authorized event.

The rotation pen is prompted for (never passed on argv — shell history) or
read from ``--pen-file``. ``rotationKeys`` carry over unchanged: the same cold
pen remains the recovery authority after this rotation.

The emitted ``--out`` tree is the moment-of-truth artifact: the previously
published ``did.json``/``chain.json`` are stale the instant rotation succeeds.
Republish it as the site root, restart the peer, and update any
password-manager copy of ``signing.key``.

Run with the peer STOPPED (single writer). The domain and curve come from the
state dir itself; there is nothing to retype wrong.

Usage:
  python rotate.py --state ../bluesky_peer/state \
      --pds https://YOUR-TUNNEL-HOSTNAME --out ../bluesky_peer/static
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from fastatp import build_did_document
from fastatp.crypto import generate_keypair, load_private_key_bytes
from fastatp.exceptions import DIDDocumentInvalid
from fastatp.identity import Identity


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", required=True, help="durable identity state dir")
    ap.add_argument("--pds", required=True, help="public https URL of YOUR peer")
    ap.add_argument("--out", required=True, help="output dir for the refreshed static artifacts")
    ap.add_argument(
        "--pen-file", help="file holding the rotation pen hex (default: prompt, no echo)"
    )
    args = ap.parse_args()

    state = Path(args.state)
    if not (state / "identity.json").exists():
        # never fall through to bootstrap-as-mint: rotating requires the
        # existing identity, and a bare state dir would mint a fresh one
        sys.exit(f"{state} has no identity.json — nothing to rotate. Restore it first.")

    identity = Identity.bootstrap(state_dir=state)
    if identity.chain is None or identity.domain is None:
        sys.exit(f"{identity.did} is a did:key — a did:key's name IS its key; nothing rotates")

    if args.pen_file:
        pen_hex = Path(args.pen_file).read_text().strip()
    else:
        pen_hex = getpass.getpass("rotation pen (hex, from your password manager): ").strip()
    try:
        rotation_pen = load_private_key_bytes(bytes.fromhex(pen_hex), identity.curve)
    except ValueError as exc:
        sys.exit(f"that is not a valid {identity.curve} key: {exc}")

    new_signing_key = generate_keypair(identity.curve)
    try:
        identity.rotate(new_signing_key, rotation_pen)  # rejects a wrong pen before mutating
    except DIDDocumentInvalid as exc:
        sys.exit(f"rotation refused (nothing changed): {exc}")
    print(f"rotated: {identity.did} now signs with a fresh {identity.curve} key")
    print(f"         chain has {len(identity.chain.pages)} pages; rotationKeys unchanged")

    # re-emit the static artifacts, same layout as bootstrap_identity.py —
    # the published copies are stale the moment the rotation above succeeded
    doc = build_did_document(
        did=identity.did,
        signing_key=identity.signing_key.public_key(),
        also_known_as=[f"at://{identity.domain}", identity.chain.twin_did],
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

    print(f"artifacts: republish {out}/ as the site root of https://{identity.domain}/")
    print("then: restart the peer; update any password-manager copy of signing.key")


if __name__ == "__main__":
    main()
