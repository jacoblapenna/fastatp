"""The enforcement plane: WireGuard peer-list adapters and the DID service.

Absence is the mechanism: a static public key not present on the interface
is cryptographically unreachable — the kernel silently drops its packets,
no handshake, no session. Adding a peer opens the tunnel; removing it (live,
via ``wg set`` — never a wg-quick config rewrite) tears down the session and
routes immediately and the grantee cannot re-handshake.

Peers advertise their WireGuard identity in their DID document as a service
entry (type ``WireGuardFirehose``) carrying the static PUBLIC key, the
tunnel-internal IP, and an optional endpoint. Only public keys are ever
published; Noise_IK derives session keys independently on each end, and the
optional WireGuard PSK is deliberately unused — no secret ever crosses the
wire. The endpoint is optional and unused for enforcement: WireGuard learns
a peer's endpoint from its handshake, so grantees can roam.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import shutil
import subprocess
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519

from fastatp.did.document import DIDDocument, Service
from fastatp.exceptions import EnforcementError

WIREGUARD_SERVICE_TYPE = "WireGuardFirehose"
WIREGUARD_SERVICE_ID = "#wireguard"


def validate_wireguard_key(key: str) -> str:
    """A WireGuard static key: 32 bytes, standard base64 (44 chars)."""
    try:
        raw = base64.b64decode(key, validate=True)
    except (binascii.Error, ValueError):
        raise EnforcementError(f"not a valid WireGuard key: {key!r}") from None
    if len(raw) != 32:
        raise EnforcementError(f"WireGuard keys are 32 bytes, got {len(raw)}")
    return key


@dataclass(frozen=True)
class WireGuardPeerInfo:
    """A peer's published WireGuard identity, parsed from its DID document."""

    public_key: str
    tunnel_ip: str
    endpoint: str | None = None

    @property
    def allowed_ips(self) -> str:
        address = ipaddress.ip_address(self.tunnel_ip)
        return f"{self.tunnel_ip}/{128 if address.version == 6 else 32}"


def generate_wireguard_keypair() -> tuple[str, str]:
    """A fresh WireGuard (X25519) keypair as ``(private_b64, public_b64)``.

    Standard base64 of the 32-byte Curve25519 keys — the exact wire format
    ``wg`` and the DID-document service entry use. Ephemeral private-response
    channels mint one per channel so the channel is reachable without
    any persistent identity. Only the public key is ever published.
    """
    private = x25519.X25519PrivateKey.generate()
    priv_raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(priv_raw).decode(), base64.b64encode(pub_raw).decode()


def build_wireguard_uri(wg_public_key: str, tunnel_ip: str, host: str | None = None) -> str:
    """A ``wg://[host]?pubkey=..&ip=..`` URI binding a peer's WG identity.

    The canonical way FastATP serializes "how to reach this WireGuard peer":
    the static PUBLIC key plus the tunnel-internal IP (the ``/32`` the doorman
    routes on), and an optional endpoint host. Used both in the DID-document
    service entry and in a channel/self attestation.
    """
    validate_wireguard_key(wg_public_key)
    ipaddress.ip_address(tunnel_ip)  # raises ValueError on garbage
    query = urllib.parse.urlencode({"pubkey": wg_public_key, "ip": tunnel_ip})
    return f"wg://{host or ''}?{query}"


def parse_wireguard_uri(uri: str, *, context: str = "endpoint") -> WireGuardPeerInfo:
    """Parse a ``wg://`` URI back to a :class:`WireGuardPeerInfo`."""
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "wg":
        raise EnforcementError(f"{context} must be a wg:// URI")
    params = urllib.parse.parse_qs(parsed.query)
    pubkey = params.get("pubkey", [""])[0]
    tunnel_ip = params.get("ip", [""])[0]
    if not pubkey or not tunnel_ip:
        raise EnforcementError(f"{context} is missing pubkey or ip")
    validate_wireguard_key(pubkey)
    try:
        ipaddress.ip_address(tunnel_ip)
    except ValueError:
        raise EnforcementError(f"{context}: invalid tunnel ip {tunnel_ip!r}") from None
    return WireGuardPeerInfo(public_key=pubkey, tunnel_ip=tunnel_ip, endpoint=parsed.netloc or None)


def build_wireguard_service(
    wg_public_key: str, tunnel_ip: str, endpoint: str | None = None
) -> Service:
    """The DID-document service entry advertising a peer's WireGuard identity.

    Pass the result in ``build_did_document(extra_services=[...])``. The key
    is the static PUBLIC key — publishing it is the point.
    """
    return Service(
        id=WIREGUARD_SERVICE_ID,
        type=WIREGUARD_SERVICE_TYPE,
        service_endpoint=build_wireguard_uri(wg_public_key, tunnel_ip, endpoint),
    )


def parse_wireguard_service(doc: DIDDocument) -> WireGuardPeerInfo:
    """Extract a peer's WireGuard identity from its resolved DID document."""
    for service in doc.service:
        if service.type != WIREGUARD_SERVICE_TYPE:
            continue
        return parse_wireguard_uri(
            service.service_endpoint, context=f"{doc.id}: {WIREGUARD_SERVICE_TYPE} endpoint"
        )
    raise EnforcementError(f"{doc.id}: document has no {WIREGUARD_SERVICE_TYPE} service")


class WireGuardInterface(Protocol):
    """The enforcement backend: a live WireGuard peer list."""

    def peers(self) -> dict[str, str]:
        """Current peers: ``{public_key: allowed_ips}``."""
        ...

    def add_peer(self, public_key: str, allowed_ips: str) -> None: ...

    def remove_peer(self, public_key: str) -> None: ...


class MemoryWireGuardInterface:
    """In-memory peer list for tests, dry runs, and change planning."""

    def __init__(self) -> None:
        self._peers: dict[str, str] = {}

    def peers(self) -> dict[str, str]:
        return dict(self._peers)

    def add_peer(self, public_key: str, allowed_ips: str) -> None:
        self._peers[validate_wireguard_key(public_key)] = allowed_ips

    def remove_peer(self, public_key: str) -> None:
        self._peers.pop(public_key, None)


class WgToolInterface:
    """Real enforcement via the ``wg`` tool (netlink frontend).

    ``wg set <ifname> peer <pubkey> allowed-ips <ips>`` and
    ``wg set <ifname> peer <pubkey> remove`` mutate the live interface — no
    wg-quick config rewrite, no interface restart: removal makes the kernel
    drop the session and routes immediately and the peer cannot
    re-handshake. Requires root (or CAP_NET_ADMIN) on the host.

    ``run_command`` is injectable so behavior is testable without root.
    """

    def __init__(
        self,
        ifname: str,
        run_command: Callable[[list[str]], str] | None = None,
    ) -> None:
        self.ifname = ifname
        self._run = run_command or self._run_wg

    @staticmethod
    def _run_wg(argv: list[str]) -> str:
        # self-contained-correct: honors the EnforcementError contract even
        # when called directly (not only through _invoke), so the built-in
        # runner is never a footgun. _invoke re-guards injected runners.
        if shutil.which(argv[0]) is None:
            raise EnforcementError(f"{argv[0]!r} not found; install wireguard-tools")
        try:
            result = subprocess.run(argv, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or "").strip() or f"exit {e.returncode}"
            raise EnforcementError(f"{' '.join(argv)}: {detail}") from None
        return result.stdout

    def _invoke(self, argv: list[str]) -> str:
        """Run one ``wg`` command through the (possibly injected) runner and
        normalize EVERY tool failure to :class:`EnforcementError`.

        The contract is the boundary, not the built-in runner: a caller that
        injects its own ``run_command`` (the netns harness, a remote exec)
        still gets ``EnforcementError`` — never a raw ``CalledProcessError``
        or ``FileNotFoundError`` — so the enforcement plane has one error type
        with actionable context (missing tool vs privilege vs bad input).
        """
        try:
            return self._run(argv)
        except EnforcementError:
            raise  # already the right type (e.g. from _run_wg's missing-binary guard)
        except FileNotFoundError:
            raise EnforcementError(f"{argv[0]!r} not found; install wireguard-tools") from None
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or "").strip() or f"exit {e.returncode}"
            raise EnforcementError(f"{' '.join(argv)}: {detail}") from None

    def peers(self) -> dict[str, str]:
        output = self._invoke(["wg", "show", self.ifname, "allowed-ips"])
        result: dict[str, str] = {}
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                # `wg show allowed-ips` uses a TAB between key and ips and
                # SPACES between multiple CIDRs; a peer with no allowed-ips
                # prints the literal "(none)", which is an empty set to us
                ips = parts[1].strip()
                result[parts[0]] = "" if ips == "(none)" else ips.replace(" ", ",")
        return result

    def add_peer(self, public_key: str, allowed_ips: str) -> None:
        validate_wireguard_key(public_key)
        # wg set expects a comma-separated allowed-ips list; accept either
        # form on input and normalize so multi-IP peers don't churn
        normalized = ",".join(p for p in allowed_ips.replace(",", " ").split() if p)
        self._invoke(["wg", "set", self.ifname, "peer", public_key, "allowed-ips", normalized])

    def remove_peer(self, public_key: str) -> None:
        validate_wireguard_key(public_key)
        self._invoke(["wg", "set", self.ifname, "peer", public_key, "remove"])
