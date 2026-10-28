"""Real-tunnel proof, rootless where possible.

Runs tests/_wg_netns_scenario.py — which drives the REAL WireGuardReconciler
over REAL wg interfaces in two network namespaces — and asserts the kernel
enforcement it proves:

- revocation latency: a granted IPv4 tunnel carries packets, and
  ``reconciler.revoke`` stops an ESTABLISHED session mid-flight (not merely
  peer-list absence — the ping dies).
- IPv6 end to end: the same grant path opens a working IPv6 tunnel,
  exercising the v6 URI/service-entry/allowed-ips paths against a real
  interface.

Runs under ``unshare --user --net --mount --map-root-user`` when not already
root (the WireGuard kernel module works in a rootless user+net namespace on
Linux), so it needs no sudo here or on a GitHub ubuntu runner. Skips cleanly
when the kernel module, ``wg``/``ip``, or user-namespace support is absent —
plain-pip installs and non-Linux CI stay green (the plain-pip discipline).
"""

import json
import os
import shutil
import subprocess

import pytest

_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _can_make_wg_netns() -> tuple[bool, str]:
    """Probe whether a rootless (or root) wg-capable netns is reachable."""
    for tool in ("wg", "ip"):
        if shutil.which(tool) is None:
            return False, f"{tool} not installed"
    probe = "ip link add wgprobe0 type wireguard && ip link del wgprobe0"
    if _IS_ROOT:
        rc = subprocess.run(["bash", "-c", probe], capture_output=True).returncode
        return (rc == 0), ("wireguard kernel module unavailable" if rc else "")
    # rootless: a user+net namespace with the wireguard module
    rc = subprocess.run(
        ["unshare", "--user", "--net", "--map-root-user", "bash", "-c", probe],
        capture_output=True,
    ).returncode
    return (rc == 0), ("no rootless netns / wireguard module" if rc else "")


_OK, _WHY = _can_make_wg_netns()
pytestmark = pytest.mark.skipif(not _OK, reason=f"real-wg netns unavailable: {_WHY}")


def _run_scenario() -> dict:
    argv = [".venv/bin/python", "-m", "tests._wg_netns_scenario"]
    if not os.path.exists(os.path.join(_REPO_ROOT, ".venv/bin/python")):
        argv[0] = "python"  # fall back to the ambient interpreter
    if not _IS_ROOT:
        argv = ["unshare", "--user", "--net", "--mount", "--map-root-user", *argv]
    proc = subprocess.run(argv, cwd=_REPO_ROOT, capture_output=True, text=True, timeout=180)
    line = next(
        (ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT: ")),
        None,
    )
    # pytest.fail (not assert) so the check holds even under `python -O`, which
    # strips asserts — this helper drives real subprocess orchestration
    if line is None:
        pytest.fail(f"scenario produced no RESULT (rc={proc.returncode}):\n{proc.stderr}")
    if proc.returncode != 0:
        pytest.fail(f"scenario failed (rc={proc.returncode}):\n{proc.stderr}\n{line}")
    return json.loads(line[len("RESULT: ") :])


def test_reconciler_gates_real_tunnel_both_families():
    results = _run_scenario()

    # item 4: IPv4 grant opens the tunnel, revoke tears down the live session
    v4 = results["v4"]
    assert v4["before_grant"] is False, "tunnel reachable before grant"
    assert v4["after_grant"] is True, "tunnel not reachable after grant"
    assert v4["after_revoke"] is False, "session survived revoke (no mid-session teardown)"

    # item 3: IPv6 grant opens a working v6 tunnel end to end
    v6 = results["v6"]
    assert v6["before_grant"] is False
    assert v6["after_grant"] is True, "IPv6 tunnel not reachable after grant"
