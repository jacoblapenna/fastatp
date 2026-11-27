"""The ride's record types, and the one loop every agent in it runs.

Every step of the ride is a signed record in the repo of the party that took
it, pointing at the exact records it acted on. Nothing is sent to anyone:
each party follows the repos it cares about and writes what is missing.

The namespaces are ours (com.example.*); FastATP ships none of these.
"""

import asyncio
import contextlib
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Literal, TypeVar

import uvicorn
from backstage import follow
from dids import resolver
from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request as HttpRequest  # not the ride's Request record
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastatp import Lexicon, Repo, RepoMirror, dagcbor, datamodel
from fastatp.authz.capability import Capability, live_capabilities
from fastatp.authz.wireguard import parse_wireguard_service
from fastatp.cid import CID
from fastatp.crypto import encode_did_key, generate_keypair
from fastatp.lex.types import AtUri, Blob, Cid, Datetime, Did, MaxGraphemes, MaxLength
from fastatp.repo.blobs import MemoryBlobStore
from fastatp.tid import generate_tid

# Who is who, agreed out of band like a phone number: each party's DID, and
# where its repo is served. A real agent would learn these the way people do.
ALICE = "did:web:alice.lan"
CONCIERGE = "did:web:concierge.lan"
BANK = "did:web:bank.lan"
HOOT, JITNEY, CAB = "did:web:hoot.lan", "did:web:jitney.lan", "did:web:cab-17.lan"
COMPANIES = [HOOT, JITNEY]  # anyone who follows ride requests

# The cast file: which machines may talk at all. Each pair is an agreement
# made out of band, the way a customer signs up with a bank or a company with
# a payment network, and at boot each side points its tunnel at the other
# with guide one's pair.py. A tunnel is only a door: who may read which repo
# behind it is a record, written and deleted live by the repo's owner. Hoot
# and Jitney have no tunnel between them, so neither can so much as knock on
# the other's repos, and Jitney has none to alice.
TUNNELS = [
    (ALICE, CONCIERGE),
    (ALICE, BANK),
    (ALICE, HOOT),
    (ALICE, CAB),
    (CONCIERGE, BANK),
    (CONCIERGE, HOOT),
    (CONCIERGE, JITNEY),
    (BANK, HOOT),
    (BANK, JITNEY),
    (HOOT, CAB),
]


def partners(party: str) -> list[str]:
    """The parties this one has a tunnel to."""
    return [b if a == party else a for a, b in TUNNELS if party in (a, b)]


def host(did: str) -> str:
    return did.removeprefix("did:web:")


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# --- the shapes inside records ----------------------------------------------


class Ref(BaseModel):
    """A strong ref: which record, and exactly which version of it.

    The URI says where the record lives; the CID is the hash of its bytes. A
    ref to a record that has since changed no longer matches, so nobody can
    quietly edit a quote after it was accepted."""

    uri: AtUri
    cid: Cid


class Money(BaseModel):
    """An amount as whole numbers, the way the orchestra keeps a pitch:
    3400 with exponent -2 is $34.00. Records have no floats.

    This lab's money is US cents and nothing else, and the schema says so: a
    record in another currency or scale doesn't validate, so no rule here
    ever compares dollars with euros or cents with mills."""

    amount: Annotated[int, Field(ge=0)]
    exponent: Literal[-2] = -2
    currency: Literal["USD"] = "USD"

    def value(self) -> Fraction:
        return self.amount * Fraction(10) ** self.exponent

    def __str__(self) -> str:
        return f"${Decimal(self.amount).scaleb(self.exponent):.2f}"  # exact, on every Python


def dollars(cents: int) -> Money:
    return Money(amount=cents)


# --- the records, in the order the ride writes them --------------------------


class Channel(Lexicon):
    """A party, in its public repo: this private repo is mine.

    A private repo has a did:key of its own, which names nobody. This record,
    signed with the party's did:web key, is what ties the two: a reader learns
    whose a repo is from a signature, not from a server. It says the repo
    exists and whose it is; who may read it, and what is in it, stay private."""

    nsid = "com.example.ride.channel"
    did: Did
    created_at: Datetime


class Allowance(Lexicon):
    """alice, in alice's repo: my agent may have my bank hold money for a ride."""

    nsid = "com.example.bank.allowance"
    bank: Did
    agent: Did
    account: Annotated[str, MaxLength(32)]
    per_ride: Money  # the most the agent may have held for one ride
    created_at: Datetime


class Intent(Lexicon):
    """The concierge, in its own repo: what it understood alice to ask."""

    nsid = "com.example.ride.intent"
    said: Ref  # alice's note, exactly as alice signed it
    reader: Annotated[str, MaxGraphemes(100)]  # what read the sentence: a model, or a stand-in
    pickup: Annotated[str, MaxGraphemes(100)]
    destination: Annotated[str, MaxGraphemes(100)]
    most: Money
    account: Annotated[str, MaxLength(32)]
    created_at: Datetime


class Request(Lexicon):
    """The concierge, in its own repo: a ride wanted, for anyone who will bid.

    It names no company. Whoever follows ride requests may answer."""

    nsid = "com.example.ride.request"
    intent: Ref
    pickup: Annotated[str, MaxGraphemes(100)]
    destination: Annotated[str, MaxGraphemes(100)]
    bids_by: Datetime  # a bid seen after this is a no-bid
    created_at: Datetime


class Quote(Lexicon):
    """A ride company, in its own repo: a bid on one request."""

    nsid = "com.example.ride.quote"
    request: Ref
    price: Money
    eta_minutes: Annotated[int, Field(ge=0, le=180)]
    car: Annotated[str, MaxGraphemes(40)]
    created_at: Datetime


class Authorize(Lexicon):
    """The concierge, in its own repo, for the bank: hold this much for this payee."""

    nsid = "com.example.bank.authorize"
    allowance: Ref  # alice's say-so
    quote: Ref  # the bid it chose
    payee: Did
    amount: Money
    account: Annotated[str, MaxLength(32)]
    created_at: Datetime


class Hold(Lexicon):
    """The bank, in its own repo: the money is held for this payee."""

    nsid = "com.example.bank.hold"
    authorize: Ref
    payee: Did
    amount: Money
    created_at: Datetime


class Refusal(Lexicon):
    """The bank, in its own repo: it will not hold the money, and why."""

    nsid = "com.example.bank.refusal"
    authorize: Ref
    reason: Annotated[str, MaxGraphemes(200)]
    created_at: Datetime


class Booking(Lexicon):
    """The concierge, in its own repo, for the winner: the ride is yours."""

    nsid = "com.example.ride.booking"
    request: Ref
    quote: Ref  # the winning bid
    passed: list[Ref] = Field(
        default_factory=list
    )  # every other bid it considered: the decision is on the record
    hold: Ref
    created_at: Datetime


class Dispatch(Lexicon):
    """The winning company, in its own repo: this car takes the ride."""

    nsid = "com.example.ride.dispatch"
    booking: Ref
    hold: Ref  # the hold it checked in the bank's own repo, not on anyone's word
    car: Did
    created_at: Datetime


class Position(Lexicon):
    """The car, in its own repo: where it is, by its own clock and its own GPS."""

    nsid = "com.example.ride.position"
    dispatch: Ref
    leg: Annotated[str, MaxLength(16)]  # "to pickup" or "riding"
    minutes_left: Annotated[int, Field(ge=0)]
    lat_e6: int  # millionths of a degree: whole numbers again
    lon_e6: int
    created_at: Datetime


class Arrival(Lexicon):
    """The car, in its own repo: at the curb, with a photo of it."""

    nsid = "com.example.ride.arrival"
    dispatch: Ref
    photo: Blob
    created_at: Datetime


class Dropoff(Lexicon):
    """The car, in its own repo: the ride is over."""

    nsid = "com.example.ride.dropoff"
    dispatch: Ref
    arrival: Ref  # the pickup it ends: the curb, the photo
    minutes: Annotated[int, Field(ge=0)]
    created_at: Datetime


class Fare(Lexicon):
    """The company, in its own repo: what the ride cost."""

    nsid = "com.example.ride.fare"
    booking: Ref
    dropoff: Ref
    hold: Ref  # the bank's hold it is paid from
    amount: Money
    created_at: Datetime


class Settlement(Lexicon):
    """The bank, in its own repo: the fare paid from the hold, the rest let go."""

    nsid = "com.example.bank.settlement"
    hold: Ref
    fare: Ref
    paid: Money
    released: Money
    created_at: Datetime


class PayeeLimit(Lexicon):
    """The bank, in its own repo: the most it will pay this payee at once, whoever asks."""

    nsid = "com.example.bank.payee"
    payee: Did
    per_payment: Money
    created_at: Datetime


# --- what an agent has seen ---------------------------------------------------


T = TypeVar("T", bound=Lexicon)


def cid_of(value: dict) -> str:
    """A record's CID, from its bytes: what a strong ref pins."""
    return CID.for_dag_cbor(dagcbor.encode(datamodel.from_json(value))).encode()


@dataclass
class Seen:
    """Every verified record an agent has read, from every repo it follows."""

    records: dict[str, tuple[str, dict]] = field(default_factory=dict)  # uri -> (cid, value)
    # when this agent first saw each version of a record, by uri and cid: a
    # record changed later is a new version, first seen later
    first: dict[tuple[str, str], datetime] = field(default_factory=dict)
    owners: dict[str, str] = field(
        default_factory=dict
    )  # a channel's did:key -> its party's did:web

    def who(self, ref: Ref) -> str:
        """The party that signed a record: its channel's owner, as that owner announced."""
        did = ref.uri.removeprefix("at://").split("/")[0]
        return self.owners.get(did, did)

    def add(self, did: str, path: str, value: dict, at: datetime | None = None) -> None:
        uri = f"at://{did}/{path}"
        self.records[uri] = (cid_of(value), value)
        self.first.setdefault((uri, self.records[uri][0]), at or datetime.now(UTC))

    def since(self, ref: Ref) -> datetime:
        """When this agent first saw exactly this version of a record."""
        return self.first[(ref.uri, ref.cid)]

    def all(self, model: type[T], by: str | None = None) -> list[tuple[Ref, T]]:
        """Every record of one type (from one author, if given), checked by this
        agent's own model. One that does not fit is skipped, not obeyed."""
        found = []
        for uri, (cid, value) in sorted(self.records.items()):
            ref = Ref(uri=uri, cid=cid)
            if uri.split("/")[3] != model.nsid or (by is not None and self.who(ref) != by):
                continue
            try:
                found.append((ref, model.model_validate(value)))
            except ValidationError:
                continue
        return found

    def get(self, ref: Ref, model: type[T]) -> T | None:
        """The record a ref points at, only if it is still exactly that record."""
        cid, value = self.records.get(ref.uri, ("", {}))
        if cid != ref.cid:
            return None  # never seen, or changed since the ref was made
        try:
            return model.model_validate(value)
        except ValidationError:
            return None


def rkey(ref: Ref) -> str:
    return ref.uri.rsplit("/", 1)[1]


def when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


# --- the loop every agent runs -------------------------------------------------


@dataclass
class Plan:
    """What an agent decides, each time it looks: which of its repos each new
    record goes in, and who may read each of its repos right now.

    Privacy is the agent's to compose. One repo per kind of reader, one per
    ride, a reader let in for a while and let go: each is a line here, and
    the loop makes the records say so. A reader not named is let go."""

    writes: list[tuple[str, str, Lexicon]] = field(default_factory=list)  # (repo, rkey, record)
    readers: dict[str, set[str]] = field(default_factory=dict)  # repo -> who may read it

    def let(self, repo: str, *dids: str) -> None:
        self.readers.setdefault(repo, set()).update(dids)

    def write(self, repo: str, key: str, record: Lexicon) -> None:
        self.writes.append((repo, key, record))


# An agent's rules: a plain function of what it has seen and the time, so it
# can be tested with no network.
Decide = Callable[[Seen, datetime], Plan]


class Desk:
    """An agent's own private repos, by name: "main" is peer.py's channel, and
    any other is made the first time a plan names it.

    Each repo is announced, signed, in the public repo, and its readers are
    whatever the plan says: a capability record for each, written or deleted
    here. The tunnel to each reader was opened at boot; the record is what
    lets one through to this repo. Absence is revocation."""

    def __init__(self, peer, seen: Seen) -> None:
        self.peer, self.seen = peer, seen
        self.me = peer.identity.did
        self.repos = {"main": peer.channel}
        self.opening = asyncio.Lock()

    async def announce(self, did: str) -> None:
        await self.peer.server.put_record(
            Channel.nsid, generate_tid(), Channel(did=did, created_at=now())
        )
        self.seen.owners[did] = self.me

    async def repo(self, name: str):
        async with self.opening:  # one repo per name, even when two callers ask at once
            return await self._repo(name)

    async def _repo(self, name: str):
        if name not in self.repos:
            key = generate_keypair("k256")
            made = await Repo.create(encode_did_key(key.public_key()), key, blobs=MemoryBlobStore())
            self.repos[name] = await self.peer.server.register(made, public=False)
            await self.announce(made.did)
            print(f"[{host(self.me)}] opened a private repo: {name}", flush=True)
        return self.repos[name]

    async def readers(self, name: str, wanted: set[str]) -> None:
        handle = await self.repo(name)
        live = live_capabilities(handle.repo)  # subject -> (rkey, capability)
        for did in sorted(wanted - set(live)):
            try:
                tunnel_key = parse_wireguard_service(await resolver()(did)).public_key
            except Exception:
                continue  # its machine is not up yet: next time
            cap = Capability(subject=did, actions=["subscribe"], wg_public_key=tunnel_key)
            await handle.put_record(Capability.nsid, generate_tid(), cap)
            print(f"[{host(self.me)}] let {host(did)} read {name}", flush=True)
        for did in sorted(set(live) - wanted):
            await handle.delete_record(Capability.nsid, live[did][0])
            print(f"[{host(self.me)}] let {host(did)} go from {name}", flush=True)


DESK: Desk | None = None  # this process's agent's repos, for routes outside the loop


async def run(peer, decide: Decide, tick: float = 0.5) -> None:
    """Read every repo the parties this one has a tunnel to let it read, and
    after every record, and every tick, make this agent's repos what `decide`
    says: their readers, then any record not yet written.

    A record already at its path is never written again, so replaying a repo
    from the start after a reconnect changes nothing: each step happens once,
    however often it is seen."""
    global DESK
    me = peer.identity.did
    seen = Seen()
    desk = DESK = Desk(peer, seen)
    await desk.announce(peer.channel.did)
    changed = asyncio.Event()
    following: set[str] = set()

    def watcher(did: str):
        async def on_record(path: str, value: dict) -> None:
            seen.add(did, path, value)
            changed.set()

        return on_record

    def announcements(owner: str):
        async def on_record(path: str, value: dict) -> None:
            if not path.startswith(f"{Channel.nsid}/"):
                return
            try:
                private = Channel.model_validate(value).did
            except ValidationError:
                return  # not an announcement this agent understands: skipped, not obeyed
            seen.owners[private] = owner  # signed by the owner, so this repo is the owner's
            if private not in following:
                following.add(private)
                # Served only inside the tunnel, at the address the owner's key
                # derives; until the owner lets this agent read it, the doorman
                # answers no, and the follow keeps asking.
                tunnel = parse_wireguard_service(await resolver()(owner)).tunnel_ip
                tasks.append(asyncio.create_task(follow(private, f"[{tunnel}]", watcher(private))))

        return on_record

    # the partners' public repos, where they announce what private repos they keep
    tasks = [asyncio.create_task(follow(p, host(p), announcements(p))) for p in partners(me)]
    while True:
        with contextlib.suppress(TimeoutError):  # a tick with nothing new still counts: time passes
            await asyncio.wait_for(changed.wait(), timeout=tick)
        changed.clear()
        plan = decide(seen, datetime.now(UTC))
        for name, wanted in plan.readers.items():
            await desk.readers(name, wanted)
        for name, key, record in plan.writes:
            if name not in plan.readers:
                continue  # every repo's readers are decided before anything goes in it
            handle = await desk.repo(name)
            if handle.repo.get_record(record.nsid, key) is None:
                await handle.put_record(record.nsid, key, record)
                seen.add(
                    handle.did, f"{record.nsid}/{key}", handle.repo.get_record(record.nsid, key)
                )
                seen.owners[handle.did] = me
                print(
                    f"[{host(me)}] wrote {record.nsid.rsplit('.', 1)[1]} {key} in {name}",
                    flush=True,
                )


async def open_tunnels(peer) -> None:
    """The out-of-band agreements, carried out at boot: this machine's tunnel
    pointed at each partner's, with guide one's pair.py, retried until the
    partner's machine is up to answer."""
    me = peer.identity.did
    waiting = partners(me)
    while waiting:
        for other in list(waiting):
            paired = await asyncio.create_subprocess_exec(
                "python",
                "pair.py",
                other,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await paired.wait() == 0:
                waiting.remove(other)
                print(f"[{host(me)}] tunnel to {host(other)}", flush=True)
        await asyncio.sleep(1)


async def own_repos(peer) -> list[dict]:
    """This machine's private repos, each read back the way any reader would:
    exported, then checked, commit signature and every hash."""
    repos = {"main": peer.channel} | (DESK.repos if DESK is not None else {})
    found = []
    for name, handle in repos.items():
        mirror = RepoMirror(handle.did, resolver=resolver())
        await mirror.load_checkpoint(handle.repo.export_car())
        records = [
            {"path": path, "cid": cid.encode(), "value": mirror.get_record(*path.split("/"))}
            for path, cid in sorted(mirror.entries.items())
            if not path.startswith(f"{Capability.nsid}/")  # who may read it, not what it says
        ]
        # who is let in: the subjects of its live capability records, by name
        readers = sorted(host(did) for did in live_capabilities(handle.repo))
        found.append({"name": name, "did": handle.did, "readers": readers, "records": records})
    return found


def mine_route(peer) -> Route:
    """/mine: this machine's own private repos, for this machine only (like
    /whoami, a lab convenience, not an atproto route). Alice's custody check
    reads alice's own records here; the demo's player shows them on screen,
    the way it shows this machine's terminal."""

    async def mine(request: HttpRequest) -> JSONResponse:
        if (denied := peer.local_only(request)) is not None:
            return denied
        return JSONResponse(await own_repos(peer))

    return Route("/mine", mine)


async def serve(peer, decide: Decide, what: str, routes=()) -> None:
    """Boot this machine's peer.py, open its tunnels, run the agent's loop
    beside it, and serve (with any routes of the agent's own ahead of peer.py's)."""
    app = await peer.boot()
    app.router.routes[0:0] = [*routes, mine_route(peer)]
    tasks = [
        asyncio.create_task(open_tunnels(peer)),
        asyncio.create_task(run(peer, decide)),
    ]
    sockets = [peer.listen(socket.AF_INET, "0.0.0.0"), peer.listen(socket.AF_INET6, "::")]
    print(
        f"[{peer.domain}] serving on http://{peer.domain} (LAN)"
        f" and http://[{peer.tunnel_ip}] (tunnel)",
        flush=True,
    )
    print(f"[{peer.domain}] {what}", flush=True)
    await uvicorn.Server(uvicorn.Config(app, port=80, log_level="warning")).serve(sockets=sockets)
    for task in tasks:
        task.cancel()
