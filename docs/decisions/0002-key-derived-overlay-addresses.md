# 0002: Key-derived overlay addresses

- Date: 2026-11-11
- Status: accepted
- Tenets served: no trusted third party (1); data, not calls (2); no shared
  secrets (7)

## Problem

WireGuard's handshake agrees on session keys, not on addresses. Each side
must already know the inner address the other will use: the host programs
"key K may send from address A", and the peer puts A on its own interface.
Someone has to make the two ends agree on A.

Before this record, FastATP had two ways, both wrong once a peer serves
repos for pens it does not carry (`docs/decisions/0001`):

- A `did:web` peer chose its own address and published it in its DID
  document next to its key. Two peers who chose the same address could not
  both be admitted by one host.
- A documentless `did:key` grantee's address was written by the grantor
  into the capability record (`wg_tunnel_ip`). The doorman then built its
  "which address is which DID" map by reading those fields out of every
  repo the host served, which made the map only as honest as the records in
  it. A tenant could write a record naming their own address as someone
  else's DID (review finding #1 on PR #23), or naming someone else's address
  (finding #19). The first was an impersonation until proofs were scoped;
  the second was a denial either way.

Both were symptoms of one thing: an address that lives in a record is a
claim, and any repo can write any claim.

## Decision

An address is a pure function of a key. Every peer's overlay address is

```text
OVERLAY_PREFIX + first 64 bits of SHA-256(raw 32-byte WireGuard public key)
```

where `OVERLAY_PREFIX` is one IPv6 unique-local /64 (RFC 4193), chosen at
random once and fixed in the source. Both ends of every tunnel compute each
other's address from keys they already exchange, so no address is ever
assigned, published, negotiated or stored. `overlay_address` is the function;
a vector pins it, because a change to it is a change to every peer's
address at once.

Consequences in the code:

- `Capability.wg_tunnel_ip` is removed. `wg://` URIs, the DID document's
  `WireGuardFirehose` service entry, attestations, `WireGuardReconciler.grant`
  and `PeerApp.open_private_channel` carry a key only. A URI that still
  carries `ip=` is refused rather than ignored, since the address it names
  is not the one the key derives.
- The doorman derives each granted key's address to build its map, so a
  record cannot claim an address it does not hold the key for. Findings #1
  and #19 have nothing left to lie about. The scoped-proof rule from 0001
  stays as defence in depth.
- Private channels derive their address from the channel's own fresh key;
  the per-channel allocator is gone.
- The prefix is a protocol constant like a port number, not a registry: it
  names no service. A builder who changes it gets an isolated network whose
  peers can never talk to peers at large, which is what a VPN-like
  deployment wants and what an app whose users should meet other apps'
  users must never do.

## Why not the alternatives

- **A central allocator** (what Tailscale's control plane does, handing
  each node an address from `100.64.0.0/10` and pushing the map to every
  node): a registry in the middle of every connection. Tenet 1.
- **The host assigns and tells the peer**: the peer cannot be told over the
  tunnel it cannot yet use, so the address would have to travel out of band
  in a record, which is either public or behind the very tunnel in question.
  Circular.
- **Peers choose their own**: collisions on any host with many followers,
  and a record naming an address it does not own.

Deriving from the key is established practice: IPv6 cryptographically
generated addresses (RFC 3972), and mesh networks such as Yggdrasil and
cjdns, where the whole address space is a hash of the public key.

## Consequences and limits

- **IPv6 only, on the overlay.** An IPv4 range has too few bits to carry a
  key hash without collisions. This says nothing about the transport:
  WireGuard's UDP rides IPv4-only networks, NATs and home routers unchanged,
  and the rootless real-tunnel test proves it by bringing the derived
  overlay up over an IPv4-only underlay. The one real requirement is that
  the local kernel will put an IPv6 address on a virtual interface, which
  every kernel has done for twenty years and which `disable_ipv6=1`
  (hardened servers; Docker containers by default) turns off.
  `check_overlay_support` fails with the exact sysctl to flip, and
  `WgToolInterface` runs it on construction.
- **Anyone can derive anyone's address.** A key is public; so is the
  address it derives, and it is exactly as useful: nothing, without the
  private key, because the kernel drops packets from keys it has not been
  told about before any handshake can start. The overlay is a star around
  each host, not a LAN, so a granted peer cannot reach another grantee
  through the host either.
- **The WireGuard key is not a pen and has no DID.** A repo is three things
  that go together: its contents, the one pen that signs them, and the DID
  that is that pen's public side (`did:key`) or that names its current pen
  (`did:web`). None of that is what WireGuard sees. A WireGuard key is an
  X25519 key, a curve no pen uses. It opens tunnels and nothing else: it
  signs no record, and no DID is made of it. A machine that tunnels holds
  one *in addition to* whatever pens it holds, and the address this record
  derives is a function of that key alone. (A `did:web` identity also has a
  paper secret, the key that rotates its document; it never touches the
  tunnel either.)
- **A capability record is what puts a DID on a tunnel, and it always
  carries the key.** The kernel admits WireGuard keys; the doorman admits
  DIDs. The bridge is a record in the gated repo that says "the peer whose
  WireGuard key is K is identity D, and may subscribe". The doorman walks
  those records, derives an address from each K, and names a caller
  arriving from that address as D. So the record must carry K, whatever
  kind of DID D is; a record without it admits nobody at the door, however
  the kernel was programmed. Where K comes from is the only thing that
  differs by DID kind. A `did:web` grantee has a document and publishes its
  own K there (the `WireGuardFirehose` entry): the grantor's `grant`
  resolves the document and writes the K it found into the record. A
  `did:key` grantee has no document, so the grantor learned K out of band
  (a self-attestation) and passes it to `grant`, which writes it. Either
  way the address is derived from K, so no record can claim an address its
  key does not own. This is the record the getting-started tutorial writes
  by hand; before 0.3 the library helper left K out for `did:web` grantees,
  which admitted them at the kernel and refused them at the door.
- **A DID's WireGuard key says where its tunnel is, not whether its repo is
  private.** The key on a `did:web` document is read by two parties: a
  grantor, to learn which key to admit, and a counterparty, to learn where
  to point its own interface. A `did:web` repo is normally public and the
  gated repos its machine serves are `did:key` repos (a private channel, a
  device repo), so the key on the document is about the machine that hosts
  them. The library does not forbid gating a `did:web` repo; whether an
  app's roots are always public is the app's rule.
- **Rotating a WireGuard key moves the address, and makes the peer a
  stranger until it is granted again.** The record is what the grantor
  signed: "this key is bob". The reconciler enforces the record and never
  consults a document after grant time, so when bob publishes a new key,
  nothing follows it; his old key stays admitted and his new one cannot
  handshake. That is deliberate. A grant that silently followed bob's
  document would hand bob's seat to whoever controls `bob.lan` the day
  they publish their own key, and a reconciler that writes grants on its
  own is a reconciler that decides who is admitted. So rotation is the
  same for every grantee, `did:web` or `did:key`: ask the grantor to grant
  again; `grant` supersedes the old record in the same commit and the old
  address goes with it. An app that watches its grantees' documents can
  tell the grantor "bob rotated his key, admit him again?", and that
  question, out of band, is the app's to ask. Either way the rotating peer
  re-runs its own interface setup with the new address, or the host's
  kernel drops every packet it sends.
- **Collisions** are a 1-in-2^64 event; a grant that would produce an
  address already granted on the same host can be refused outright.
- **The doorman's map is still an O(N) walk** per gated read, N being every
  capability the host serves. Right for a peer with a few repos. A host at
  scale keeps a cache of "address → DID, granting repo" in its own database,
  updated as owners write and delete capabilities, and the doorman still
  admits by the capability currently in the named repo; that is in the
  bring-your-own-database work.
