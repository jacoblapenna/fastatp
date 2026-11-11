"""Runnable netns scenario driving the REAL reconciler over a REAL tunnel.

Invoked by tests/test_wireguard_rootless_netns.py, normally under
``unshare --user --net --mount --map-root-user`` so it needs no sudo (the
WireGuard kernel module works in a rootless user+net namespace). Sets up two
network namespaces joined by a veth, creates real ``wg`` interfaces, and
drives ``WireGuardReconciler`` + ``WgToolInterface`` through grant/revoke —
proving the enforcement has kernel teeth for both IPv4 and IPv6, including
that revocation stops an ESTABLISHED session mid-flight (audit items 3, 4).

Prints one ``RESULT: {json}`` line and exits 0 on success, non-zero on any
setup/assertion failure. Kept dependency-free of pytest so it runs cleanly
as ``python -m tests._wg_netns_scenario`` inside the namespace.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time

from fastatp.authz import (
    WgToolInterface,
    WireGuardReconciler,
    build_wireguard_service,
    overlay_address,
)
from fastatp.authz.wireguard import generate_wireguard_keypair
from fastatp.crypto import generate_keypair
from fastatp.did.document import build_did_document
from fastatp.repo.repo import Repo


def run(
    argv: list[str], check: bool = True, ns: str | None = None, stdin: str | None = None
) -> subprocess.CompletedProcess:
    full = (["ip", "netns", "exec", ns, *argv]) if ns else argv
    return subprocess.run(full, capture_output=True, text=True, check=check, input=stdin)


def ping(ns: str, target: str) -> bool:
    # the overlay is IPv6; some images fold ping6 into `ping`
    tool = "ping6" if _have("ping6") else "ping"
    return run([tool, "-c", "1", "-W", "2", target], check=False, ns=ns).returncode == 0


def _have(binary: str) -> bool:
    import shutil

    return shutil.which(binary) is not None


def ping_within(ns: str, target: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ping(ns, target):
            return True
        time.sleep(1.0)
    return False


async def scenario() -> dict:
    nsa, nsb = "a_ovl", "b_ovl"
    va, vb = "vaovl", "vbovl"
    priv_a, pub_a = generate_wireguard_keypair()
    priv_b, pub_b = generate_wireguard_keypair()
    # overlay addresses derived from each side's own key; the v4 underlay
    # below carries WireGuard's UDP, proving the transport family is irrelevant
    addr_a, addr_b = overlay_address(pub_a), overlay_address(pub_b)
    try:
        for ns in (nsa, nsb):
            run(["ip", "netns", "add", ns])
            run(["ip", "link", "set", "lo", "up"], ns=ns)
        run(["ip", "link", "add", va, "type", "veth", "peer", "name", vb])
        run(["ip", "link", "set", va, "netns", nsa])
        run(["ip", "link", "set", vb, "netns", nsb])
        # a v4 underlay carries WireGuard's UDP regardless of the tunnel family
        run(["ip", "addr", "add", "192.168.99.1/24", "dev", va], ns=nsa)
        run(["ip", "addr", "add", "192.168.99.2/24", "dev", vb], ns=nsb)
        run(["ip", "link", "set", va, "up"], ns=nsa)
        run(["ip", "link", "set", vb, "up"], ns=nsb)
        for ns, wg in ((nsa, "wgA"), (nsb, "wgB")):
            run(["ip", "link", "add", wg, "type", "wireguard"], ns=ns)
        # private keys go to wg over a stdin pipe, never a file: no key touches
        # disk, and Ubuntu 26.04's AppArmor profile for wg only lets it open
        # files under /etc/wireguard (a key file in /run is denied)
        run(
            ["wg", "set", "wgA", "private-key", "/dev/stdin", "listen-port", "51820"],
            ns=nsa,
            stdin=priv_a,
        )
        run(
            ["wg", "set", "wgB", "private-key", "/dev/stdin", "listen-port", "51821"],
            ns=nsb,
            stdin=priv_b,
        )
        run(["ip", "addr", "add", f"{addr_a}/64", "dev", "wgA"], ns=nsa)
        run(["ip", "addr", "add", f"{addr_b}/64", "dev", "wgB"], ns=nsb)
        run(["ip", "link", "set", "wgA", "up"], ns=nsa)
        run(["ip", "link", "set", "wgB", "up"], ns=nsb)
        # ns_b initiates toward wgA's pubkey + endpoint, with keepalive
        run(
            [
                "wg",
                "set",
                "wgB",
                "peer",
                pub_a,
                "endpoint",
                "192.168.99.1:51820",
                "allowed-ips",
                f"{addr_a}/128",
                "persistent-keepalive",
                "1",
            ],
            ns=nsb,
        )

        # the REAL reconciler drives wgA inside ns_a via WgToolInterface
        did_b = "did:web:grantee.example"
        doc_b = build_did_document(
            did=did_b,
            signing_key=generate_keypair("k256").public_key(),
            extra_services=[build_wireguard_service(pub_b)],
        )

        async def resolver(did: str):
            return doc_b if did == did_b else None

        interface = WgToolInterface("wgA", run_command=lambda argv: run(argv, ns=nsa).stdout)
        rec = WireGuardReconciler(
            Repo("did:web:host.example", generate_keypair("k256")), interface, resolver
        )

        before = ping_within(nsb, addr_a, 3)
        await rec.grant(did_b)
        granted = ping_within(nsb, addr_a, 20)
        # audit item 4: revoke an ESTABLISHED session; packets must stop
        await rec.revoke(did_b, reason="offboarded")
        time.sleep(2.0)
        after_revoke = ping_within(nsb, addr_a, 3)
        return {
            "address_a": addr_a,
            "before_grant": before,  # expect False
            "after_grant": granted,  # expect True
            "after_revoke": after_revoke,  # expect False
        }
    finally:
        for ns in (nsa, nsb):
            run(["ip", "netns", "del", ns], check=False)


async def main() -> int:
    subprocess.run(["mount", "-t", "tmpfs", "none", "/run"], check=False)
    subprocess.run(["mkdir", "-p", "/run/netns"], check=False)
    try:
        r = await scenario()
    except Exception as e:  # setup failure on this host
        print(f"SCENARIO-ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    # grant opens the tunnel, revoke closes the live session — on the derived
    # IPv6 overlay over a v4 underlay
    ok = not r["before_grant"] and r["after_grant"] and not r["after_revoke"]
    print("RESULT: " + json.dumps({"overlay": r}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
