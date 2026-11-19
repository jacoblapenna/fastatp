"""did:web resolution on this LAN.

Production verifiers fetch https://<domain>/.well-known/did.json. This LAN has
no certificate authority, so the same WebResolver is handed an HTTP client that
speaks plain http to the same URL. That is the only stand-in in this lab.
"""

import httpx

from fastatp import WebResolver


class _PlainHTTP(httpx.AsyncHTTPTransport):
    # httpx lets you swap the layer that actually sends a request. This is the
    # normal sender with one change: whatever URL the resolver built, send it
    # over http instead of https. The resolver never knows and never cares.
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.url = request.url.copy_with(scheme="http")
        return await super().handle_async_request(request)


def resolver() -> WebResolver:
    # Every script in the lab calls this instead of WebResolver() directly, so
    # the stand-in lives in exactly one place. WebResolver() with no arguments
    # is the production behaviour.
    #
    # Why https matters there. A signature proves "signed by key K". The DID
    # document is what turns that into "signed by alice": it is where a reader
    # first learns that K is alice's key. If that one fetch goes over plain
    # http, anyone between the reader and alice (a bad router, a laptop on the
    # same Wi-Fi) can answer first with a document naming a public key whose private half
    # they hold. From
    # then on the reader verifies the impostor's records and rejects alice's,
    # and every check passes honestly. TLS makes that fetch untamperable. This
    # LAN has no one in between, which is the only reason the shortcut is safe.
    return WebResolver(client=httpx.AsyncClient(transport=_PlainHTTP()))
