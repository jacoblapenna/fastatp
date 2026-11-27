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
from fractions import Fraction
from typing import Annotated, TypeVar

import uvicorn
from backstage import follow
from pydantic import BaseModel, Field, ValidationError

from fastatp import Lexicon, dagcbor, datamodel
from fastatp.cid import CID
from fastatp.lex.types import AtUri, Blob, Cid, Datetime, Did, MaxGraphemes, MaxLength

# Who is who, agreed out of band like a phone number: each party's DID, and
# where its repo is served. A real agent would learn these the way people do.
ALICE = "did:web:alice.lan"
CONCIERGE = "did:web:concierge.lan"
BANK = "did:web:bank.lan"
COMPANIES = ["did:web:hoot.lan", "did:web:jitney.lan"]  # anyone who follows ride requests


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
    3400 with exponent -2 is $34.00. Records have no floats."""

    amount: Annotated[int, Field(ge=0)]
    exponent: Annotated[int, Field(ge=-6, le=0)] = -2
    currency: Annotated[str, MaxLength(3)] = "USD"

    def value(self) -> Fraction:
        return self.amount * Fraction(10) ** self.exponent

    def __str__(self) -> str:
        return f"${self.value():.2f}"


def dollars(cents: int) -> Money:
    return Money(amount=cents)


# --- the records, in the order the ride writes them --------------------------


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
    minutes: Annotated[int, Field(ge=0)]
    created_at: Datetime


class Fare(Lexicon):
    """The company, in its own repo: what the ride cost."""

    nsid = "com.example.ride.fare"
    booking: Ref
    dropoff: Ref
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
    first: dict[str, datetime] = field(default_factory=dict)  # uri -> when this agent first saw it

    def add(self, did: str, path: str, value: dict, at: datetime | None = None) -> None:
        uri = f"at://{did}/{path}"
        self.records[uri] = (cid_of(value), value)
        self.first.setdefault(uri, at or datetime.now(UTC))

    def all(self, model: type[T], by: str | None = None) -> list[tuple[Ref, T]]:
        """Every record of one type (from one author, if given), checked by this
        agent's own model. One that does not fit is skipped, not obeyed."""
        found = []
        for uri, (cid, value) in sorted(self.records.items()):
            author, collection = uri.removeprefix("at://").split("/")[:2]
            if collection != model.nsid or (by is not None and author != by):
                continue
            try:
                found.append((Ref(uri=uri, cid=cid), model.model_validate(value)))
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


def author(ref: Ref) -> str:
    return ref.uri.removeprefix("at://").split("/")[0]


def rkey(ref: Ref) -> str:
    return ref.uri.rsplit("/", 1)[1]


def when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


# --- the loop every agent runs -------------------------------------------------

# What an agent decides: records to write, as (rkey, record). It is a plain
# function of what it has seen and the time, so it can be tested with no network.
Decide = Callable[[Seen, datetime], list[tuple[str, Lexicon]]]


async def run(me: str, server, watching: list[str], decide: Decide, tick: float = 0.5) -> None:
    """Follow every repo in `watching` and this agent's own, and after every
    record, and every tick, write whatever `decide` says is missing.

    A record already at its path is never written again, so replaying a
    repo from the start after a reconnect changes nothing: each step happens
    once, however often it is seen."""
    seen = Seen()
    changed = asyncio.Event()

    def watcher(did: str):
        async def on_record(path: str, value: dict) -> None:
            seen.add(did, path, value)
            changed.set()

        return on_record

    # kept, so the follows are not garbage-collected while they run
    follows = [asyncio.create_task(follow(did, host(did), watcher(did))) for did in {*watching, me}]
    while follows:
        with contextlib.suppress(TimeoutError):  # a tick with nothing new still counts: time passes
            await asyncio.wait_for(changed.wait(), timeout=tick)
        changed.clear()
        for key, record in decide(seen, datetime.now(UTC)):
            if server.repo.get_record(record.nsid, key) is None:
                await server.put_record(record.nsid, key, record)
                seen.add(me, f"{record.nsid}/{key}", server.repo.get_record(record.nsid, key))
                print(f"[{host(me)}] wrote {record.nsid.rsplit('.', 1)[1]} {key}", flush=True)


async def serve(peer, watching: list[str], decide: Decide, what: str, routes=()) -> None:
    """Boot this machine's peer.py, run the agent's loop beside it, and serve
    (with any routes of the agent's own ahead of peer.py's)."""
    app = await peer.boot()
    app.router.routes[0:0] = list(routes)
    loop = asyncio.create_task(run(peer.identity.did, peer.server, watching, decide))
    sockets = [peer.listen(socket.AF_INET, "0.0.0.0"), peer.listen(socket.AF_INET6, "::")]
    print(
        f"[{peer.domain}] serving on http://{peer.domain} (LAN)"
        f" and http://[{peer.tunnel_ip}] (tunnel)",
        flush=True,
    )
    print(f"[{peer.domain}] {what}", flush=True)
    await uvicorn.Server(uvicorn.Config(app, port=80, log_level="warning")).serve(sockets=sockets)
    loop.cancel()
