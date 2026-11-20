"""Point this machine's tunnel at another peer, using nothing but that peer's DID document:
python pair.py <peer-did>

This is not a request and not a subscribe. It changes this machine's own
kernel and sends nothing the other peer will answer. The ask ("admit my DID") happens
out of band; that peer's /admit is the other half; follow.py through the tunnel
is the subscribe. FastATP's WireGuard helpers cover the admitting half
(add_peer, remove_peer, the reconciler) and the DID-document service. The admitting peer
needs no endpoint, because WireGuard learns a peer's address from its
handshake. The reader is the asymmetric side: it must know where to send the
first packet and must keep knocking, so this one `wg set` is plain WireGuard.
"""

import asyncio
import subprocess
import sys

from dids import resolver

from fastatp.authz.wireguard import parse_wireguard_service

target = sys.argv[
    1
]  # a DID such as did:web:alice.lan, not an address; the address comes from the document


async def main() -> None:
    # Fetch the other peer's DID document, the same request anyone on the LAN can make,
    # and read the WireGuard service out of it: the public key that answers and
    # the UDP endpoint to send to. The tunnel address that key is allowed to use
    # is not in the document: it is derived from the key, here, by the same
    # function that peer used to put it on its own interface.
    info = parse_wireguard_service(await resolver()(target))
    if info.endpoint is None:
        # A WireGuard service may leave the address out. The reader is the side
        # that sends first, so without one there is nowhere to knock.
        raise SystemExit(f"{target}'s DID document names no WireGuard endpoint to send to")
    # Hand those to the kernel. This is the whole "client side" of a WireGuard
    # tunnel, four facts:
    #   peer <key>                trust this key
    #   endpoint <host>:51820     send to it here
    #   allowed-ips <addr>/128    packets from this key may only claim the one
    #                             address its key derives
    #   persistent-keepalive 5    knock every 5 s, so the tunnel comes up on its
    #                             own the moment that peer admits us
    subprocess.run(
        [
            "wg",
            "set",
            "wg0",
            "peer",
            info.public_key,
            "endpoint",
            info.endpoint,
            "allowed-ips",
            info.allowed_ips,
            "persistent-keepalive",
            "5",
        ],
        check=True,
    )
    print(
        f"tunnel peer set: {target} is {info.public_key} at {info.endpoint}, tunnel ip {info.tunnel_ip}"
    )
    # WireGuard is silent to strangers. Until the other peer adds our public key on its side,
    # our knocks get no reply at all, not even a refusal.
    print("nothing will handshake until that peer admits this machine")


asyncio.run(main())
