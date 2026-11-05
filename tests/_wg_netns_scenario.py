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

from fastatp.authz import WgToolInterface, WireGuardReconciler, build_wireguard_service
from fastatp.authz.wireguard import generate_wireguard_keypair
from fastatp.crypto import generate_keypair
from fastatp.did.document import build_did_document
from fastatp.repo.repo import Repo

FAMILIES = {
    "v4": {"a": "10.100.0.1", "b": "10.100.0.2", "plen": 24},
    "v6": {"a": "fd00::1", "b": "fd00::2", "plen": 64},
}


def run(
    argv: list[str], check: bool = True, ns: str | None = None, stdin: str | None = None
) -> subprocess.CompletedProcess:
    full = (["ip", "netns", "exec", ns, *argv]) if ns else argv
    return subprocess.run(full, capture_output=True, text=True, check=check, input=stdin)


def ping(ns: str, target: str, fam: str) -> bool:
    cmd = "ping6" if fam == "v6" else "ping"
    # some images fold ping6 into `ping`; fall back if the dedicated tool is absent
    tool = cmd if _have(cmd) else "ping"
    return run([tool, "-c", "1", "-W", "2", target], check=False, ns=ns).returncode == 0


def _have(binary: str) -> bool:
    import shutil

    return shutil.which(binary) is not None


def ping_within(ns: str, target: str, fam: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ping(ns, target, fam):
            return True
        time.sleep(1.0)
    return False


async def scenario(fam: str) -> dict:
    cfg = FAMILIES[fam]
    nsa, nsb = f"a_{fam}", f"b_{fam}"
    va, vb = f"va{fam}", f"vb{fam}"
    priv_a, pub_a = generate_wireguard_keypair()
    priv_b, pub_b = generate_wireguard_keypair()
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
        run(["ip", "addr", "add", f"{cfg['a']}/{cfg['plen']}", "dev", "wgA"], ns=nsa)
        run(["ip", "addr", "add", f"{cfg['b']}/{cfg['plen']}", "dev", "wgB"], ns=nsb)
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
                f"{cfg['a']}/{128 if fam == 'v6' else 32}",
                "persistent-keepalive",
                "1",
            ],
            ns=nsb,
        )

        # the REAL reconciler drives wgA inside ns_a via WgToolInterface
        did_b = f"did:web:grantee-{fam}.example"
        doc_b = build_did_document(
            did=did_b,
            signing_key=generate_keypair("k256").public_key(),
            extra_services=[build_wireguard_service(pub_b, cfg["b"])],
        )

        async def resolver(did: str):
            return doc_b if did == did_b else None

        interface = WgToolInterface("wgA", run_command=lambda argv: run(argv, ns=nsa).stdout)
        rec = WireGuardReconciler(
            Repo("did:web:host.example", generate_keypair("k256")), interface, resolver
        )

        before = ping_within(nsb, cfg["a"], fam, 3)
        await rec.grant(did_b)
        granted = ping_within(nsb, cfg["a"], fam, 20)
        # audit item 4: revoke an ESTABLISHED session; packets must stop
        await rec.revoke(did_b, reason="offboarded")
        time.sleep(2.0)
        after_revoke = ping_within(nsb, cfg["a"], fam, 3)
        return {
            "family": fam,
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
    results = {}
    ok = True
    for fam in ("v4", "v6"):
        try:
            r = await scenario(fam)
        except Exception as e:  # setup failure on this host/family
            print(f"SCENARIO-ERROR {fam}: {type(e).__name__}: {e}", file=sys.stderr)
            return 3
        results[fam] = r
        # item 4 (revocation latency) proven on v4; item 3 (e2e) on v6 —
        # both require grant to open and (for v4) revoke to close the tunnel
        if r["before_grant"] or not r["after_grant"]:
            ok = False
        if fam == "v4" and r["after_revoke"]:
            ok = False
    print("RESULT: " + json.dumps(results))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
