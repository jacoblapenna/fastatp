"""Test 2 — real WireGuard enforcement in network namespaces (root-gated).

This is the definitive proof that the reconciler's grant/revoke has kernel
teeth: two network namespaces, real ``wg`` interfaces, a real Noise handshake,
and a real ping across the tunnel that succeeds only while the peer is
present on the interface. It requires root (CAP_NET_ADMIN), the ``wg`` and
``ip`` tools, and WireGuard kernel support, so it is skipped everywhere else.

Run it deliberately, as root, on a host with wireguard-tools:

    sudo -E env "PATH=$PATH" .venv/bin/python -m pytest tests/test_wireguard_netns.py -v

Topology (grantor = ns_a hosting wgA; grantee = ns_b initiating from wgB):

    ns_a  wgA 10.100.0.1/24   <== WireGuard over UDP ==>   wgB 10.100.0.2/24  ns_b
          vethA 192.168.99.1/24                            vethB 192.168.99.2/24

ns_b is fully pre-configured and initiates; ns_a adds/removes wgB as a peer
ONLY through the reconciler (via WgToolInterface). Ping ns_b -> 10.100.0.1
therefore succeeds iff the reconciler has granted did_b.
"""

import base64
import os
import shutil
import subprocess
import time

import pytest

from fastatp.authz import WgToolInterface, WireGuardReconciler, build_wireguard_service
from fastatp.crypto import generate_keypair
from fastatp.did.document import build_did_document

try:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    _HAVE_X25519 = True
except ImportError:  # pragma: no cover
    _HAVE_X25519 = False

_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

# name exactly what is missing so a skip is self-diagnosing
_MISSING = []
if not _IS_ROOT:
    _MISSING.append("root (run under sudo)")
if shutil.which("wg") is None:
    _MISSING.append("wg — install wireguard-tools (apt install wireguard-tools)")
if shutil.which("ip") is None:
    _MISSING.append("ip — install iproute2")
if not _HAVE_X25519:
    _MISSING.append("X25519 (cryptography)")

pytestmark = pytest.mark.skipif(
    bool(_MISSING), reason="missing prerequisites -> " + "; ".join(_MISSING)
)

NS_A = f"fatp{os.getpid()}a"
NS_B = f"fatp{os.getpid()}b"
DID_A = "did:web:node-a.example"
DID_B = "did:web:node-b.example"


def _run(argv: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=check)


def _in(ns: str, argv: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return _run(["ip", "netns", "exec", ns, *argv], check=check)


def _wg_keypair() -> tuple[str, str]:
    """A WireGuard keypair (base64). wg keys are X25519 keys."""
    priv = X25519PrivateKey.generate()
    priv_b = priv.private_bytes_raw() if hasattr(priv, "private_bytes_raw") else None
    if priv_b is None:  # older cryptography
        from cryptography.hazmat.primitives.serialization import (
            Encoding as E,
        )
        from cryptography.hazmat.primitives.serialization import (
            NoEncryption,
            PrivateFormat,
        )

        priv_b = priv.private_bytes(E.Raw, PrivateFormat.Raw, NoEncryption())
    pub_b = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(priv_b).decode(), base64.b64encode(pub_b).decode()


def _write_key(tmp_path, name: str, key_b64: str) -> str:
    path = tmp_path / name
    path.write_text(key_b64)
    return str(path)


@pytest.fixture
def netns(tmp_path):
    priv_a, pub_a = _wg_keypair()
    priv_b, pub_b = _wg_keypair()
    key_a = _write_key(tmp_path, "a.key", priv_a)
    key_b = _write_key(tmp_path, "b.key", priv_b)
    created = []
    try:
        for ns in (NS_A, NS_B):
            _run(["ip", "netns", "add", ns])
            created.append(ns)
            _in(ns, ["ip", "link", "set", "lo", "up"])

        # underlay carrying WireGuard's UDP: a veth pair straddling the two ns
        _run(["ip", "link", "add", "vethA", "type", "veth", "peer", "name", "vethB"])
        _run(["ip", "link", "set", "vethA", "netns", NS_A])
        _run(["ip", "link", "set", "vethB", "netns", NS_B])
        _in(NS_A, ["ip", "addr", "add", "192.168.99.1/24", "dev", "vethA"])
        _in(NS_B, ["ip", "addr", "add", "192.168.99.2/24", "dev", "vethB"])
        _in(NS_A, ["ip", "link", "set", "vethA", "up"])
        _in(NS_B, ["ip", "link", "set", "vethB", "up"])

        # WireGuard interfaces created INSIDE each namespace so the encrypted
        # UDP socket lives there too. A wg interface created in one namespace
        # and MOVED keeps its socket in the ORIGINAL namespace (a deliberate
        # WireGuard feature for split plaintext/ciphertext namespaces) — doing
        # that here leaves the socket in the root ns, unreachable from the
        # veth, so handshakes are sent into the void. Create it in place.
        for ns, wg in ((NS_A, "wgA"), (NS_B, "wgB")):
            _in(ns, ["ip", "link", "add", wg, "type", "wireguard"])
        _in(NS_A, ["wg", "set", "wgA", "private-key", key_a, "listen-port", "51820"])
        _in(NS_B, ["wg", "set", "wgB", "private-key", key_b, "listen-port", "51821"])
        _in(NS_A, ["ip", "addr", "add", "10.100.0.1/24", "dev", "wgA"])
        _in(NS_B, ["ip", "addr", "add", "10.100.0.2/24", "dev", "wgB"])
        _in(NS_A, ["ip", "link", "set", "wgA", "up"])
        _in(NS_B, ["ip", "link", "set", "wgB", "up"])

        # loose reverse-path filtering: rp_filter can drop the decrypted inner
        # packet in a netns WireGuard setup (a common gotcha), harmless here
        for ns in (NS_A, NS_B):
            for scope in ("all", "default", "vethA", "vethB", "wgA", "wgB"):
                _in(ns, ["sysctl", "-qw", f"net.ipv4.conf.{scope}.rp_filter=2"], check=False)

        # ns_b is the initiator: it knows ns_a's pubkey + endpoint up front.
        # ns_a is the grantor: it will learn/allow ns_b ONLY via the reconciler.
        _in(
            NS_B,
            [
                "wg",
                "set",
                "wgB",
                "peer",
                pub_a,
                "endpoint",
                "192.168.99.1:51820",
                "allowed-ips",
                "10.100.0.1/32",
                "persistent-keepalive",
                "1",
            ],
        )
        yield {"pub_a": pub_a, "pub_b": pub_b}
    finally:
        for ns in created:
            _run(["ip", "netns", "del", ns], check=False)


def _ping_once(ns: str, target: str) -> bool:
    return _in(ns, ["ping", "-c", "1", "-W", "2", target], check=False).returncode == 0


def _ping_within(ns: str, target: str, timeout: float) -> bool:
    """Retry until a ping succeeds or the deadline passes.

    WireGuard is data-driven and rate-limits handshake attempts (~5s between
    retries), so a freshly-granted tunnel can take a couple of attempts to
    come up; poll rather than assume the first packet lands.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _ping_once(ns, target):
            return True
        time.sleep(1.0)
    return False


def _wg_diag() -> str:
    a = _in(NS_A, ["wg", "show", "wgA"], check=False).stdout
    b = _in(NS_B, ["wg", "show", "wgB"], check=False).stdout
    return f"\n--- wgA (grantor ns_a) ---\n{a}\n--- wgB (grantee ns_b) ---\n{b}"


async def test_reconciler_grant_revoke_gates_real_tunnel(netns):
    pub_b = netns["pub_b"]

    # underlay sanity: the veth carrying WireGuard's UDP must itself work, so a
    # tunnel failure below is unambiguously WireGuard, not the netns plumbing
    assert _ping_once(NS_B, "192.168.99.1"), "veth underlay is broken" + _wg_diag()

    # the grantee's DID document advertises its real WG pubkey + tunnel ip
    doc_b = build_did_document(
        did=DID_B,
        signing_key=generate_keypair("k256").public_key(),
        extra_services=[build_wireguard_service(pub_b, "10.100.0.2")],
    )

    async def resolver(did: str):
        return doc_b if did == DID_B else None

    # the reconciler drives the REAL wgA interface inside ns_a
    interface = WgToolInterface("wgA", run_command=lambda argv: _in(NS_A, argv).stdout)
    repo_a = generate_keypair("k256")
    from fastatp.repo.repo import Repo

    reconciler = WireGuardReconciler(Repo(DID_A, repo_a), interface, resolver)

    # 1. BEFORE grant: ns_b is not a peer on wgA -> handshake refused -> no tunnel
    assert not _ping_within(NS_B, "10.100.0.1", 3), "tunnel reachable before grant"

    # 2. GRANT: reconciler adds ns_b as a real wg peer via `wg set`
    await reconciler.grant(DID_B)
    assert pub_b in interface.peers()
    assert _ping_within(NS_B, "10.100.0.1", 20), "tunnel not reachable after grant" + _wg_diag()

    # 3. REVOKE: reconciler removes the peer live -> kernel drops the session
    await reconciler.revoke(DID_B, reason="offboarded")
    assert pub_b not in interface.peers()
    # allow a moment for any in-flight session to be torn down, then confirm dead
    time.sleep(2.0)
    assert not _ping_within(NS_B, "10.100.0.1", 3), (
        "tunnel still reachable after revoke" + _wg_diag()
    )
