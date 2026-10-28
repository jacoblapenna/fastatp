"""The wheel serves the browser peer.

The mount helper serves the bundled wasm/JS with correct MIME + caching,
refuses to exist when the bundle is absent, and refuses version drift —
`pip install fastatp` must serve exactly this package version's peer.
"""

import importlib.metadata
from pathlib import Path

import httpx
import pytest

from fastatp.browser import STATIC_DIR, browser_peer_assets, bundled_version

PACKAGE_VERSION = importlib.metadata.version("fastatp")


def _dummy_bundle(directory: Path, version: str = PACKAGE_VERSION) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "fastatp_wasm_bg.wasm").write_bytes(b"\x00asm\x01\x00\x00\x00")
    (directory / "fastatp_wasm.js").write_text("export default {};\n")
    (directory / "snippets").mkdir(exist_ok=True)
    (directory / "snippets" / "glue.js").write_text("// glue\n")
    (directory / "VERSION").write_text(version + "\n")


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://assets")


async def test_serves_wasm_and_js_with_correct_mime_and_caching(tmp_path):
    _dummy_bundle(tmp_path)
    app = browser_peer_assets(tmp_path)
    async with _client(app) as client:
        wasm = await client.get("/fastatp_wasm_bg.wasm")
        assert wasm.status_code == 200
        assert wasm.headers["content-type"] == "application/wasm"
        assert wasm.headers["cache-control"] == "max-age=3600"
        assert wasm.content.startswith(b"\x00asm")

        js = await client.get("/fastatp_wasm.js")
        assert js.status_code == 200
        assert js.headers["content-type"].startswith("text/javascript")

        nested = await client.get("/snippets/glue.js")  # wasm-bindgen snippets
        assert nested.status_code == 200
        assert nested.headers["content-type"].startswith("text/javascript")


async def test_manifest_lists_the_bundle(tmp_path):
    _dummy_bundle(tmp_path)
    app = browser_peer_assets(tmp_path)
    async with _client(app) as client:
        manifest = await client.get("/")
    assert manifest.status_code == 200
    body = manifest.json()
    assert body["version"] == PACKAGE_VERSION
    assert "fastatp_wasm_bg.wasm" in body["files"]
    assert "VERSION" not in body["files"]


async def test_path_traversal_and_missing_files_are_404(tmp_path):
    _dummy_bundle(tmp_path)
    (tmp_path.parent / "outside.txt").write_text("never served")
    app = browser_peer_assets(tmp_path)
    async with _client(app) as client:
        assert (await client.get("/..%2Foutside.txt")).status_code == 404
        assert (await client.get("/nope.wasm")).status_code == 404
        assert (await client.get("/snippets")).status_code == 404  # dir, not file


def test_absent_bundle_refuses_at_construction(tmp_path):
    with pytest.raises(RuntimeError, match="not built"):
        browser_peer_assets(tmp_path / "never-built")


def test_version_drift_refuses_at_construction(tmp_path):
    _dummy_bundle(tmp_path, version="0.0.999-drifted")
    with pytest.raises(RuntimeError, match="version drift"):
        browser_peer_assets(tmp_path)


def test_bundled_version_is_none_when_not_built(tmp_path):
    assert bundled_version(tmp_path) is None


def test_bundled_version_lockstep_when_built(tmp_path):
    _dummy_bundle(tmp_path)
    assert bundled_version(tmp_path) == PACKAGE_VERSION


@pytest.mark.skipif(not (STATIC_DIR / "VERSION").exists(), reason="real wasm bundle not built")
async def test_real_bundle_mounts_and_is_in_lockstep():
    assert bundled_version() == PACKAGE_VERSION
    app = browser_peer_assets()
    async with _client(app) as client:
        manifest = await client.get("/")
    assert manifest.status_code == 200
    assert any(name.endswith(".wasm") for name in manifest.json()["files"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
