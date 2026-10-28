"""WireGuard hardening audit (no privileges).

The audit items that close at unit level, plus test cases adapted from the
MIT-licensed WireGuard packages. Cases copied from MIT sources are attributed in
the test docstring per the license wall; the netns-backed items (IPv6 e2e,
revocation latency) live in tests/test_wireguard_rootless_netns.py where a real
tunnel can prove them.
"""

import subprocess

import pytest

from fastatp.authz.wireguard import (
    WgToolInterface,
    WireGuardPeerInfo,
    build_wireguard_service,
    build_wireguard_uri,
    generate_wireguard_keypair,
    parse_wireguard_service,
    parse_wireguard_uri,
    validate_wireguard_key,
)
from fastatp.crypto import generate_keypair
from fastatp.did.document import build_did_document
from fastatp.exceptions import EnforcementError

# --- audit item 1: wg show allowed-ips parsing (netns-probed grammar) -----------
#
# Real `wg show <if> allowed-ips` output, captured in a rootless netns probe:
# a TAB separates pubkey from ips, SPACES separate multiple CIDRs, and a peer
# with no allowed-ips prints the literal "(none)".

_PK1 = "cglp3p/AzuQmga2aJnr//moPFYHmkBygENhfXSUQMkM="
_PK2 = "Sw5ZYnZ89nKzCW+op6R7v2tIG/2MggITTV81MFI1xCo="


def test_peers_parses_multi_cidr_and_none_rows():
    show = f"{_PK1}\t10.0.0.1/32 10.0.0.2/32 fd00::1/128\n{_PK2}\t(none)\n"
    peers = WgToolInterface("wg0", run_command=lambda argv: show).peers()
    # multiple CIDRs (v4 + v6) become a comma list; nothing lost or reordered
    assert peers[_PK1] == "10.0.0.1/32,10.0.0.2/32,fd00::1/128"
    # a "(none)" peer normalizes to an empty allowed-ips, not the literal token
    assert peers[_PK2] == ""


# --- audit item 2: key validation strictness ------------------------------------
#
# Negative inputs harvested from fictive-kin/wireguard v1.0.2 (MIT) — their
# tests accept "something" as a public key (they never validate); we invert
# that into the contract that we reject it. And the padded-length edges.


@pytest.mark.parametrize(
    "bad",
    [
        "something",  # from fictive-kin/wireguard test_config.py:125 (they accept it)
        "",  # empty
        "A" * 43,  # unpadded (wg keys are 44 chars incl. one '=')
        "A" * 43 + "B",  # 44 chars, decodes to 33 bytes (no padding)
        "AAAA",  # valid base64, wrong length
    ],
)
def test_key_validation_rejects(bad):
    with pytest.raises(EnforcementError):
        validate_wireguard_key(bad)


def test_key_validation_rejects_whitespace_wrapped():
    _, pub = generate_wireguard_keypair()
    for framed in (f" {pub}", f"{pub} ", f"{pub}\n"):
        with pytest.raises(EnforcementError):
            validate_wireguard_key(framed)


def test_own_pubkey_is_format_valid_documented_nonissue():
    """A peer's own public key is format-valid; validate can't (and needn't)
    reject it. The reconciler keys peers by pubkey, so a self-grant is inert,
    not dangerous — recorded as a non-issue, pinned so the reasoning holds."""
    _, pub = generate_wireguard_keypair()
    assert validate_wireguard_key(pub) == pub


def test_wireguard_keypair_roundtrip_invariant():
    """Round-trip invariant harvested from fictive-kin/wireguard (MIT) and
    django-wireguard (MIT), both of which assert b64 key round-trips: our
    generated keys are exactly 44-char standard base64 of 32 raw bytes."""
    import base64

    priv, pub = generate_wireguard_keypair()
    for k in (priv, pub):
        assert len(k) == 44
        assert len(base64.b64decode(k, validate=True)) == 32


# --- audit item 6: error taxonomy — never a raw CalledProcessError --------------


def test_wg_failure_becomes_enforcement_error():
    """A wg command failure surfaces as EnforcementError with context, even
    when a custom run_command raises the raw subprocess error (the netns
    harness's injected runner does exactly this)."""

    def boom(argv: list[str]) -> str:
        raise subprocess.CalledProcessError(1, argv, stderr="No such device")

    iface = WgToolInterface("wg-missing", run_command=boom)
    for call in (
        iface.peers,
        lambda: iface.add_peer(generate_wireguard_keypair()[1], "10.0.0.1/32"),
        lambda: iface.remove_peer(generate_wireguard_keypair()[1]),
    ):
        with pytest.raises(EnforcementError, match="No such device"):
            call()


def test_missing_binary_becomes_enforcement_error():
    """A missing wg binary is one clear EnforcementError naming the fix —
    whether the runner raises FileNotFoundError or the built-in guard fires.
    (Missing-interface negative case pattern from django-wireguard, MIT.)"""

    def not_found(argv: list[str]) -> str:
        raise FileNotFoundError(argv[0])

    with pytest.raises(EnforcementError, match="wireguard-tools"):
        WgToolInterface("wg0", run_command=not_found).peers()
    # and the built-in runner's own missing-binary guard
    with pytest.raises(EnforcementError, match="not found"):
        WgToolInterface("wg0")._run_wg(["definitely-not-a-binary-xyzzy", "show"])


def test_enforcement_error_passes_through_unwrapped():
    """A runner that already raises EnforcementError is not double-wrapped."""

    def raises_enf(argv: list[str]) -> str:
        raise EnforcementError("custom reason")

    with pytest.raises(EnforcementError, match=r"^custom reason$"):
        WgToolInterface("wg0", run_command=raises_enf).peers()


# --- audit item 3 (unit half): IPv6 through the full URI/service/peer path -------
#
# The netns v6 tunnel proof is in test_wireguard_rootless_netns.py; here the
# data path: a v6 tunnel IP survives build->parse->service->peer_info and
# yields a /128 allowed-ips. IPv6 sample addresses harvested from
# fictive-kin/wireguard test_peers.py (MIT).


def test_ipv6_tunnel_ip_end_to_end():
    _, pub = generate_wireguard_keypair()
    v6 = "fde2:3a65:ca93:3125:1234:abcd:4321:32"
    uri = build_wireguard_uri(pub, v6)
    info = parse_wireguard_uri(uri)
    assert info.tunnel_ip == v6
    assert info.allowed_ips == f"{v6}/128"  # /128 for v6, not /32

    doc = build_did_document(
        did="did:web:v6.example",
        signing_key=generate_keypair("k256").public_key(),
        extra_services=[build_wireguard_service(pub, v6)],
    )
    assert parse_wireguard_service(doc).allowed_ips == f"{v6}/128"


def test_ipv4_allowed_ips_is_slash_32():
    """/32 for v4 (matches fictive-kin/wireguard test_peers.py, MIT)."""
    _, pub = generate_wireguard_keypair()
    assert WireGuardPeerInfo(public_key=pub, tunnel_ip="192.168.1.12").allowed_ips == (
        "192.168.1.12/32"
    )


# --- add_peer normalization: comma/space churn -----------------------------------
#
# Vector table harvested from django-wireguard (MIT) test_package.py:229-250
# (test_comma_separated_list_clean): mixed separators, whitespace, and
# trailing commas must normalize to the same canonical comma list so a
# multi-IP peer never churns add_peer.


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("10.0.0.1/32,10.0.0.2/32", "10.0.0.1/32,10.0.0.2/32"),
        ("10.0.0.1/32 10.0.0.2/32", "10.0.0.1/32,10.0.0.2/32"),
        ("10.0.0.1/32, 10.0.0.2/32", "10.0.0.1/32,10.0.0.2/32"),
        ("10.0.0.1/32,,10.0.0.2/32,", "10.0.0.1/32,10.0.0.2/32"),
        ("  10.0.0.1/32  ", "10.0.0.1/32"),
        ("10.0.0.1/32\t10.0.0.2/32", "10.0.0.1/32,10.0.0.2/32"),
    ],
)
def test_add_peer_normalizes_allowed_ips(raw, expected):
    sent = {}
    _, pub = generate_wireguard_keypair()

    def capture(argv: list[str]) -> str:
        sent["ips"] = argv[argv.index("allowed-ips") + 1]
        return ""

    WgToolInterface("wg0", run_command=capture).add_peer(pub, raw)
    assert sent["ips"] == expected


# --- cross-impl interop KAT (fictive-kin/wireguard, MIT) -------------------------


def test_x25519_known_answer_cross_impl():
    """Two X25519 private->public pairs generated by PyNaCl (fictive-kin/
    wireguard v1.0.2 test_json.py, MIT). FastATP's cryptography-based
    derivation must reproduce the public key byte-exactly — the interop
    guarantee against any libsodium/PyNaCl-based WireGuard peer."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import x25519

    pairs = [
        (
            "9ZFnCUpTWG3/rOLWXr1Yx5nHY6TawlthxoVl9WsPWJk=",
            "clrrtKlXuXnbDXN7nM00fMytLHDzaAGChERA1Pmvqns=",
        ),
        (
            "aJ7VaCMQNg0qIQ5Xa3xYJQpF9OZaWk/PQRFYtHxyWVE=",
            "ZJMdTDweEMnyoSxa88HWulr3NUtkqhldHHNG/Oup9iM=",
        ),
    ]
    for priv_b64, pub_b64 in pairs:
        priv = x25519.X25519PrivateKey.from_private_bytes(base64.b64decode(priv_b64))
        derived = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        assert base64.b64encode(derived).decode() == pub_b64


# --- audit items 5 & 7: reconciler idempotency, heal, endpoint roaming ----------


def _reconciler_fixture(interface):
    """A reconciler over an in-memory repo + a single did:web grantee whose
    document advertises a WG identity WITH a (roaming) endpoint."""
    import asyncio  # noqa: F401 - imported by callers' asyncio.run

    _wg_priv, wg_pub = generate_wireguard_keypair()
    did_b = "did:web:grantee.example"
    doc = build_did_document(
        did=did_b,
        signing_key=generate_keypair("k256").public_key(),
        extra_services=[build_wireguard_service(wg_pub, "10.100.0.2", "roamer.example:51820")],
    )

    async def resolver(did: str):
        return doc if did == did_b else None

    from fastatp.authz.reconciler import WireGuardReconciler
    from fastatp.repo.repo import Repo

    rec = WireGuardReconciler(
        Repo("did:web:host.example", generate_keypair("k256")), interface, resolver
    )
    return rec, did_b, wg_pub


def test_reconcile_is_idempotent():
    """Audit item 5: after a grant, reconcile is a no-op and repeats stay
    no-ops — the _ips_key comparison prevents add_peer churn."""
    import asyncio

    from fastatp.authz.wireguard import MemoryWireGuardInterface

    async def run():
        iface = MemoryWireGuardInterface()
        rec, did_b, pub = _reconciler_fixture(iface)
        await rec.grant(did_b)
        assert (await rec.reconcile()).changed is False
        assert (await rec.reconcile()).changed is False
        assert list(iface.peers()) == [pub]

    asyncio.run(run())


def test_partial_failure_heals_on_next_reconcile():
    """Audit item 5: grant commits the capability record BEFORE touching the
    interface, so a wg failure (now an EnforcementError, item 6) leaves the
    record but no peer — and the next reconcile adds it. Records first, teeth
    second, heal in the safe direction."""
    import asyncio

    from fastatp.authz.wireguard import MemoryWireGuardInterface
    from fastatp.exceptions import EnforcementError

    class FlakyOnce(MemoryWireGuardInterface):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def add_peer(self, pk, ips):
            self.calls += 1
            if self.calls == 1:
                raise EnforcementError("simulated wg set failure")
            super().add_peer(pk, ips)

    async def run():
        iface = FlakyOnce()
        rec, did_b, pub = _reconciler_fixture(iface)
        with pytest.raises(EnforcementError):
            await rec.grant(did_b)
        assert iface.peers() == {}  # teeth not applied
        report = await rec.reconcile()  # heals
        assert pub in iface.peers()
        assert report.added == [did_b]

    asyncio.run(run())


def test_endpoint_roaming_never_pinned():
    """Audit item 7: a grantee's published endpoint is informational only.
    allowed-ips comes from the tunnel IP (the stable /32 the doorman routes
    on), and the reconciler never sends the roaming external endpoint to the
    interface — WireGuard learns it from the handshake."""
    import asyncio

    from fastatp.authz.wireguard import MemoryWireGuardInterface, parse_wireguard_service

    async def run():
        iface = MemoryWireGuardInterface()
        rec, did_b, pub = _reconciler_fixture(iface)
        info = parse_wireguard_service(await rec.resolver(did_b))
        assert info.endpoint == "roamer.example:51820"  # present in the doc
        assert info.allowed_ips == "10.100.0.2/32"  # derived from tunnel_ip only
        await rec.grant(did_b)
        # the interface stores exactly the /32 allowed-ips, no endpoint anywhere
        assert iface.peers()[pub] == "10.100.0.2/32"

    asyncio.run(run())


# --- shipping study: graceful degradation with no wg binary ---------------------


def test_graceful_degradation_without_wg_binary():
    """WG enforcement is an optional adapter: with NO `wg` on PATH, the
    package still imports and the whole enforcement API is constructible;
    only the FIRST actual interface call fails, as one clear EnforcementError
    naming the missing capability. Proven in a subprocess with a PATH that
    has no wg (so this holds on any OS / a plain-pip install)."""
    import subprocess
    import sys
    import textwrap

    program = textwrap.dedent(
        """
        import fastatp                       # imports with no wg present
        from fastatp.authz import WgToolInterface
        from fastatp.authz.wireguard import generate_wireguard_keypair
        from fastatp.exceptions import EnforcementError
        iface = WgToolInterface("wg-fastatp")   # construction needs no binary
        _priv, pub = generate_wireguard_keypair()  # key ops are pure-python
        try:
            iface.peers()                    # first real use is where it fails
        except EnforcementError as e:
            assert "wireguard-tools" in str(e), str(e)
            print("OK")
        else:
            raise SystemExit("expected EnforcementError with no wg on PATH")
        """
    )
    # a PATH with no `wg` (an empty dir); keep the venv's site-packages via -S off
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as empty:
        env = dict(os.environ, PATH=empty)
        proc = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, env=env
        )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
