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

```
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
- **A capability record is what puts a DID on a tunnel.** The kernel admits
  WireGuard keys; the doorman admits DIDs. The bridge is a record that says
  "the peer whose WireGuard key is K is identity D", and where K comes from
  depends on what kind of DID D is. A `did:web` has a document, so D
  publishes its own K there (the `WireGuardFirehose` entry) and the
  grantor's reconciler re-reads it on every reconcile. A `did:key` has no
  document, so the grantor learned K out of band (a self-attestation) and
  embeds it in the record (`wg_public_key`). Either way the address is
  derived from K, so no record can claim an address its key does not own.
- **What each kind of grant reaches.** The doorman builds its "address →
  DID" map from the keys embedded in records, which means it can name a
  `did:key` caller and cannot name a `did:web` one (that key lives in a
  document, not in the record). This is the design, not a gap. A `did:web`
  repo is the identity's public firehose, and a WireGuard grant to a
  `did:web` peer admits it to *that*, where reachability is the whole gate:
  absence is unreachable. Everything gated (a private channel, a device
  repo) is a `did:key` repo, admitted by embedded key, where the doorman
  can name the caller. A `did:web` identity reaches a gated repo through
  its devices: a delegation names a device's pen, and the device is granted
  by its own WireGuard key over the tunnel, or knocks as the identity
  through the browser gate. The hosted-users chain in 0001 is exactly this
  shape: a public `did:web` root, `did:key` devices, private repos read
  through the knock.
- **Rotating a WireGuard key moves the address**, correct by construction,
  and the key is data, so a rotation is a change to data. A `did:web` peer
  publishes the new key in its DID document; every grantor's next reconcile
  re-resolves it, programs the new derived address and removes the old one.
  A documentless peer's key lives in its grantor's capability record, so it
  asks the grantor to grant again; `grant` supersedes the old record in the
  same commit (deleted, with a revocation that says so) and the old address
  goes with it. Either way the rotating peer re-runs its own interface
  setup with the new address, or the host's kernel drops every packet it
  sends. Nothing new to build; this is the existing grant and reconcile
  behaviour, stated.
- **Collisions** are a 1-in-2^64 event; a grant that would produce an
  address already granted on the same host can be refused outright.
- **The doorman's map is still an O(N) walk** per gated read, N being every
  capability the host serves. Right for a peer with a few repos. A host at
  scale keeps a cache of "address → DID, granting repo" in its own database,
  updated as owners write and delete capabilities, and the doorman still
  admits by the capability currently in the named repo; that is in the
  bring-your-own-database work.
