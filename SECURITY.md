# Security policy

## Reporting a vulnerability

Report privately through GitHub's security advisory form for this repository:

https://github.com/jacoblapenna/fastatp/security/advisories/new

Please do not open a public issue or pull request for a vulnerability. The
advisory form keeps the report private between you and the maintainer, supports
a threaded discussion, and can issue a CVE when a fix ships.

You can expect an acknowledgement within a week. Fixes are released as a patch
version with a CHANGELOG entry and a published advisory. Credit is given unless
you ask otherwise.

## What counts as a security issue

FastATP's whole value is that a record verifies as coming from exactly who it
claims. Anything that weakens that is a security issue, even when it looks like
a conformance detail:

- A signature that verifies but shouldn't: high-S or DER-encoded ECDSA
  accepted, a wrong curve or multicodec prefix accepted, a rotation key
  accepted as a signing key.
- A record proof, commit, or Merkle Search Tree path that verifies against the
  wrong root, or a CAR import that admits blocks the signed root doesn't cover.
- DAG-CBOR or CID handling that lets two different byte sequences claim the
  same content identifier, or accepts a non-canonical encoding.
- DID resolution that returns keys for a document other than the one asked
  for, or that can be steered to a resolver the caller did not choose.
- Capability or admission logic where a revoked or absent grant still permits
  access, or a private-channel reply readable by a peer it wasn't addressed to.
- Key material written anywhere it shouldn't be, or logged.

Interop deviations from the AT Protocol specification or the vendored
conformance vectors are treated as bugs; where they affect verification they
are handled as security issues.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |

Only the latest minor release line receives fixes. Pin a version in production
and watch the releases page or the changelog.

## Scope notes

- FastATP does not encrypt records. Confidentiality comes from reachability
  and isolation, not ciphertext; a report that records are readable by a peer
  who was granted access is working as designed.
- The browser peer bundle is built from the Rust crates under `rust/` and
  ships pre-built in the package. A vulnerability in that bundle is in scope
  and reported the same way.
- Dependencies are tracked by Dependabot. Upstream vulnerabilities in
  `cryptography`, `pydantic`, or the server stack are best reported upstream;
  open an issue here if a pinned minimum needs raising.
