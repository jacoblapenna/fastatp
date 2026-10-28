import asyncio
import contextlib
import socket
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# The CC-0 conformance vectors are vendored in-repo (tests/vectors/, see its
# PROVENANCE.md) so a fresh clone runs the wire-conformance suite instead of
# skipping it — the public repo's verifiability. The gitignored
# reference/ clone is the fallback for a working copy that predates the
# vendoring, but the in-repo copy is authoritative and always present.
_VENDORED = REPO_ROOT / "tests" / "vectors"
_CLONE = REPO_ROOT / "reference" / "atproto-interop-tests"
INTEROP = _VENDORED if _VENDORED.is_dir() else _CLONE


@pytest.fixture
def serve_app():
    """Serve an ASGI app on an ephemeral loopback port, no rebind TOCTOU.

    The socket is bound once and handed to uvicorn via ``serve(sockets=[...])``
    — there is no close-then-rebind window for a concurrent process to steal
    the port (parallel-CI safe). Yields the bound port::

        async with serve_app(server.app) as port:
            ...
    """
    import uvicorn

    @contextlib.asynccontextmanager
    async def _serve(app):
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        uv = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        task = asyncio.create_task(uv.serve(sockets=[sock]))
        try:
            while not uv.started:
                await asyncio.sleep(0.01)
            yield port
        finally:
            uv.should_exit = True
            await task

    return _serve


def read_vector_lines(name: str) -> list[str]:
    """Read an interop syntax vector file: skip comments and empty lines.

    Mirrors the reference readInteropFile(): lines with leading/trailing
    whitespace are significant test cases and must be kept verbatim.
    """
    text = (INTEROP / "syntax" / name).read_text(encoding="utf-8")
    return [line for line in text.split("\n") if line and not line.startswith("#")]


@pytest.fixture(scope="session")
def interop_dir() -> Path:
    # vendored in-repo: no skip guard — the wire-conformance suite MUST run on
    # a fresh clone. Corpus-scale fixtures that need a full reference
    # clone keep their own skip guards; the vector suite may not.
    assert INTEROP.is_dir(), f"conformance vectors missing at {INTEROP}"
    return INTEROP
