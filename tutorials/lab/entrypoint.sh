#!/bin/sh
# Boot one machine: a WireGuard interface with a key of its own, then the peer.
set -e
DOMAIN="$1"
mkdir -p "/srv/$DOMAIN"
# The WireGuard private key lives next to signing.key and, like it, is made
# once and kept. Same key on every boot means the DID document stays true.
[ -f "/srv/$DOMAIN/wg.key" ] || (umask 077; wg genkey > "/srv/$DOMAIN/wg.key")
# wg0 is a network interface the kernel owns. Creating it needs NET_ADMIN,
# which is the one capability the runner grants. Skip if a restart left one behind.
ip link show wg0 >/dev/null 2>&1 || ip link add wg0 type wireguard
# This machine's address inside the tunnel is not assigned by anyone: it is
# derived from its public key, by the same function every peer uses, so a
# reader who learns the key from the DID document already knows the address.
# The overlay is IPv6 (one fixed private /64); the LAN underneath can be
# anything. check_overlay_support fails loudly if this kernel has IPv6 off.
TUNNEL_IP=$(python -c '
import sys
from fastatp.authz.wireguard import check_overlay_support, overlay_address
check_overlay_support("wg0")
print(overlay_address(sys.argv[1]))' "$(wg pubkey < "/srv/$DOMAIN/wg.key")")
ip -6 address replace "$TUNNEL_IP/64" dev wg0
wg set wg0 private-key "/srv/$DOMAIN/wg.key" listen-port 51820   # the UDP port peers knock on
ip link set wg0 up
# No peers yet. The peer process adds them as it admits DIDs (hub side) or
# as pair.py is run (reader side). Until then wg0 is a locked door.
# Part 4 (`./lab.sh conductor`): this machine's player starts peer.py and takes the demo's cues.
[ -n "$CONDUCTOR" ] && exec python player.py "$DOMAIN"
exec python peer.py "$DOMAIN"
