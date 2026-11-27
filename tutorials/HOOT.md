# Hoot: tool calling, as signed records

alice wants a ride to the airport, under $40, paid from checking. alice says
so once, to an agent, and puts the phone down. A bank, two ride companies and
a car do the rest. At the end, alice checks every step of it, back to alice's
own words, and looks at a photo of the curb the car stopped at.

This is tool calling, the thing MCP is for: an agent finding tools, calling
them, and acting on what comes back. Here nobody calls anybody. The agent
writes a record in its own repo; whoever can help writes an answer in theirs.
That makes some hard things easy: asking every ride company at once, letting
the work run as long as it takes with nothing held open, and keeping a chain
of custody where every step is signed by whoever took it.

This guide builds on the two before it: [Getting Started](GETTING_STARTED.md)
for private repos and tunnels, and [the conductor and the
orchestra](CONDUCTOR_AND_ORCHESTRA.md) for the engine that plays the show.
It reuses their words and their lab.

> 🤖 **Coding agents:** you can run this whole guide yourself, headless, and
> read what happened. [AGENTS.md](AGENTS.md) shows how; the score is `HOOT`.

## What you are building

<!-- @demo machines alice concierge bank hoot jitney cab-17 -->
<!-- @demo peer alice rider.py -->
<!-- @demo peer concierge concierge.py -->
<!-- @demo peer bank bank.py -->
<!-- @demo peer hoot company.py -->
<!-- @demo peer jitney company.py -->
<!-- @demo peer cab-17 cab.py -->
<!-- @demo start -->
<!-- @demo chapter 1 "The cast" -->
<!-- @demo blurb Six parties, and who may reach whom. alice gives an agent a say-so over alice's money, signed. -->

Six machines, six parties: alice; alice's agent, the concierge; a bank; two
ride companies, Hoot and Jitney; and Hoot's car, cab-17. alice will ask for
a ride in one sentence, to the concierge, and never speak to any of the
others.

<!-- @demo end -->

<!-- @demo start -->

> 🔑 **Key concept: the words in this guide, one line each.**
>
> - **Repo**: one signed tree of records, owned by one DID. Only its owner
>   writes to it.
> - **Record**: one typed entry in a repo, addressed as
>   `at://<did>/<collection>/<rkey>`.
> - **DID**: a permanent name for whoever signs a repo. `did:web:hoot.lan`
>   is Hoot.
> - **Follow**: read a repo whole, then every change after it, checking
>   every signature on the way.
> - **Private repo**: a repo read only through a tunnel, and only by those
>   its owner let in, with a record.
> - **Tunnel**: an encrypted link between two machines that agreed to have one.
> - **Agent**: a program that follows repos and answers in records of its
>   own. Every machine here runs one.
> - **Strong ref**: a record's address plus the hash of its exact bytes, so
>   a step names exactly what it acted on.

Every party here is a peer with repos of its own. Nothing is sent from one
to another: each follows the repos it cares about, and answers in its own.

In four chapters:
1. **The cast.** Who may reach whom, and alice's signed say-so over alice's
   money.
2. **The ask and the bids.** One sentence from alice, one request from the
   concierge, and two sealed bids nobody asked for by name.
3. **The ride.** The bank holds the money, Hoot sends a car, the car drives,
   and the bank pays.
4. **The chain.** alice walks it all back, checks every signature, and looks
   at the curb.

<!-- @demo glossary -->
The rest of the guide's words, each one line. On the conductor's screen a
dotted word shows its line when you hover it.

- **Peer**: one process with an identity of its own that hosts repos.
  There is no client and no server; every participant is a peer.
- **Commit**: one signed change to a repo. Every write is one.
- **Signature**: bytes only the holder of a private key could have made.
  Anyone with the matching public key can check them.
- **Hash**: a short fingerprint of some bytes. Change one byte and the hash
  changes.
- **Lexicon**: a record type: its name, and the rules its fields keep.
- **Concierge**: alice's agent. It acts for alice: it reads what alice asks for
  and gets it done.
- **Allowance**: alice's signed say-so: this agent may have this bank hold
  up to this much for a ride.
- **Request**: the concierge's record asking for a ride, for whoever will bid.
- **Quote**: a ride company's bid, in its own repo.
- **Hold**: money the bank puts aside for one payee.
- **Booking**: the concierge's record telling the winner the ride is theirs.
- **Stand-in**: a few rules playing the part a model would play in production.
- **Cast file**: the list of which machines may have a tunnel between them.
- **Announcement**: a record in a party's public repo saying a private repo
  is theirs.
- **Backstage**: the demo engine's own network and peers, kept apart so the
  LAN carries only the guide's traffic.
- **MCP**: the Model Context Protocol, where an agent calls tools on servers
  and waits for each answer.
<!-- @demo /glossary -->

<!-- @demo end -->

## Setup

You need what the two guides before this one needed: Docker with the
compose plugin, and a kernel that can make a WireGuard interface. Getting
Started's Setup checks both.

This guide runs on the conductor:

```bash
cd tutorials/lab              # from the repository root
./lab.sh clean                # start from fresh identities
./lab.sh conductor HOOT
```

<!-- @demo terminal -->Then open <http://localhost:8080> and press
**Start**. The `./lab.sh` commands in this guide are also yours to type in
a second terminal while the show is up.<!-- @demo /terminal -->

Six machines boot this time, four of them new to the lab, and each runs a
program of its own; the guide names them, in lines `./lab.sh conductor`
reads. Eight files are new:

```text
tutorials/
├── HOOT.md              this guide
└── lab/
    ├── ride.py          the ride's record types, the cast file, and the loop every agent runs
    ├── rider.py         alice's machine: peer.py, plus a route to sign an allowance
    ├── concierge.py     alice's agent: reads alice's words, asks for bids, chooses, books
    ├── company.py       a ride company, Hoot or Jitney: bids, sends a car, bills
    ├── bank.py          the bank: holds money by its own rules, then pays
    ├── cab.py           the car: drives by its own clock, and photographs the curb
    ├── peek.py          try to read another party's private repos, from this machine
    └── custody.py       walk the ride back, as alice, and check every step
```

Every agent is Getting Started's `peer.py`, imported unchanged, plus one
loop from `ride.py`: follow what it cares about, and write whatever is
missing.

### `peek.py`: try to read another party's private repos

`peek.py` asks every private repo a party announces, from the machine it
runs on, and says what came back: the records, a refusal, or no answer.

```python
"""Try to read another party's private repos, from this machine: python peek.py <party>

Each party announces its private repos, signed, in its public repo: that
they exist, and that they are its. What is in them is another matter. This
asks each one, through the tunnel, the way any reader would, and says what
came back: the records, a refusal, or no answer at all.
"""

import asyncio
import socket
import sys

import httpx
from dids import resolver
from ride import Channel, host

from fastatp import RepoMirror
from fastatp.authz.wireguard import parse_wireguard_service

me = socket.gethostname()  # this machine: jitney.lan, concierge.lan, ...
party = f"did:web:{sys.argv[1].removesuffix('.lan')}.lan"


async def main() -> int:
    async with httpx.AsyncClient(timeout=3) as http:
        # the public repo: anyone may read it, and it holds only the announcements
        public = RepoMirror(party, resolver=resolver())
        answer = await http.get(
            f"http://{host(party)}/xrpc/com.atproto.sync.getRepo", params={"did": party}
        )
        answer.raise_for_status()
        await public.load_checkpoint(answer.content)
        repos = [
            public.get_record(*path.split("/"))["did"]
            for path in sorted(public.entries)
            if path.startswith(f"{Channel.nsid}/")
        ]
        print(f"{host(party)} announces {len(repos)} private repo(s); {me} asks each:")
        tunnel = parse_wireguard_service(await resolver()(party)).tunnel_ip
        read = 0
        for repo in repos:
            try:
                answer = await http.get(
                    f"http://[{tunnel}]/xrpc/com.atproto.sync.getRepo", params={"did": repo}
                )
            except httpx.HTTPError:
                print(f"  {repo}: no answer: no tunnel between {me} and {host(party)}")
                continue
            if answer.status_code == 404:  # how the doorman says no: as if there were nothing
                print(f"  {repo}: refused ({answer.status_code}): no record lets {me} in")
                continue
            if answer.status_code != 200:
                print(f"  {repo}: unavailable ({answer.status_code})")
                continue
            mirror = RepoMirror(repo, resolver=resolver())
            await mirror.load_checkpoint(answer.content)  # its signature and every hash, checked
            kinds = sorted({path.split("/")[0].rsplit(".", 1)[1] for path in mirror.entries})
            kinds = [k for k in kinds if k != "capability"]  # who is let in, not what it says
            print(f"  {repo}: let in, verified: {', '.join(kinds) or 'nothing written yet'}")
            read += 1
    print(f"{me} read {read} of {len(repos)}")
    return 0 if read else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

### `rider.py`: alice's machine

`rider.py` is alice's `peer.py` with one more local route: `/allow` signs
an allowance into a private repo only the bank and the concierge may read.

```python
"""alice's machine in the ride: this machine's peer.py, plus one more local route.

python rider.py alice.lan

alice asks for a ride the way guide one wrote private notes: a note,
committed with /commit into alice's channel, which only alice's agent may
read. The one thing a note can't say is the money: which agent may have which
bank hold how much. That is a record of its own, an Allowance, signed into a
second private repo, one the bank and the agent may read and nobody else.
Like /commit, only this machine may call /allow.

    {"agent": "did:web:concierge.lan", "bank": "did:web:bank.lan",
     "account": "checking", "per_ride": 4000}         $40.00, in cents
"""

import asyncio

import peer  # this machine's peer, exactly as guide one left it
import ride
from pydantic import ValidationError
from ride import BANK, CONCIERGE, Allowance, Plan, dollars, serve
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from fastatp.tid import generate_tid


async def allow(request: Request) -> JSONResponse:
    if (denied := peer.local_only(request)) is not None:
        return denied
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("send one JSON object")
        # cents, exactly: a float or a true/false is not an amount of money
        if type(body.get("per_ride")) is not int:
            raise ValueError("per_ride must be a whole number of cents")
        # the repo it goes in is read by this lab's bank and concierge, so it names them
        if (body.get("bank"), body.get("agent")) != (BANK, CONCIERGE):
            raise ValueError(f"this lab's bank is {BANK} and alice's agent is {CONCIERGE}")
        allowance = Allowance(
            bank=body["bank"],
            agent=body["agent"],
            account=body["account"],
            per_ride=dollars(body["per_ride"]),
            created_at=peer.now(),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    rkey = generate_tid()
    if ride.DESK is None:
        return JSONResponse({"error": "not ready yet"}, status_code=503)
    for_bank = await ride.DESK.repo("bank")
    await for_bank.put_record(Allowance.nsid, rkey, allowance)
    return JSONResponse({"uri": f"at://{for_bank.did}/{Allowance.nsid}/{rkey}"})


def decide(seen, now) -> Plan:
    """alice writes by hand; these are only who may read what alice writes."""
    plan = Plan()
    plan.let("main", CONCIERGE)  # alice's notes: for alice's agent
    plan.let("bank", BANK, CONCIERGE)  # alice's allowance: for the bank, and the agent it names
    return plan


if __name__ == "__main__":
    # nothing to follow and nothing to decide: alice writes by hand
    asyncio.run(
        serve(
            peer,
            decide,
            "alice's machine: /commit for notes, /allow for money",
            routes=[Route("/allow", allow, methods=["POST"])],
        )
    )
```

### `ride.py`: the records, the cast file, and the loop

`ride.py` holds every record the ride writes, the cast file of who may
reach whom, and the loop every agent runs around its own rules.

```python
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
    owner: Did  # whose account the money is held from, and which: the hold says,
    account: Annotated[str, MaxLength(32)]  # so the ledger needs nothing else to count it
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
```

### `concierge.py`: alice's agent

`concierge.py` turns alice's note into an open request, takes the best bid
it saw in time, asks the bank to hold the money, and books the ride.

```python
"""alice's agent, the concierge: this machine's peer.py, plus a loop that turns alice's words into a ride.

python concierge.py concierge.lan

It follows alice's repo, the ride companies' and the bank's, and writes, in
its own repo, each step that is missing:

1. alice wrote a note asking for a ride: the concierge writes what it
   understood (an Intent), then a Request, for anyone who will bid.
2. The bidding time is over: it takes the cheapest bid it saw in time and
   asks the bank to hold the money for that company (an Authorize).
3. The bank held it: it books the ride with the winner (a Booking).

It calls no one. Any company let in to read its requests may bid; a bid it
never sees in time is a no-bid.

Its privacy is its own to compose: one private repo for what it understood
(alice only), one for the bank, one for the ride companies, who may read it
only while a request is open, then are let go, except the winner, until the
ride is paid for. Each is one line of its plan.
"""

import asyncio
import os
import re
from datetime import datetime, timedelta

import peer  # this machine's peer, exactly as guide one left it
from lexicons import Note
from ride import (
    ALICE,
    BANK,
    COMPANIES,
    CONCIERGE,
    Allowance,
    Authorize,
    Booking,
    Hold,
    Intent,
    Plan,
    Quote,
    Ref,
    Refusal,
    Request,
    Seen,
    Settlement,
    dollars,
    rkey,
    serve,
    when,
)

BID_SECONDS = float(os.environ.get("BID_SECONDS", "3"))  # how long a request stays open
READER = "stand-in: concierge.py read(), rules in place of a model"
NUMBERS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "hundred": 100}


def read(text: str) -> dict | None:
    """The stand-in reader: a sentence in, what alice wants out, or None.

    In production this is where a model reads alice's words. Here a few rules
    play its part, honestly named in every Intent it produces. Which workload
    a follow triggers is the builder's choice: a model, rules, a person."""
    words = text.lower()
    to = re.search(r"\bto (?:the )?([a-z][a-z ]*?)\s*(?:,|\.|$|\bunder\b|\bfor\b)", words)
    under = re.search(r"\bunder \$?(\d+|" + "|".join(NUMBERS) + r")\b", words)
    on = re.search(r"\bon (checking|savings)\b", words)
    if not (to and under and on):
        return None  # not a ride it understands: it does nothing, and says nothing
    most = int(under.group(1)) if under.group(1).isdigit() else NUMBERS[under.group(1)]
    return {
        "pickup": "home",
        "destination": to.group(1).strip(),
        "most": dollars(most * 100),
        "account": on.group(1),
    }


CARS = ["hatchback", "sedan", "suv"]  # a tie on price goes to the nicer car: later is nicer


def bids(seen: Seen, request_ref: Ref, request: Request) -> list[tuple[Ref, Quote]]:
    """Every bid on this request the concierge saw before the deadline."""
    deadline = when(request.bids_by)
    return [
        (ref, quote)
        for company in COMPANIES
        for ref, quote in seen.all(Quote, by=company)
        # this version of the bid, seen in time: a bid changed later is a new, late one
        if quote.request == request_ref and seen.since(ref) <= deadline
    ]


def choose(offers: list[tuple[Ref, Quote]], most) -> tuple[Ref, Quote] | None:
    """The cheapest bid within what alice will pay; on a tie, the nicer car."""
    within = [(ref, q) for ref, q in offers if q.price.value() <= most.value()]
    nicer = {car: i for i, car in enumerate(CARS)}
    return min(within, key=lambda o: (o[1].price.value(), -nicer.get(o[1].car, -1)), default=None)


def decide(seen: Seen, now: datetime) -> Plan:
    plan = Plan()
    # Its repos, and who reads each. alice reads them all: they are alice's
    # errands. "main" holds what it understood; "desk" what it asks the bank;
    # "rides" what it asks the ride companies, who are let in while a request
    # is open, then let go, except a winner, until its ride is paid for.
    plan.let("main", ALICE)
    plan.let("desk", ALICE, BANK)
    plan.let("rides", ALICE)
    allowances = [(r, a) for r, a in seen.all(Allowance, by=ALICE) if a.agent == CONCIERGE]
    mine = {rkey(ref): (ref, x) for ref, x in seen.all(Intent, by=CONCIERGE)}
    requests = {rkey(ref): (ref, r) for ref, r in seen.all(Request, by=CONCIERGE)}

    # 1. alice's words, understood, then put out for bids
    for said, note in seen.all(Note, by=ALICE):
        key = rkey(said)  # one ride, one key, in every repo it touches
        wanted = read(note.text)
        if wanted is None:
            continue
        if key not in mine:
            plan.write(
                "main", key, Intent(said=said, reader=READER, created_at=peer.now(), **wanted)
            )
            continue
        intent_ref, intent = mine[key]
        if key not in requests:
            plan.let("rides", *COMPANIES)  # anyone who bids: let in before the request lands
        bids_by = when(note.created_at) + timedelta(seconds=BID_SECONDS)
        bids_by = max(bids_by, seen.since(intent_ref) + timedelta(seconds=BID_SECONDS))
        request = Request(
            intent=intent_ref,
            pickup=intent.pickup,
            destination=intent.destination,
            bids_by=bids_by.isoformat().replace("+00:00", "Z"),
            created_at=peer.now(),
        )
        plan.write("rides", key, request)

    # 2. bidding closed: the best bid, and the bank asked to hold the money, once
    authorized = {rkey(ref) for ref, _ in seen.all(Authorize, by=CONCIERGE)}
    for key, (request_ref, request) in requests.items():
        if now < when(request.bids_by):
            plan.let("rides", *COMPANIES)  # still open: every company may see it and bid
            continue
        if key in authorized or key not in mine:
            continue
        intent = mine[key][1]
        # alice's newest say-so for the account alice named, or no money is asked for
        fitting = [(r, a) for r, a in allowances if a.account == intent.account]
        if not fitting:
            continue
        best = choose(bids(seen, request_ref, request), intent.most)
        if best is None:
            continue  # no bid in time, or none within alice's limit: no ride
        plan.let("rides", seen.who(best[0]))  # the winner stays; the others go
        allowance_ref, _ = fitting[-1]
        authorize = Authorize(
            allowance=allowance_ref,
            quote=best[0],
            payee=seen.who(best[0]),
            amount=intent.most,
            account=intent.account,
            created_at=peer.now(),
        )
        plan.write("desk", key, authorize)

    # 3. the money is held: the ride is booked with the winner, who alone stays
    #    in "rides" until the bank has paid it
    settled = {s.hold.uri for _, s in seen.all(Settlement, by=BANK)}
    refused = {(r.authorize.uri, r.authorize.cid) for _, r in seen.all(Refusal, by=BANK)}
    for authorize_ref, authorize in seen.all(Authorize, by=CONCIERGE):
        key = rkey(authorize_ref)
        held = [ref for ref, h in seen.all(Hold, by=BANK) if h.authorize == authorize_ref]
        if key not in requests:
            continue
        if (authorize_ref.uri, authorize_ref.cid) in refused:
            continue  # no money, no ride: the payee is let go with everyone else
        if not held or held[0].uri not in settled:
            plan.let("rides", authorize.payee)
        if not held:
            continue
        request_ref, request = requests[key]
        passed = [ref for ref, _ in bids(seen, request_ref, request) if ref != authorize.quote]
        booking = Booking(
            request=request_ref,
            quote=authorize.quote,
            passed=passed,
            hold=held[0],
            created_at=peer.now(),
        )
        plan.write("rides", key, booking)
    return plan


if __name__ == "__main__":
    asyncio.run(serve(peer, decide, "taking alice's notes as ride requests"))
```

### `company.py`: a ride company

`company.py` bids on every open request, sends a car once it has seen the
hold in the bank's own repo, and bills when its car is done.

```python
"""A ride company: this machine's peer.py, plus a loop that bids, dispatches and bills.

python company.py hoot.lan     (or jitney.lan: the same program, its own prices)

It follows ride requests from the concierges it knows, the bank, and its own
cars, and writes, in its own repo, each step that is missing:

1. A request it can serve, still open: a bid (a Quote).
2. A booking for its bid: it checks the hold in the bank's own repo, not on
   the concierge's word, and sends a car (a Dispatch).
3. Its car dropped the rider off: the bill (a Fare).
"""

import asyncio
from datetime import datetime

import peer  # this machine's peer, exactly as guide one left it
from ride import (
    ALICE,
    BANK,
    CAB,
    CONCIERGE,
    Booking,
    Dispatch,
    Dropoff,
    Fare,
    Hold,
    Plan,
    Quote,
    Request,
    Seen,
    dollars,
    rkey,
    serve,
    when,
)

# Each company's own business: what it charges, how soon, in what, with which cars.
# The bid is all the concierge learns of it.
# readers: who may read the company's private repo. Hoot lets in the
# concierge it bids to, the bank that pays it, its car, and the rider; Jitney,
# which this story has lose, keeps its bids to the concierge and the bank.
OFFERS = {
    "hoot.lan": {
        "price": dollars(3400),
        "eta": 4,
        "car": "sedan",
        "cars": [CAB],
        "readers": [CONCIERGE, BANK, CAB, ALICE],
    },
    "jitney.lan": {
        "price": dollars(3700),
        "eta": 3,
        "car": "hatchback",
        "cars": [],
        "readers": [CONCIERGE, BANK],
    },
}


def company(domain: str):
    me = f"did:web:{domain}"
    offer = OFFERS[domain]

    def decide(seen: Seen, now: datetime) -> Plan:
        plan = Plan()
        plan.let("main", *offer["readers"])

        # 1. open requests: a bid on each
        for request_ref, request in seen.all(Request, by=CONCIERGE):
            if now < when(request.bids_by):
                quote = Quote(
                    request=request_ref,
                    price=offer["price"],
                    eta_minutes=offer["eta"],
                    car=offer["car"],
                    created_at=peer.now(),
                )
                plan.write("main", rkey(request_ref), quote)

        # 2. a booking for one of its bids: the hold checked in the bank's repo, then a car
        for booking_ref, booking in seen.all(Booking, by=CONCIERGE):
            # exactly the bid it signed, for exactly the request the booking names
            quote = seen.get(booking.quote, Quote)
            if seen.who(booking.quote) != me or quote is None or not offer["cars"]:
                continue
            if quote.request != booking.request or seen.who(booking.hold) != BANK:
                continue  # a hold is only a hold in the bank's own repo
            hold = seen.get(booking.hold, Hold)
            price = quote.price
            if hold is None or hold.payee != me or hold.amount.value() < price.value():
                continue  # no such hold, not for this company, or too small: no car
            dispatch = Dispatch(
                booking=booking_ref, hold=booking.hold, car=offer["cars"][0], created_at=peer.now()
            )
            plan.write("main", rkey(booking_ref), dispatch)

        # 3. its car's dropoff: the fare, the price it bid
        for dispatch_ref, dispatch in seen.all(Dispatch, by=me):
            booking = seen.get(dispatch.booking, Booking)  # the booking exactly as dispatched
            quote = booking and seen.get(booking.quote, Quote)
            if booking is None or quote is None:
                continue
            for dropoff_ref, dropoff in seen.all(Dropoff, by=dispatch.car):
                if dropoff.dispatch == dispatch_ref:
                    fare = Fare(
                        booking=dispatch.booking,
                        dropoff=dropoff_ref,
                        hold=dispatch.hold,
                        amount=quote.price,
                        created_at=peer.now(),
                    )
                    plan.write("main", rkey(dispatch_ref), fare)
        return plan

    return me, offer, decide


if __name__ == "__main__":
    me, offer, decide = company(peer.domain)
    asyncio.run(serve(peer, decide, "bidding on ride requests"))
```

### `bank.py`: the bank

`bank.py` holds money only on alice's own signed allowance and its own
limits, in a private repo the payee is let into, then pays the fare.

```python
"""The bank: this machine's peer.py, plus a loop that holds and pays, by its own rules.

python bank.py bank.lan

It follows alice, the concierge and the ride companies, and writes, in its
own repo, each step that is missing:

1. Its own limits: the most it pays any one payee at once.
2. A request from the concierge to hold money: it checks alice's own
   allowance, the limits and the balance, then holds the money (a Hold) or
   says why not (a Refusal).
3. A payee's fare: it pays it from the hold and lets the rest go (a Settlement).
"""

import asyncio
from datetime import datetime

import peer  # this machine's peer, exactly as guide one left it
from ride import (
    ALICE,
    BANK,
    COMPANIES,
    CONCIERGE,
    Allowance,
    Authorize,
    Fare,
    Hold,
    Money,
    PayeeLimit,
    Plan,
    Refusal,
    Seen,
    Settlement,
    dollars,
    host,
    rkey,
    serve,
)

# The bank's own books. In a real bank these live in its database, not here.
BALANCES = {(ALICE, "checking"): dollars(25000)}
# The most each payee is paid at once, whoever asks. The bank checks its
# books; it also writes them as records, for the customer and the agent.
LIMITS = {payee: dollars(10000) for payee in COMPANIES}


WAIT = "wait"  # not a refusal: something it needs has not reached this bank yet


def spoken_for(seen: Seen, account: tuple[str, str]) -> int:
    """Cents of this account already gone or set aside: what each settled hold
    paid out, and each open hold in full."""
    settled = {(s.hold.uri, s.hold.cid): s for _, s in seen.all(Settlement, by=BANK)}
    cents = 0
    for ref, hold in seen.all(Hold, by=BANK):
        if (hold.owner, hold.account) == account:
            done = settled.get((ref.uri, ref.cid))
            cents += done.paid.amount if done else hold.amount.amount
    return cents


def check(seen: Seen, authorize_ref, authorize: Authorize, pending: dict) -> str | None:
    """Why the bank will not hold this money (a reason), WAIT, or None if it will.

    pending: cents this same pass has already agreed to hold, by account."""
    if authorize.allowance.uri not in seen.records:
        return WAIT  # alice's allowance may simply not have arrived yet
    allowance = seen.get(authorize.allowance, Allowance)
    if allowance is None:
        return "that allowance changed since it was named"
    owner = seen.who(authorize.allowance)
    if (
        allowance.bank != BANK
        or allowance.agent != seen.who(authorize_ref)
        or allowance.account != authorize.account
    ):
        return "the allowance does not let this agent spend from this account at this bank"
    if authorize.amount.value() > allowance.per_ride.value():
        return f"{authorize.amount} is over the {allowance.per_ride} the allowance gives a ride"
    limit = LIMITS.get(authorize.payee)
    if limit is None or authorize.amount.value() > limit.value():
        return f"{host(authorize.payee)} is not a payee this bank pays that much"
    account = (owner, authorize.account)
    balance = BALANCES.get(account)
    if balance is None:
        return "no such account"
    free = balance.amount - spoken_for(seen, account) - pending.get(account, 0)
    if free < authorize.amount.amount:
        return "not enough money in that account"
    return None


def decide(seen: Seen, now: datetime) -> Plan:
    plan = Plan()
    # Everything private. "main", for the customer and the customer's agent:
    # its limits and its refusals. One repo per hold, made when it holds: the
    # customer, the agent, and the payee the agent named, let in at that
    # moment, so the payee can check the money is there. No other payee, and
    # no other customer, sees it.
    plan.let("main", ALICE, CONCIERGE)

    # 1. its own limits, written once
    for payee, limit in LIMITS.items():
        plan.write(
            "main", host(payee), PayeeLimit(payee=payee, per_payment=limit, created_at=peer.now())
        )

    # 2. each request to hold money, answered once, either way
    # matched by the full ref: a request changed since is a new request
    holds = {(h.authorize.uri, h.authorize.cid): h for _, h in seen.all(Hold, by=BANK)}
    refused = {(r.authorize.uri, r.authorize.cid) for _, r in seen.all(Refusal, by=BANK)}
    pending: dict = {}  # cents agreed in this pass, by account, so two can't both spend the same
    for authorize_ref, authorize in seen.all(Authorize, by=CONCIERGE):
        key = rkey(authorize_ref)
        answer = (authorize_ref.uri, authorize_ref.cid)
        if answer in holds:
            plan.let(f"ride-{key}", ALICE, CONCIERGE, holds[answer].payee)
            continue
        if answer in refused:
            continue
        why = check(seen, authorize_ref, authorize, pending)
        if why == WAIT:
            continue
        if why is not None:
            plan.write(
                "main", key, Refusal(authorize=authorize_ref, reason=why, created_at=peer.now())
            )
            continue
        hold = Hold(
            authorize=authorize_ref,
            owner=seen.who(authorize.allowance),
            account=authorize.account,
            payee=authorize.payee,
            amount=authorize.amount,
            created_at=peer.now(),
        )
        account = (seen.who(authorize.allowance), authorize.account)
        pending[account] = pending.get(account, 0) + authorize.amount.amount
        plan.let(f"ride-{key}", ALICE, CONCIERGE, authorize.payee)
        plan.write(f"ride-{key}", key, hold)

    # 3. a fare from the payee of a hold: paid from it, the rest let go
    for company in COMPANIES:
        for fare_ref, fare in seen.all(Fare, by=company):
            if seen.who(fare.hold) != BANK:
                continue  # only a hold in this bank's own repo
            hold = seen.get(fare.hold, Hold)
            if not hold or hold.payee != company or fare.amount.value() > hold.amount.value():
                continue  # not a hold for this company, or less than the fare
            rest = Money(amount=hold.amount.amount - fare.amount.amount)  # both in cents
            settlement = Settlement(
                hold=fare.hold,
                fare=fare_ref,
                paid=fare.amount,
                released=rest,
                created_at=peer.now(),
            )
            key = rkey(fare.hold)
            plan.let(f"ride-{key}", ALICE, CONCIERGE, company)
            plan.write(f"ride-{key}", key, settlement)
    return plan


if __name__ == "__main__":
    asyncio.run(serve(peer, decide, "holding and paying"))
```

### `cab.py`: the car

`cab.py` drives by its own clock, a minute a second: positions on the way,
a signed photo of the curb, the ride, and the dropoff.

```python
"""A car: this machine's peer.py, plus a loop that drives, by its own clock.

python cab.py cab-17.lan

It follows the company it drives for. Given a dispatch, it drives: a
position each minute on the way to the pickup, a photo of the curb on
arrival, a position each minute of the ride, then the dropoff. All of it is
signed by the car, in the car's own repo.

Everything here is pretend: the clock runs a minute a second (MINUTE_SECONDS),
the GPS is a straight line, the photo is a drawing. What it shows is real:
whatever the car says, it signs, and nobody can say it in the car's place.
"""

import asyncio
import os
from datetime import datetime, timedelta

import peer  # this machine's peer, exactly as guide one left it
import ride
from ride import ALICE, Arrival, Dispatch, Dropoff, Plan, Position, Seen, rkey, serve

MINUTE = timedelta(seconds=float(os.environ.get("MINUTE_SECONDS", "1")))
if MINUTE.total_seconds() <= 0:
    raise ValueError("MINUTE_SECONDS must be positive: the car's clock has to move forward")
TO_PICKUP, RIDE = 4, 17  # minutes
# A pretend map, in millionths of a degree: where the car starts, the
# pickup, and the airport.
START = (45_010_000, -100_020_000)
PICKUP = (45_000_000, -100_000_000)
AIRPORT = (45_090_000, -100_150_000)
COMPANY = "did:web:hoot.lan"  # the company this car drives for


def along(a, b, part: float) -> tuple[int, int]:
    return round(a[0] + (b[0] - a[0]) * part), round(a[1] + (b[1] - a[1]) * part)


def curb(when: str) -> bytes:
    """The photo of the curb, as this car's camera took it: here, a drawing."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">
<rect width="320" height="180" fill="#1e2a38"/><rect y="120" width="320" height="60" fill="#3b3b3b"/>
<rect y="112" width="320" height="8" fill="#9a9a9a"/><rect x="40" y="40" width="90" height="72" fill="#5a4636"/>
<rect x="72" y="70" width="26" height="42" fill="#2b2018"/><circle cx="250" cy="40" r="14" fill="#e8e2c6"/>
<text x="12" y="172" fill="#ffffff" font-family="monospace" font-size="10">alice's curb, as cab-17 saw it, {when}</text>
</svg>""".encode()


def car(me: str, add_blob):
    """This car's loop. add_blob stores the photo's bytes beside the repo and
    returns the reference a record carries."""
    photos: dict[str, object] = {}  # one photo per ride, taken once

    def decide(seen: Seen, now: datetime) -> Plan:
        plan = Plan()
        plan.let(
            "ride", COMPANY, ALICE
        )  # a repo of its own, for the company it drives for and the rider
        for dispatch_ref, dispatch in seen.all(Dispatch, by=COMPANY):
            if dispatch.car != me:
                continue
            key = rkey(dispatch_ref)
            started = seen.since(dispatch_ref)  # the car's own clock starts when it hears
            minutes = int((now - started) / MINUTE)
            for m in range(min(minutes, TO_PICKUP) + 1):  # on the way to the pickup
                where = along(START, PICKUP, m / TO_PICKUP)
                leg = position(dispatch_ref, "to pickup", TO_PICKUP - m, where)
                plan.write("ride", f"{key}-a{m:02d}", leg)
            if minutes < TO_PICKUP:
                continue
            if key not in photos:  # at the curb: one photo, taken once
                taken = add_blob(curb(peer.now()), "image/svg+xml")
                if taken is None:
                    continue  # nowhere to keep it yet: the next pass takes it
                photos[key] = taken
            arrival = Arrival(dispatch=dispatch_ref, photo=photos[key], created_at=peer.now())
            plan.write("ride", key, arrival)
            for m in range(min(minutes - TO_PICKUP, RIDE) + 1):  # the ride
                where = along(PICKUP, AIRPORT, m / RIDE)
                plan.write(
                    "ride", f"{key}-r{m:02d}", position(dispatch_ref, "riding", RIDE - m, where)
                )
            arrived = [ref for ref, a in seen.all(Arrival, by=me) if a.dispatch == dispatch_ref]
            if minutes >= TO_PICKUP + RIDE and arrived:  # at the airport
                dropoff = Dropoff(
                    dispatch=dispatch_ref, arrival=arrived[0], minutes=RIDE, created_at=peer.now()
                )
                plan.write("ride", key, dropoff)
        return plan

    return decide


def position(dispatch_ref, leg: str, left: int, where: tuple[int, int]) -> Position:
    return Position(
        dispatch=dispatch_ref,
        leg=leg,
        minutes_left=left,
        lat_e6=where[0],
        lon_e6=where[1],
        created_at=peer.now(),
    )


if __name__ == "__main__":

    async def main() -> None:
        def add_blob(data: bytes, mime: str):
            # the photo's bytes go beside the ride's repo, which the car made
            # with a place for them; the arrival record names them by hash
            kept = ride.DESK.repos.get("ride") if ride.DESK else None
            return None if kept is None else kept.repo.add_blob(data, mime)

        decide = car(f"did:web:{peer.domain}", add_blob)
        await serve(peer, decide, f"driving for {COMPANY}")

    asyncio.run(main())
```

### `custody.py`: the ride, walked back

`custody.py` follows every strong ref back from the bank's settlement to
alice's own note, checking each step's signature and hash as it goes.

```python
"""Walk the ride back, as alice, and check every step: python custody.py

Starts from the bank's settlement and follows every strong ref back to
alice's own note. Each record is fetched from its owner's private repo,
through the tunnel, and checked twice: the repo's commit is signed by the
repo's key, and the record's hash is the one the step before it named. Whose
repo it is comes from the owner's signed announcement, not from a server.

It knows nothing about rides. A strong ref ({"uri", "cid"}) looks the same in
any record, so this is the walk for any work done this way. A step alice was
never let in to read is reported as sealed, not as a failure.
"""

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
from dids import resolver
from ride import ALICE, BANK, CAB, COMPANIES, CONCIERGE, Channel, host

from fastatp import RepoMirror
from fastatp.authz.wireguard import parse_wireguard_service
from fastatp.cid import CID

PARTIES = [ALICE, CONCIERGE, BANK, *COMPANIES, CAB]


def refs(value) -> Iterator[dict]:
    """Every strong ref inside a record, wherever it sits, in order."""
    if isinstance(value, dict):
        if (
            isinstance(value.get("uri"), str)
            and value["uri"].startswith("at://")
            and "cid" in value
        ):
            yield value
            return
        for inner in value.values():
            yield from refs(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from refs(inner)


def walk(start: dict, look) -> Iterator[tuple[int, dict, str, dict | None]]:
    """Depth first from one ref: (depth, ref, verdict, record) for each step.

    look(ref) -> (verdict, record): "verified", "sealed", or "changed"."""
    checked: set[tuple[str, str]] = set()

    def visit(ref: dict, depth: int) -> Iterator[tuple[int, dict, str, dict | None]]:
        # a step is a record AND its hash: the same address named with another
        # hash is another claim, and is checked on its own
        if (ref["uri"], ref["cid"]) in checked:
            yield depth, ref, "again", None
            return
        checked.add((ref["uri"], ref["cid"]))
        verdict, record = look(ref)
        yield depth, ref, verdict, record
        if verdict == "verified":
            for inner in refs(record):
                yield from visit(inner, depth + 1)

    yield from visit(start, 0)


async def main() -> int:
    async with httpx.AsyncClient(timeout=3) as http:
        # Whose is each private repo? Each party's public repo says, signed.
        owners: dict[str, str] = {}
        for party in PARTIES:
            mirror = RepoMirror(party, resolver=resolver())
            public = await http.get(
                f"http://{host(party)}/xrpc/com.atproto.sync.getRepo", params={"did": party}
            )
            await mirror.load_checkpoint(public.content)
            for path in mirror.entries:
                if path.startswith(f"{Channel.nsid}/"):
                    owners[mirror.get_record(*path.split("/"))["did"]] = party

        # Every private repo alice is let in to, read whole through the tunnel and
        # checked: the commit's signature against the repo's key, every hash.
        records: dict[str, tuple[str, dict]] = {}  # uri -> (cid, record)
        sealed: set[str] = set()
        for repo, party in owners.items():
            if party == ALICE:
                continue  # alice's own: alice knows what alice wrote (below)
            tunnel = parse_wireguard_service(await resolver()(party)).tunnel_ip
            try:
                answer = await http.get(
                    f"http://[{tunnel}]/xrpc/com.atproto.sync.getRepo", params={"did": repo}
                )
            except httpx.TransportError:
                sealed.add(repo)  # no tunnel to it: alice was never meant to reach it
                continue
            if answer.status_code == 404:
                sealed.add(repo)  # the doorman's no: no record lets alice in
                continue
            answer.raise_for_status()  # anything else is a fault, not a seal: the walk stops
            mirror = RepoMirror(repo, resolver=resolver())
            await mirror.load_checkpoint(answer.content)
            for path, cid in mirror.entries.items():  # each hash, from the verified tree
                records[f"at://{repo}/{path}"] = (cid.encode(), mirror.get_record(*path.split("/")))
        own = await http.get("http://127.0.0.1/mine")  # alice's own repos, from alice's peer
        own.raise_for_status()
        for repo in own.json():
            for r in repo["records"]:
                records[f"at://{repo['did']}/{r['path']}"] = (r["cid"], r["value"])

        def look(ref: dict) -> tuple[str, dict | None]:
            repo = ref["uri"].removeprefix("at://").split("/")[0]
            if ref["uri"] in records:
                cid, value = records[ref["uri"]]
                return ("verified", value) if cid == ref["cid"] else ("changed", None)
            return ("sealed", None) if repo in sealed else ("missing", None)

        # the newest settlement, by the time the bank signed into it
        settlements = sorted(
            (records[u][1].get("created_at", ""), u)
            for u in records
            if "/com.example.bank.settlement/" in u
        )
        if not settlements:
            print("no settlement yet: the ride isn't paid for")
            return 1
        newest = settlements[-1][1]
        start = {"uri": newest, "cid": records[newest][0]}
        print("the ride, walked back from the bank's settlement, as alice:")
        counts = {"verified": 0, "sealed": 0, "changed": 0, "missing": 0}
        photo = None
        for depth, ref, verdict, record in walk(start, look):
            repo, collection, _ = ref["uri"].removeprefix("at://").split("/")
            kind = collection.rsplit(".", 1)[1]
            who = host(owners.get(repo, repo))
            if verdict == "again":
                continue  # already checked above: a chain can meet itself
            counts[verdict] += 1
            mark = {"verified": "✓ signed, hash matches", "sealed": "· sealed: alice isn't let in"}
            line = mark.get(verdict, f"✗ {verdict.upper()}: not the record named")
            print(f"{'  ' * depth}{kind:<11} {who:<14} {line}  {ref['uri']}")
            if record and isinstance(record.get("photo"), dict):
                photo = (repo, owners.get(repo, repo), record["photo"])

        if photo is not None:  # the curb, fetched and checked against the hash its record names
            repo, party, blob = photo
            tunnel = parse_wireguard_service(await resolver()(party)).tunnel_ip
            answer = await http.get(
                f"http://[{tunnel}]/xrpc/com.atproto.sync.getBlob",
                params={"did": repo, "cid": blob["ref"]["$link"]},
            )
            answer.raise_for_status()
            data = answer.content
            if CID.for_raw(data).encode() == blob["ref"]["$link"]:
                Path("curb.svg").write_bytes(data)  # only a photo that checks out is shown
                print(
                    f"the curb photo: {len(data)} bytes, hash matches,"
                    " saved as curb.svg: is it your curb?"
                )
            else:
                counts["changed"] += 1
                print("the curb photo: ✗ HASH DIFFERS: not the photo the car signed, not shown")
        print(
            f"{counts['verified']} steps verified, {counts['sealed']} sealed from alice,"
            f" {counts['changed'] + counts['missing']} wrong"
        )
        return 0 if counts["changed"] + counts["missing"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

## Part 1: the cast

<!-- @demo start -->

Not every machine may reach every other. Which may is agreed before the
story starts, out of band, the way you open an account with a bank: a
tunnel between each pair that agreed, and none anywhere else.

![six parties; lines are tunnels agreed out of band; there is none between hoot and jitney](sketches/ride-cast.svg)

<!-- @demo point diagram -->

> 🔑 **Key concept: a tunnel is a door, and a record is who may come
> through.** A tunnel only says two machines may reach each other at all.
> Which repo a party may read behind that door is a record in the repo,
> written by its owner and deleted by its owner, while the story runs. A
> deleted record is a door closed: absence is the revocation. Hoot and
> Jitney have no tunnel between them at all.

Each party keeps its records in private repos. Its public repo holds only
announcements: signed lines saying "this private repo exists, and it is
mine". What is inside, and who may read it, stays private.

The concierge is alice's agent: it acts for alice, the way a coding agent
acts for the person who runs it. Whatever it does is alice's errand, so
everything it writes, alice can read.

<!-- @demo ask alice hasn't asked for anything yet. What has alice's agent written so far? -->

```bash
./lab.sh alice python peek.py concierge
```

```text
concierge.lan announces 3 private repo(s); alice.lan asks each:
  did:key:zQ3shf4N2w8R6HE7UeJGJ83qiruAASC5f3nm5aLTwr1wzDeqA: let in, verified: nothing written yet
  did:key:zQ3shgLucFWfAUTjwqY7meqT18QaWQCMuoBqX6m7ayoigMzbf: let in, verified: nothing written yet
  did:key:zQ3shrFcj6mHrJDsGPawUu4p6Pe6R4mPPWujXwURLaewLpuoJ: let in, verified: nothing written yet
alice.lan read 3 of 3
```

Nothing yet: three private repos, all empty. alice reads the agent's work
the way anyone reads a repo, through the tunnel, checking the signature on
each, so alice never has to take even the agent's word for it.

<!-- @demo close code -->

Three, because the concierge keeps alice's business apart for alice: one
repo for what it understood alice to say, which only alice reads; one for
what it asks the bank; one for what it asks the ride companies. Who else
reads each one is the agent's to decide, on alice's behalf.

<!-- @demo point concierge.repo -->

Acting for alice is one thing; spending alice's money is another, and the
bank won't take the agent's word for it. So alice signs a say-so: alice's
agent may have alice's bank hold up to $40 for a ride, from checking. It goes
in a private repo of alice's own that only the bank and the concierge read.

<!-- @demo code rider.py "async def allow(" -->

```bash
./lab.sh alice curl -s -X POST localhost/allow -d '{"agent": "did:web:concierge.lan", "bank": "did:web:bank.lan", "account": "checking", "per_ride": 4000}'
```

```text
{"uri":"at://did:key:zQ3shVThs3LYBaVAMwHazQpE8eah96zoFhUMthD7EcGJps9ew/com.example.bank.allowance/3n3ctic4agstu"}
```

<!-- @demo point alice.repo -->

<!-- @demo ask Later the concierge will ask the bank to hold alice's money. Why should the bank believe it? -->

> 🔑 **Key concept: the bank checks alice's signature, not the agent's
> word.** When the concierge asks for a hold, it names alice's allowance by
> strong ref. The bank reads that allowance from alice's own repo and checks
> three things: alice signed it, it names this agent, and the amount fits
> inside it. The concierge holds no key of alice's, so it can't stretch the
> allowance or invent one. For this to work, alice once told the bank to
> trust records alice signs. A bank's builders could set that up with an
> OAuth login if they chose; the next guide shows how to do it with no
> shared secret on the wire at all.

![alice signs an allowance; the concierge asks the bank to hold forty dollars under it; the bank checks alice's signature and the amount, then holds](sketches/allowance.svg)

<!-- @demo chapter 2 "The ask and the bids" -->
<!-- @demo blurb alice asks once, in a private note. The concierge writes one request, and two ride companies bid on it, each sealed from the other. -->

## Part 2: the ask and the bids

alice asks the way Getting Started wrote a private note. How a person asks
from a friendly app in a browser is the next guide's subject; here alice
commits a record by plain means.

<!-- @demo ask alice names no ride company. Who will answer? -->
<!-- @demo code concierge.py "def read(" -->

```bash
./lab.sh alice curl -s -X POST localhost/commit -d '{"repo": "channel", "text": "Get me a ride to the airport, under $40, put it on checking"}'
```

```text
{"uri":"at://did:key:zQ3shReKLwygEnFKs3tAza6fPa4sS55UMUu7xnhBvyuMrePi9/com.example.tutorial.note/3n3ctic7cuqtu", ...}
```

The concierge follows alice's private repo. The note is signed with alice's
key, so the agent knows it came from alice, and from here on it acts for
alice. Its reader has read the note: a few rules that turn alice's sentence
into where, how much and from which account.

> ⚠️ **Important: the reader is a stand-in.** In production a model would
> read alice's sentence. Here a few rules play its part, and every intent
> the concierge writes says so, in its `reader` field. What matters is the
> follow, not the model: a builder chooses the work a follow triggers, a
> model, rules or a person, and the record names which one did it.

<!-- @demo close code -->

Now watch every repo. The concierge wrote what it understood in its `main`
repo, which only alice reads, and a ride request in `rides`, which Hoot and
Jitney are let into while it is open.

<!-- @demo point concierge.repo -->

The request names no company. Hoot and Jitney follow ride requests, so each
read it and wrote a quote, in its own repo, in its own time.

<!-- @demo point hoot.repo jitney.repo -->

<!-- @demo ask To get two quotes from two companies over MCP, what does the agent have to do? -->

> 🔑 **Key concept: one record, any number of answers.** With calls, an
> agent must know every company, connect to each one's server, and call its
> quote tool, holding each call open for the answer. Here the concierge
> wrote one record and called no one. Any company let in to read its
> requests may bid; adding one is one more tunnel and one more reader, not
> one more integration. The work moves to the
> companies that want the ride. The cost, plainly: this works only if the
> companies speak atproto, not necessarily FastATP. It is a picture of what
> could be, not of today.

![with calls the agent calls each company's quote tool and holds each open; with records the concierge writes one request and each company answers in its own repo](sketches/open-request.svg)

Hoot bid $34, in a sedan. Jitney bid $37, in a hatchback. Each bid sits in
its own company's private repo.

<!-- @demo ask Jitney would love to see Hoot's price before bidding. Can it? -->
<!-- @demo fails -->

```bash
./lab.sh jitney python peek.py hoot
```

```text
hoot.lan announces ... private repo(s); jitney.lan asks each:
  did:key:zQ3shvC32gTYZe4AzWcvMwQa6xyBRUEjnL3h3hx6wqzVL8nec: no answer: no tunnel between jitney.lan and hoot.lan
jitney.lan read 0 of ...
```

No. Jitney's machine knocked on Hoot's repo and nothing answered, because
there is no tunnel between them.

<!-- @demo close code -->

> 🔑 **Key concept: sealed bids.** Hoot and Jitney have no tunnel between
> them, and neither has a record letting the other in. Neither can wait for
> the other's bid and then undercut it. The concierge is let into both, so
> it sees both, each checked against its own company's signature.

![hoot's quote and jitney's quote each in that company's repo; the concierge reads both; neither company can read the other's](sketches/sealed-bids.svg)

The concierge, which Jitney did let in, reads Jitney's bid.

```bash
./lab.sh concierge python peek.py jitney
```

```text
jitney.lan announces 1 private repo(s); concierge.lan asks each:
  did:key:zQ3shhHHweYKQgUqDaJ7dgMTWiqEgaaffscR2enZQjiiKpjLi: let in, verified: quote
concierge.lan read 1 of 1
```

<!-- @demo ask Hoot's bid landed in time. What if Jitney's had landed a minute late? -->

> 🔑 **Key concept: the agent's own rules, on its own clock.** The request
> said when bids close. The concierge counts only the bids it saw by then:
> a late bid is a no-bid, by the concierge's clock, not the company's. It
> takes the cheapest within alice's $40, and on a tie, the nicer car. Those
> rules are the builder's, and they compose on lexicons: a rating, a pickup
> time, a company alice blocked. Each is a few lines over records. With no
> bid in time, it could extend the window. It could even pay the winner the
> runner-up's price, a second-price rule that rewards an honest bid: auction
> theory as a pluggable policy. A new price is just a new commit the agent
> may take or ignore, and rounds of haggling are just more commits.

<!-- @demo close code -->

Nobody here holds all the power. alice gets competition on alice's terms:
price, car, timing. The companies compete on their niches and learn from
signed records; a rider who never complains might get the cheapest car at
the cheapest price.

The page shows every repo only because the conductor peeks in backstage,
each machine reporting its own, as the conductor guide shows. In
production there is no conductor, and no one view of all this exists
anywhere. That is the point: no central thing made a single call.

<!-- @demo chapter 3 "The ride" -->
<!-- @demo blurb The bank holds the money for the winner, Hoot sends a car, and the car drives for seventeen minutes with nothing held open. MCP and A2A, side by side. -->

## Part 3: the ride

When bidding closed, the concierge took Hoot's $34 over Jitney's $37, and
wrote, for the bank, a request to hold alice's $40 for Hoot, naming alice's
allowance and Hoot's quote. Jitney's chip left the concierge's `rides`
repo at that moment: Jitney is let go.

<!-- @demo point concierge.repo -->

The bank checked the allowance, its own limits and alice's balance, then
held the money in a private repo made for this one ride. It let in alice,
the concierge, and Hoot: the payee the concierge named. Nobody else.

<!-- @demo ask Hoot won. Can Hoot see the bank's hold, and anything else the bank keeps? -->

```bash
./lab.sh hoot python peek.py bank
```

```text
bank.lan announces 2 private repo(s); hoot.lan asks each:
  did:key:...: refused (404): no record lets hoot.lan in
  did:key:...: let in, verified: hold
hoot.lan read 1 of 2
```

<!-- @demo point hoot.terminal -->

> 🔑 **Key concept: the bank lets the payee see one hold, and nothing
> else.** The concierge named the payee; the bank opened a repo for this
> ride and let Hoot in. Hoot checks the money is really held, in the bank's
> own repo, signed by the bank, before it sends a car, never on the
> concierge's word. The bank's other records stay out of Hoot's sight.
> This could go further: the concierge could forward the bank's own signed
> proof of the hold, so that Hoot checks it without being let into the bank
> at all. A builder can also serve one kind of record to one reader, as
> signed proofs. Privacy composes, one repo and one record at a time.

![the bank's main repo is read by alice and the concierge; this ride's repo, with the hold, is read by alice, the concierge and hoot; jitney reads neither](sketches/hold-repo.svg)

<!-- @demo close code -->

Hoot booked cab-17. The car drove by its own clock, a minute a second here:
four minutes to alice's curb, a photo of it, seventeen minutes to the
airport, a signed position every minute, all in the car's own repo. Hoot
followed the car; the concierge followed Hoot.

<!-- @demo point cab-17.repo hoot.repo -->

<!-- @demo ask The ride took twenty-one minutes. What was held open, waiting, all that time? -->

> 🔑 **Key concept: nothing waited on a connection.** Over MCP, an agent
> calls a tool and holds the call open for its answer; MCP has added
> long-running tasks, but the result still comes back to the caller who
> asked. A2A hands a task to another agent and gets updates by a stream or
> a push to a webhook the caller must expose. Here every party wrote signed
> records in its own repo, in its own time, and the others followed. The
> car could have driven for a day. And afterwards, anyone let in can check
> every step, not just the two ends of a call. Here the bids closed three
> seconds after alice's note, and twenty-one minutes of ride ran in about
> twenty-five seconds. The deal lives in the records, not in anyone's
> session: the concierge could sleep, or restart, and see where things stand
> by reading the repos again. A real one keeps its own repos on disk; this
> lab keeps them in memory, so a restart here would start it afresh.

![MCP: an agent calls each server, held open, with a token; A2A: a task, then a push to a webhook; records: each party signs its own, the other follows; who can check it later: the two ends, the two ends, anyone let in](sketches/three-ways.svg)

Jitney lost. The concierge let Jitney go from its rides repo the moment it
chose Hoot: it deleted the record that let Jitney in.

<!-- @demo ask Can Jitney still read the concierge's ride requests? -->
<!-- @demo fails -->

```bash
./lab.sh jitney python peek.py concierge
```

```text
concierge.lan announces 3 private repo(s); jitney.lan asks each:
  did:key:...: refused (404): no record lets jitney.lan in
  did:key:...: refused (404): no record lets jitney.lan in
  did:key:...: refused (404): no record lets jitney.lan in
jitney.lan read 0 of 3
```

No. The tunnel is still there, but no record lets Jitney through. A
deleted record is the door closed; the next request lets every company back
in. Hoot is let go too, once the bank has paid it. Taking away access is not
cancelling a deal, though: what was agreed changes only by a new signed
record.

<!-- @demo point jitney.terminal -->

<!-- @demo close code -->

<!-- @demo ask So should every tool call become a signed record? When is MCP still the better reach? -->

> 🔑 **Key concept: reach for each where it fits.** MCP is the better reach
> when one owner's model needs a tool now: local files, a shell, an editor,
> answers in milliseconds, nothing anyone will audit. Signed records are the
> better reach when the work crosses owners, runs for minutes or days, fans
> out to whoever wants it, or moves money that someone will later ask about.
> It is not either-or: an agent can call its own tools over MCP and deal
> with everyone else in records.

![reach for MCP: one owner and its own tools, a model waiting in milliseconds, files and a shell, nobody asks later; reach for records: many owners, minutes or days, open bids in parallel, money and who did what](sketches/reach-for.svg)

<!-- @demo chapter 4 "The chain" -->
<!-- @demo blurb alice walks the whole ride back, from the bank's settlement to alice's own words, and checks every signature on the way. Then the curb, and what all this costs. -->

## Part 4: the chain

The car dropped alice at the airport. Hoot billed the $34 it bid, naming
the hold, and the bank paid it from the hold and let the other $6 go.

<!-- @demo point hoot.repo bank.repo -->

<!-- @demo wait 10 -->

<!-- @demo ask alice wasn't watching any of this. How can alice know it happened the way the records say? -->

```bash
./lab.sh alice python custody.py
```

```text
the ride, walked back from the bank's settlement, as alice:
settlement  bank.lan       ✓ signed, hash matches  ...
  fare        hoot.lan       ✓ signed, hash matches  ...
    hold        bank.lan       ✓ signed, hash matches  ...
      authorize   concierge.lan  ✓ signed, hash matches  ...
        quote       hoot.lan       ✓ signed, hash matches  ...
          request     concierge.lan  ✓ signed, hash matches  ...
            intent      concierge.lan  ✓ signed, hash matches  ...
              note        alice.lan      ✓ signed, hash matches  ...
        allowance   alice.lan      ✓ signed, hash matches  ...
    booking     concierge.lan  ✓ signed, hash matches  ...
      quote       jitney.lan     · sealed: alice isn't let in  ...
    dropoff     cab-17.lan     ✓ signed, hash matches  ...
      arrival     cab-17.lan     ✓ signed, hash matches  ...
        dispatch    hoot.lan       ✓ signed, hash matches  ...
the curb photo: ... bytes, hash matches, saved as curb.svg: is it your curb?
13 steps verified, 1 sealed from alice, 0 wrong
```

alice walked back from the bank's settlement through every strong ref to
alice's own note. Each step came from its owner's private repo, through the
tunnel, and was checked twice: the owner's signature, and the hash the step
before it named. One step is sealed: Jitney's losing bid, which alice was
never let in to read.

<!-- @demo point alice.terminal -->

<!-- @demo close code -->

> 🔑 **Key concept: a chain of custody, with nothing bolted on.** Every step
> is signed by the party that took it and names the step before it by its
> hash. Change one byte anywhere and that link breaks, at that step, for
> anyone who checks. To get this from tool calls you would add a central
> log, and a log is only as honest as whoever keeps it. Here there is no
> keeper: the chain is the records themselves, and it runs as deep as the
> work went.

![a central log one keeper writes and can edit, against a chain of steps each signed by its own party and pointing at the one before](sketches/chain-not-log.svg)

Every card alice checked is marked in its own repo now: ✓ where the
signature and the hash held, dashed where alice was never let in.

<!-- @demo point concierge.repo bank.repo hoot.repo cab-17.repo -->

The last thing alice checked was a photo of the curb, signed by the car.

<!-- @demo ask The car signed the photo. Does that prove the car was at alice's curb? -->

> 🔑 **Key concept: a signature proves who; your eyes check what.** The
> signature proves cab-17 took the photo, and the hash proves it is the
> photo the car named. Whether it shows alice's curb, as it looks right now,
> day or night, only alice can say. A car can't sign a photo of alice's
> curb without being at alice's curb, so a picture you can check by eye,
> signed by who took it, is hard to fake.

![the curb photo, signed by cab-17, its hash matching; who took it the signature says, what it shows only alice can say](sketches/curb-photo.svg)

> 💡 **Did you know: privacy and security compose as deep as the builder
> needs.** This lab does the simplest private thing. Each of these is one
> more record, repo or rule over records:
>
> - The car keeps a channel per ride that only alice reads: the positions
>   and the curb photo for alice alone, and no ride linkable to the next.
> - The request names only an area; the exact pickup goes to the winner
>   alone.
> - Each request gets a repo of its own, so a company let back in for the
>   next ride never reads the last one.
> - The award carries a fresh nonce, for the winner only. The car's arrival
>   must repeat it, and alice's peer doesn't even show a photo whose nonce is
>   wrong.
> - For a robotaxi, alice photographs the car as the car photographs the
>   curb, each signing its own and checking the other's, and the door stays
>   locked until alice signs as walking up. A door is a follower like the
>   bank: it acts only on verified records.
> - The bank pays only once alice's own signed "I arrived" lands, not on the
>   company's word.
> - Reputation is the signed history: so many rides, on time, in this city,
>   confirmed by both sides. The car's positions can be forgotten; those
>   facts stay checkable. No reviews needed.

> ⚠️ **Important: what this costs.** Every step is a commit, stored by the
> party that wrote it. Answers arrive as fast as follows carry them, not in
> a stream of tokens. Every party has to speak atproto, and keep its DID's
> host up. A party let into a repo reads all of it: here the repo, not the
> record, is the unit of privacy. And none of it works unless the bank, the
> ride companies and the agent are built this way, which they are not
> today. And a stolen device signs as its owner: keys belong in a hardware
> enclave, as phones and cars already have. This guide shows what could be.

The bank trusted alice's signed allowance because alice once told it to.
A bank could set that up with an OAuth login, if its builders chose. The
next guide does it with nothing secret on the wire: you sign up to an app
in your browser, and your browser holds your key.

<!-- @demo end -->

## MCP, A2A and records, side by side

The show's comparison, as a table. MCP and A2A are both moving quickly;
this is as of late 2026, from their current specifications.

| | MCP | A2A | Signed records (this guide) |
| --- | --- | --- | --- |
| The unit | a tool call to a server | a task handed to another agent | a record in the writer's own repo |
| Who must know whom | the agent knows every server | the agent knows every agent | whoever follows the repo can answer |
| Long-running work | experimental tasks the caller checks back on | updates by a stream, or a push to a webhook the caller exposes | records land when they land; nothing is held open |
| Asking many at once | one call per server | one task per agent | one record, any number of answers |
| Authorization | OAuth 2.1 bearer tokens, for remote servers | standard schemes each agent advertises (OAuth, API keys, ...) | capability records, read through tunnels; no shared secret |
| Who can check it later | the caller, and the server's own logs | the two agents | anyone let in, step by step, back to the start |
| Where it shines | one owner's model using its own tools, fast | agents of different owners trading tasks | work across owners that runs long or moves money |

## Things to try

<!-- @demo terminal -->

- Let Jitney win. Lower Jitney's price below $34 in `company.py`,
  `./lab.sh clean`, and play again. Jitney has no car, so it bids and wins
  but sends none: the concierge's rules and Jitney's business are separate.
- Close the bidding at once. Set `BID_SECONDS` to 0 in `concierge.py`:
  no bid is ever seen in time, so no ride, and no money held.
- Ask for too much. Have alice ask for a ride "under $60" while the
  allowance says $40: the bank refuses, and its refusal says why, in the
  bank's repo where alice can read it.

<!-- @demo /terminal -->

## Where this leads

The next guide is the browser app: you sign up to Geese, a small dating
app, from your own browser, with nothing secret on the wire, and meet
alice, bob and eve there.

## Cleanup

```bash
./lab.sh clean
```
