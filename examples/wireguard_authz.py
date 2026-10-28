"""v0.4 authorization end to end, runnable without root or WireGuard.

The MemoryWireGuardInterface stands in for the kernel; swap in
WgToolInterface("wg-fastatp") on a real host (requires wireguard-tools and
CAP_NET_ADMIN, and a dedicated interface the reconciler owns).

Run: python examples/wireguard_authz.py
"""

import asyncio
import base64
from datetime import datetime, timedelta, timezone

from fastatp import Peer, PeerServer, build_did_document
from fastatp.authz import (
    MemoryWireGuardInterface,
    WireGuardReconciler,
    build_wireguard_service,
)
from fastatp.crypto import generate_keypair

GRANTOR = "did:web:cloud.example"
DEVICE = "did:web:edge-device.example"
DEVICE_WG_KEY = base64.b64encode(bytes(range(32))).decode()  # demo static pubkey

docs = {
    DEVICE: build_did_document(
        did=DEVICE,
        signing_key=generate_keypair("k256").public_key(),
        extra_services=[build_wireguard_service(DEVICE_WG_KEY, tunnel_ip="10.100.0.7")],
    )
}


async def resolver(did: str):
    return docs.get(did)


async def main() -> None:
    server = PeerServer(Peer(did=GRANTOR, signing_key=generate_keypair("k256")))
    interface = MemoryWireGuardInterface()
    guard = WireGuardReconciler(server, interface, resolver)

    # grant: signed capability (firehosed) + WG peer added
    expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    rkey, cap = await guard.grant(DEVICE, expires_at=expiry, note="field gateway")
    print(f"granted {cap.subject}: capability {rkey}, tunnel {interface.peers()}")

    # drift: the peer entry vanishes (host reboot, manual wg fiddling)
    interface.remove_peer(DEVICE_WG_KEY)
    report = await guard.reconcile()
    print(f"reconcile healed drift: re-added {report.added}")

    # revoke: signed revocation + kernel drops the session
    revocation = await guard.revoke(DEVICE, reason="decommissioned")
    print(f"revoked {revocation.subject} ({revocation.reason}); peers now {interface.peers()}")
    print("audit trail record:", server.repo.list_records(revocation.nsid)[0][0])


asyncio.run(main())
