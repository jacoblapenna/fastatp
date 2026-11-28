# Where FastATP stands

FastATP is a Python library for the AT Protocol (atproto). The name is
FastATP, not FastAPI: the two are unrelated. It is young. The first commit
is from October 2026, it is in 0.x versions, and it has one maintainer.
This page says what has been checked, what has not, and where its limits
are, so you can judge it without taking the rest of the site on trust.

## What has been checked, and by what

Each of these runs on every pull request, and each links to the test that
does it.

- **The wire format, against atproto's own test vectors.** The conformance
  vectors Bluesky publishes for syntax, crypto, the data model, the repo's
  tree, the firehose and lexicons are in the repository and never skipped.
  [Where they come from](../../tests/vectors/PROVENANCE.md).
- **The firehose, by Bluesky's own validator.** `goat`, Bluesky's
  command-line tool, checks the frames a FastATP peer sends.
  [The test](../../tests/test_goat_oracle.py).
- **The bytes, against another codec.** Encoded records and their content
  IDs are compared with `libipld`, an independent Rust implementation.
  [The test](../../tests/cross_sdk/test_harvested.py).
- **A repo, read by software that does not know FastATP.** An unmodified
  third-party package built on the `atproto` Python SDK reads a repo that
  FastATP exported. [The test](../../tests/test_sensorthings_interop.py).
- **A second implementation.** The wire-format core is written twice, in
  Python and in Rust, and the browser peer is built from the Rust one.
  [The crates](../../rust/crates).
- **Access control, on a real tunnel.** Granting and revoking a capability
  opens and closes a real WireGuard tunnel between network namespaces.
  [The test](../../tests/test_wireguard_netns.py).
- **Every tutorial, played end to end.** Each guide's commands run in
  order on real machines in Docker, and a command that should be refused
  must be refused. [How the tests run](../../docs/TESTING.md).

The suite runs on Python 3.11 to 3.14 and fails under 85% coverage. A test
that is skipped in every job fails the build, so none can quietly stop
running.

## What has not been done

- **No independent security audit.** Nobody outside the project has
  reviewed the code. [The security policy](../../SECURITY.md) says what
  counts as a vulnerability and how to report one.
- **No stable release.** In 0.x a minor release may change or remove
  public API. [The changelog](../../CHANGELOG.md) lists each break.
- **No known production use.** The maintainer knows of no deployment
  outside the project's own tests and tutorials.

## Known limits

These are choices, not bugs, and they decide where FastATP fits.

- **Records are never encrypted.** Privacy comes from who can reach a
  repo, not from ciphertext. A peer you admit can read what it reaches.
- **A host can read what it serves.** People who will not run a server
  need a host for their repo. That host cannot forge or alter a record,
  but it can read the repos it holds, see who connects and when, and
  refuse to serve them. [The design note](../../docs/HOSTED-PEERS.md)
  works through it.
- **A `did:web` identity rests on a domain, unless the reader checks its twin.** Whoever controls the domain's DNS and web server controls the
  DID document it serves. FastATP adds a twin to the document, an "also
  known as" entry bound to a signed history of the keys, and a FastATP
  peer [checks it](../api/ContinuityResolver.md): to such a reader a
  thief needs the domain and the pen, not the domain alone. Ordinary
  atproto software does not check the twin, so to it the domain is still
  enough.
- **Private repos need WireGuard.** A machine joins a tunnel with a kernel
  interface, which takes privileges. A browser cannot join one at all, so
  a person in a browser reaches an app's peer, and that peer hosts their
  repo.
- **It takes two.** A record is only useful to a counterparty that reads
  it. FastATP speaks atproto's sync protocol, so atproto tools can read a
  public repo, but the private side needs the other party to run a peer
  as well.

## When something else fits better

The README says when
[MCP or A2A is the better choice](../../README.md#when-mcp-or-a2a-is-the-better-choice):
one model using local tools in one session, or agents inside web login
infrastructure that already exists.

## Check it yourself

You do not have to take this page on trust either. A coding agent can
[run a tutorial by itself](../../tutorials/AGENTS.md), in about five
minutes, and report what the machines did.
