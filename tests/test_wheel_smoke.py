"""Wheel-install smoke test.

Builds the wheel with the real wasm bundle present, installs it into a FRESH
isolated venv (never the dev environment), and proves a pip-only consumer
gets the whole browser peer: the assets mount, the version lockstep holds,
and the wasm file physically exists in site-packages.

Slow by nature (venv + dependency install); skips when the bundle is not
built or uv is unavailable. Deselect locally with: -k "not wheel_smoke".
"""

import shutil
import subprocess
import venv
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "fastatp" / "browser" / "static" / "fastatp_wasm_bg.wasm"
UV = shutil.which("uv") or ""

if not STATIC.exists():
    pytest.skip("wasm bundle not built (scripts/build-wasm.sh)", allow_module_level=True)
if not UV:
    pytest.skip("uv not available to build the wheel", allow_module_level=True)

_CHECK = """
import importlib.metadata
import pathlib

import fastatp.browser

app = fastatp.browser.browser_peer_assets()  # constructs = bundle + lockstep OK
bundled = fastatp.browser.bundled_version()
installed = importlib.metadata.version("fastatp")
assert bundled == installed, f"lockstep broken: {bundled} != {installed}"
wasm = pathlib.Path(fastatp.browser.__file__).parent / "static" / "fastatp_wasm_bg.wasm"
assert wasm.exists(), "wasm missing from site-packages"
print(f"wheel-smoke OK {installed} ({wasm.stat().st_size} bytes)")
"""


def test_wheel_installs_and_serves_the_browser_peer(tmp_path):
    dist = tmp_path / "dist"
    subprocess.run(
        [UV, "build", "--wheel", "--out-dir", str(dist)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        timeout=300,
    )
    wheels = list(dist.glob("fastatp-*.whl"))
    assert len(wheels) == 1, f"expected one wheel, found {wheels}"

    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(env_dir)
    python = env_dir / "bin" / "python"
    subprocess.run(
        [str(python), "-m", "pip", "install", "-q", str(wheels[0])],
        check=True,
        capture_output=True,
        timeout=600,
    )

    result = subprocess.run(
        [str(python), "-c", _CHECK], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert "wheel-smoke OK" in result.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-s"]))
