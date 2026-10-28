"""Serve the bundled browser peer (wasm + JS glue) from any Starlette app.

The fastatp wheel ships the compiled ``fastatp-wasm`` bundle as package data:
``pip install fastatp`` gives an app builder everything needed
to serve the browser peer matching this package version. The artifact
directory (``fastatp/browser/static/``) is VCS-ignored and populated at
release time by ``scripts/build-wasm.sh``; it travels inside both the wheel
and the sdist, so a plain ``pip install`` never needs a Rust toolchain.

Version lockstep is enforced at mount time: a bundle stamped with a different
version than the installed package refuses to serve — a drifted peer must
never reach a browser silently.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

STATIC_DIR = Path(__file__).resolve().parent / "static"

_MEDIA_TYPES = {
    ".wasm": "application/wasm",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".json": "application/json",
    ".map": "application/json",
    ".ts": "text/plain",  # wasm-pack emits .d.ts type stubs
}
_CACHE_CONTROL = "max-age=3600"


def bundled_version(directory: Path | None = None) -> str | None:
    """The version stamped into the built bundle, or ``None`` when not built."""
    static = Path(directory) if directory is not None else STATIC_DIR
    try:
        return (static / "VERSION").read_text().strip() or None
    except OSError:
        return None


def _installed_version() -> str | None:
    try:
        return metadata.version("fastatp")
    except metadata.PackageNotFoundError:  # running from a bare source tree
        return None


def browser_peer_assets(directory: Path | None = None) -> Starlette:
    """A mountable ASGI app serving the browser peer bundle.

    ::

        from fastatp.browser import browser_peer_assets
        app.mount("/fastatp", browser_peer_assets())

    Raises ``RuntimeError`` at construction when the bundle is absent (never a
    silent empty mount) or when its stamped VERSION does not match the
    installed package (version drift = the wrong peer would be served).
    ``directory`` overrides the packaged location, mainly for tests.
    """
    static = (Path(directory) if directory is not None else STATIC_DIR).resolve()
    version = bundled_version(static)
    if version is None:
        raise RuntimeError(
            f"browser assets not built at {static}; run scripts/build-wasm.sh "
            "or install a release wheel"
        )
    installed = _installed_version()
    if installed is not None and version != installed:
        raise RuntimeError(
            f"bundled browser peer is version {version} but fastatp is {installed} "
            "(version drift); rebuild the assets with scripts/build-wasm.sh"
        )

    async def manifest(request: Request) -> Response:
        files = sorted(
            str(path.relative_to(static))
            for path in static.rglob("*")
            if path.is_file() and path.name != "VERSION"
        )
        return JSONResponse(
            {"version": version, "files": files},
            headers={"Cache-Control": "no-store"},
        )

    async def serve(request: Request) -> Response:
        candidate = (static / request.path_params["path"]).resolve()
        # the bundle is served verbatim and nothing else is: any path that
        # escapes the static dir (or names a non-file) is a plain 404
        if static not in candidate.parents or not candidate.is_file():
            return JSONResponse({"error": "not_found"}, status_code=404)
        media = _MEDIA_TYPES.get(candidate.suffix, "application/octet-stream")
        return FileResponse(candidate, media_type=media, headers={"Cache-Control": _CACHE_CONTROL})

    return Starlette(routes=[Route("/", manifest), Route("/{path:path}", serve)])
