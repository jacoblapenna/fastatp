"""did:web resolution and resolver composition.

``DIDResolver`` is the whole storage contract: given a DID, return a
``DIDDocument`` (or None / raise ``DIDNotFound``). Callers plug in a DB
lookup, an HTTPS fetch, or a chain of both — FastATP never manages storage.
"""

from __future__ import annotations

import asyncio
import re
import time
import urllib.parse
from collections import OrderedDict
from collections.abc import Awaitable, Callable

import httpx

from fastatp.did.document import DIDDocument
from fastatp.exceptions import (
    DIDDocumentInvalid,
    DIDNotFound,
    DIDResolutionTimeout,
)

DIDResolver = Callable[[str], Awaitable[DIDDocument | None]]

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
_MAX_AGE_RE = re.compile(r"(?<![a-zA-Z-])max-age=(\d+)")  # must not match s-maxage


def did_web_url(did: str) -> str:
    """Map a did:web to its document URL per the W3C-CCG did:web spec.

    ``did:web:host`` -> ``https://host/.well-known/did.json``;
    ``did:web:host:a:b`` -> ``https://host/a/b/did.json``. Percent-encoding in
    the method-specific id is decoded (e.g. ``%3A`` for a port). Localhost
    hosts resolve over plain http so local development works.

    # FASTATP-CONFLICT: atproto supports hostname-level did:web ONLY (did
    # spec: path-based did:web are explicitly unsupported). FastATP resolves
    # a deliberate SUPERSET: path-form DIDs (``did:web:host:<slug>``) are the
    # continuity twins — self-certifying internal identities, never
    # announced to a relay. Segmentation rule: anything
    # atproto-visible gets its own hostname; slug twins stay FastATP-internal.
    """
    if not did.startswith("did:web:"):
        raise DIDNotFound(f"not a did:web: {did!r}")
    segments = [urllib.parse.unquote(seg) for seg in did[len("did:web:") :].split(":")]
    host = segments[0]
    if not host:
        raise DIDNotFound(f"did:web has empty host: {did!r}")
    if host.startswith("["):  # IPv6 literal, e.g. [::1] or [::1]:8080
        bare_host = host[: host.index("]") + 1] if "]" in host else host
    else:
        bare_host = host.split(":")[0]
    scheme = "http" if bare_host in _LOCAL_HOSTS else "https"
    if len(segments) == 1:
        return f"{scheme}://{host}/.well-known/did.json"
    path = "/".join(segments[1:])
    return f"{scheme}://{host}/{path}/did.json"


class WebResolver:
    """Default ``DIDResolver`` for did:web over HTTPS.

    Respects ``Cache-Control: max-age`` via a small in-memory LRU and
    coalesces concurrent resolutions of the same DID.

    Resolving a DID means an outbound HTTP request to a host the DID's
    *author* chose. On a private network that is the product working as
    designed; a verifier reachable from untrusted peers should pass
    ``host_policy`` (host -> bool) to restrict which hosts it will fetch
    from (e.g. deny RFC-1918/link-local ranges, or allowlist a domain).
    """

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        cache_size: int = 1024,
        client: httpx.AsyncClient | None = None,
        host_policy: Callable[[str], bool] | None = None,
        max_cache_age: float = 3600.0,
    ) -> None:
        self._timeout = timeout
        self._cache_size = cache_size
        self._host_policy = host_policy
        self._max_cache_age = max_cache_age
        self._cache: OrderedDict[str, tuple[float, DIDDocument]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[DIDDocument]] = {}
        self._client = client

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(follow_redirects=False)
        return self._client

    def invalidate(self, did: str) -> None:
        """Drop a cached document so the next resolve refetches.

        The explicit-invalidation seam a firehose consumer needs: an
        ``#identity`` event is precisely "your cache is stale" — waiting out
        the TTL would verify against a rotated-away key in the meantime.
        (Invalidation-API pattern from MarshalX/atproto's identity cache.)
        """
        self._cache.pop(did, None)

    async def __call__(self, did: str) -> DIDDocument:
        now = time.monotonic()
        cached = self._cache.get(did)
        if cached is not None:
            expires_at, doc = cached
            if now < expires_at:
                self._cache.move_to_end(did)
                return doc
            del self._cache[did]

        # coalesce concurrent lookups of the same DID onto one fetch
        task = self._inflight.get(did)
        if task is None:
            task = asyncio.create_task(self._fetch(did))
            self._inflight[did] = task
            task.add_done_callback(lambda _t: self._inflight.pop(did, None))
        return await asyncio.shield(task)

    async def _fetch(self, did: str) -> DIDDocument:
        url = did_web_url(did)
        if self._host_policy is not None:
            host = httpx.URL(url).host
            if not self._host_policy(host):
                raise DIDNotFound(f"{did}: host {host!r} refused by resolver host_policy")
        try:
            response = await self._get_client().get(url, timeout=self._timeout)
        except httpx.TimeoutException as e:
            raise DIDResolutionTimeout(f"{did}: {e}") from None
        except httpx.HTTPError as e:
            raise DIDNotFound(f"{did}: {e}") from None
        if response.status_code == 404:
            raise DIDNotFound(f"{did}: 404 from {url}")
        if response.status_code != 200:
            raise DIDNotFound(f"{did}: HTTP {response.status_code} from {url}")
        try:
            doc = DIDDocument.from_json_dict(response.json())
        except ValueError as e:
            raise DIDDocumentInvalid(f"{did}: response is not JSON: {e}") from None
        if doc.id != did:
            raise DIDDocumentInvalid(f"{did}: document id mismatch ({doc.id!r})")
        self._store(did, doc, response.headers.get("cache-control", ""))
        return doc

    def _store(self, did: str, doc: DIDDocument, cache_control: str) -> None:
        m = _MAX_AGE_RE.search(cache_control)
        max_age = int(m.group(1)) if m else 0
        if max_age <= 0 or "no-store" in cache_control or "no-cache" in cache_control:
            return
        # cap the author-controlled max-age so key rotation propagates in
        # bounded time even if a document server pins an enormous value
        capped_age = min(float(max_age), self._max_cache_age)
        self._cache[did] = (time.monotonic() + capped_age, doc)
        self._cache.move_to_end(did)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


web_resolver: WebResolver = WebResolver()
"""Module-level default resolver instance (shared cache)."""


def chain(*resolvers: DIDResolver) -> DIDResolver:
    """Compose resolvers: first non-None document wins.

    Members may signal a miss by returning None or raising ``DIDNotFound``;
    the chain raises ``DIDNotFound`` only if every member misses.
    """

    async def _chained(did: str) -> DIDDocument:
        for resolver in resolvers:
            try:
                doc = await resolver(did)
            except DIDNotFound:
                continue
            if doc is not None:
                return doc
        raise DIDNotFound(f"no resolver produced a document for {did}")

    return _chained


ChainFetch = Callable[[str], Awaitable["dict | None"]]
"""``fetch(chain_url) -> chain-log JSON | None``. Injectable for tests; the
default fetches ``https://.../{slug}/chain.json`` over HTTP."""


class ContinuityResolver:
    """Self-certifying did:web resolution.

    Wraps a base resolver. When the resolved document advertises a continuity
    **twin** (``alsoKnownAs`` entry ``<did>:<slug>``), the resolver fetches and
    **verifies** the twin's append-only chain before trusting the served
    document, and **fails closed** on any inconsistency:

    - genesis hash != slug, or a page not rotation-signed (``chain.verify()``);
    - the chain's genesis does not bind back to this DID;
    - the served signing keys do not match the chain head (a domain thief who
      swapped keys but cannot extend the chain).

    Tiers (edge policy): a **vanilla** did:web resolver ignores the alias
    entirely (graceful degradation — never punished); this resolver **verifies
    if present** and, if the alias is **absent**, returns the plain document
    (adoption is opt-in); ``strict=True`` **requires** a continuity twin for
    every resolved DID (refuse strangers without one).
    """

    def __init__(
        self,
        base: DIDResolver | None = None,
        *,
        strict: bool = False,
        chain_fetch: ChainFetch | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base = base or web_resolver
        self.strict = strict
        self._chain_fetch = chain_fetch
        self._timeout = timeout

    async def __call__(self, did: str) -> DIDDocument | None:
        doc = await self._base(did)
        if doc is None:
            return None
        # a twin alias is an alsoKnownAs entry that is a path segment UNDER this
        # DID (did:web:host -> did:web:host:<slug>); this also guards the cycle
        # (the twin's own doc points aKa -> pretty, which is NOT under the twin)
        twin = next((a for a in doc.also_known_as if a.startswith(did + ":")), None)
        if twin is None:
            if self.strict:
                raise DIDDocumentInvalid(
                    f"{did}: strict continuity required but the document advertises no twin"
                )
            return doc  # absent -> plain did:web (adoption is never punished)

        from fastatp.did.continuity import ContinuityChain

        chain_json = await self._fetch_chain(twin)
        if chain_json is None:
            raise DIDDocumentInvalid(
                f"{did}: advertises twin {twin!r} but its chain log is unreachable (fail closed)"
            )
        chain = ContinuityChain.from_json(chain_json)
        chain.verify()  # slug commitment + rotation-signed pages; raises on tamper/fork
        if chain.pretty_did != did:
            raise DIDDocumentInvalid(
                f"{did}: the twin's genesis binds to {chain.pretty_did!r}, not back to this DID"
            )
        if chain.twin_did != twin:
            raise DIDDocumentInvalid(
                f"{did}: served twin {twin!r} != chain twin {chain.twin_did!r}"
            )
        served = doc.get_verification_method().public_key_multibase
        head = chain.head.get_verification_method().public_key_multibase
        if served != head:
            raise DIDDocumentInvalid(
                f"{did}: served signing key does not match the continuity chain head "
                "(swapped keys without a valid chain extension — fail closed)"
            )
        return doc

    async def _fetch_chain(self, twin: str) -> dict | None:
        if self._chain_fetch is not None:
            return await self._chain_fetch(twin)
        url = did_web_url(twin).replace("/did.json", "/chain.json")
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(url)
            except httpx.HTTPError as e:
                raise DIDResolutionTimeout(f"fetching chain {url}: {e}") from None
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise DIDDocumentInvalid(f"chain fetch {url} returned {response.status_code}")
        return response.json()


async def did_key_resolver(did: str) -> DIDDocument:
    """Resolve a did:key by deriving its document from the key itself.

    did:key documents are self-certifying: the single verification method is
    the multikey embedded in the DID, with the multikey as its fragment.
    """
    from fastatp.crypto import decode_multikey
    from fastatp.did.document import VerificationMethod

    if not did.startswith("did:key:"):
        raise DIDNotFound(f"not a did:key: {did!r}")
    multikey = did[len("did:key:") :]
    try:
        decode_multikey(multikey)  # validates curve + point
    except Exception as e:
        raise DIDNotFound(f"invalid did:key: {e}") from None
    return DIDDocument(
        id=did,
        verification_method=[
            VerificationMethod(
                id=f"{did}#{multikey}", controller=did, public_key_multibase=multikey
            )
        ],
    )
