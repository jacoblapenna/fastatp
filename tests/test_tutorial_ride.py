"""The Hoot lab's agents, run by hand: one ride, step by step, with no network.

Each agent's rules are a plain function of what it has seen and the time
(`decide`), so a whole ride can be played here by passing each agent's
records to the next, and the refusals that matter can be checked one by one.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastatp import dagcbor, datamodel
from fastatp.cid import CID
from fastatp.lex.types import Blob

LAB = Path(__file__).resolve().parent.parent / "tutorials" / "lab"
T0 = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def stamp(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


@pytest.fixture
def lab(monkeypatch):
    """The lab's modules, with peer.py stood in for (it needs WireGuard)."""
    monkeypatch.syspath_prepend(str(LAB))
    clock = {"now": T0}
    monkeypatch.setitem(
        sys.modules, "peer", SimpleNamespace(now=lambda: stamp(clock["now"]), domain="test.lan")
    )
    for name in ("ride", "concierge", "company", "bank", "cab", "lexicons"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    modules = {name: __import__(name) for name in ("ride", "concierge", "company", "bank", "cab")}
    return SimpleNamespace(
        clock=clock, last={}, repo_of={}, Note=__import__("lexicons").Note, **modules
    )


def put(seen, did, key, record, at):
    """A record lands in did's repo, and this test's one shared view sees it at `at`."""
    value = datamodel.to_json(dagcbor.decode(record.to_dag_cbor()))
    seen.add(did, f"{record.nsid}/{key}", value, at=at)


def act(seen, did, decide, at, lab):
    """One pass of an agent's loop: write what it decides, if not already
    written. (Which of its repos each record goes in is checked on its own;
    here every record is simply the agent's.)"""
    lab.clock["now"] = at
    wrote = []
    plan = lab.last[did] = decide(seen, at)
    for repo, key, record in plan.writes:
        assert repo in plan.readers  # nothing goes in a repo whose readers are undecided
        if f"at://{did}/{record.nsid}/{key}" not in seen.records:
            put(seen, did, key, record, at)
            wrote.append((record.nsid.rsplit(".", 1)[1], key))
            lab.repo_of[(did, record.nsid.rsplit(".", 1)[1], key)] = repo
    return wrote


def test_the_stand_in_reader_understands_a_ride_and_nothing_else(lab):
    read = lab.concierge.read
    wanted = read("Get me a ride to the airport, under $40, put it on checking")
    assert wanted["destination"] == "airport" and wanted["account"] == "checking"
    assert wanted["most"].amount == 4000
    assert read("a ride to the airport under forty bucks, on checking")["most"].amount == 4000
    assert read("hello world") is None  # not a ride: nothing happens
    assert read("a ride to the airport, on checking") is None  # no limit: it won't guess one


def test_a_tie_on_price_goes_to_the_nicer_car_and_nothing_over_the_limit_wins(lab):
    ride = lab.ride
    ref = ride.Ref(uri="at://did:web:x.lan/com.example.ride.quote/3k", cid=_cid())

    def quote(cents, car):
        return ride.Quote(
            request=ref, price=ride.dollars(cents), eta_minutes=3, car=car, created_at=stamp(T0)
        )

    a, b = (
        (ref, quote(3400, "hatchback")),
        (ref.model_copy(update={"uri": ref.uri + "x"}), quote(3400, "suv")),
    )
    assert lab.concierge.choose([a, b], ride.dollars(4000))[1].car == "suv"
    assert lab.concierge.choose([(ref, quote(4500, "suv"))], ride.dollars(4000)) is None


def test_one_ride_from_alice_note_to_the_bank_settlement(lab):
    ride, Note = lab.ride, lab.Note
    seen = ride.Seen()
    alice, concierge, bank = ride.ALICE, ride.CONCIERGE, ride.BANK
    hoot, jitney, cab = "did:web:hoot.lan", "did:web:jitney.lan", "did:web:cab-17.lan"
    _, _, hoot_decide = lab.company.company("hoot.lan")
    _, _, jitney_decide = lab.company.company("jitney.lan")
    photo = Blob(ref={"$link": _cid()}, mimeType="image/svg+xml", size=10)
    cab_decide = lab.cab.car(cab, lambda data, mime: photo)
    t = T0

    # alice, by hand: the money first, then the words
    allowance = ride.Allowance(
        bank=bank,
        agent=concierge,
        account="checking",
        per_ride=ride.dollars(4000),
        created_at=stamp(t),
    )
    put(seen, alice, "3kallow", allowance, t)
    note = Note(
        text="Get me a ride to the airport, under $40, put it on checking", created_at=stamp(t)
    )
    put(seen, alice, "3kride", note, t)

    # the concierge understands it, then asks for bids; one ride, one key
    assert act(seen, concierge, lab.concierge.decide, t, lab) == [("intent", "3kride")]
    assert act(seen, concierge, lab.concierge.decide, t, lab) == [("request", "3kride")]
    intent = seen.get(seen.all(ride.Request, by=concierge)[0][1].intent, ride.Intent)
    assert intent.reader.startswith("stand-in")  # the record says what read the sentence

    # while a request is open, every company may read the concierge's rides repo
    readers = lab.last[concierge].readers
    assert readers["rides"] == {alice, hoot, jitney} and readers["desk"] == {alice, bank}
    assert readers["main"] == {alice}  # what it understood alice to say: alice's eyes only

    # both companies bid while it is open; the bank writes its own limits
    t += timedelta(seconds=1)
    assert act(seen, hoot, hoot_decide, t, lab) == [("quote", "3kride")]
    assert act(seen, jitney, jitney_decide, t, lab) == [("quote", "3kride")]
    act(seen, bank, lab.bank.decide, t, lab)

    # before the deadline the concierge waits; after it, it chooses the cheaper bid
    assert act(seen, concierge, lab.concierge.decide, t, lab) == []
    t += timedelta(seconds=lab.concierge.BID_SECONDS)
    assert act(seen, concierge, lab.concierge.decide, t, lab) == [("authorize", "3kride")]
    authorize = seen.all(ride.Authorize, by=concierge)[0][1]
    assert authorize.payee == hoot and authorize.amount.amount == 4000
    assert lab.last[concierge].readers["rides"] == {alice, hoot}  # jitney lost: let go
    assert act(seen, jitney, jitney_decide, t, lab) == []  # closed: no late bid

    # the bank checks alice's own allowance, its limit and the balance, then holds
    assert act(seen, bank, lab.bank.decide, t, lab) == [("hold", "3kride")]
    # a repo of its own for this hold: alice, the agent, and the payee the agent named
    assert lab.last[bank].readers["ride-3kride"] == {alice, concierge, hoot}
    assert jitney not in set().union(*lab.last[bank].readers.values())
    assert act(seen, bank, lab.bank.decide, t, lab) == []  # answered once, either way

    # booked with the winner, the losing bid on the record
    assert act(seen, concierge, lab.concierge.decide, t, lab) == [("booking", "3kride")]
    booking = seen.all(ride.Booking, by=concierge)[0][1]
    assert [seen.who(r) for r in booking.passed] == [jitney]

    # hoot checks the hold in the bank's repo, then sends its car; jitney does nothing
    assert act(seen, jitney, jitney_decide, t, lab) == []
    assert act(seen, hoot, hoot_decide, t, lab) == [("dispatch", "3kride")]

    # the car drives by its own clock: four minutes out, the curb, seventeen minutes in
    wrote = act(seen, cab, cab_decide, t, lab)
    assert wrote == [("position", "3kride-a00")]
    t += lab.cab.MINUTE * (lab.cab.TO_PICKUP + lab.cab.RIDE)
    wrote = act(seen, cab, cab_decide, t, lab)
    wrote += act(seen, cab, cab_decide, t, lab)  # the dropoff names the arrival, once it is written
    assert ("arrival", "3kride") in wrote and ("dropoff", "3kride") in wrote
    dropoff = seen.all(ride.Dropoff, by=cab)[0][1]
    assert seen.get(dropoff.arrival, ride.Arrival).photo == photo  # the chain reaches the curb
    assert len([w for w in wrote if w[0] == "position"]) == lab.cab.TO_PICKUP + lab.cab.RIDE + 1

    # the fare, then the bank pays it from the hold and lets the rest go
    assert act(seen, hoot, hoot_decide, t, lab) == [("fare", "3kride")]
    assert act(seen, bank, lab.bank.decide, t, lab) == [("settlement", "3kride")]
    settlement = seen.all(ride.Settlement, by=bank)[0][1]
    assert (str(settlement.paid), str(settlement.released)) == ("$34.00", "$6.00")
    act(seen, concierge, lab.concierge.decide, t, lab)
    assert lab.last[concierge].readers["rides"] == {alice}  # paid: the winner is let go too

    # each record went in the repo the agent meant, the one with the right readers
    where = lab.repo_of
    assert where[(concierge, "intent", "3kride")] == "main"  # alice's eyes only
    assert where[(concierge, "request", "3kride")] == "rides"
    assert where[(concierge, "authorize", "3kride")] == "desk"  # the bank's, not the companies'
    assert where[(concierge, "booking", "3kride")] == "rides"
    assert where[(bank, "hold", "3kride")] == where[(bank, "settlement", "3kride")] == "ride-3kride"
    assert where[(bank, "payee", "hoot.lan")] == "main"
    assert where[(cab, "arrival", "3kride")] == "ride"

    # every agent, run again over everything, writes nothing more
    for did, decide in [
        (concierge, lab.concierge.decide),
        (bank, lab.bank.decide),
        (hoot, hoot_decide),
        (jitney, jitney_decide),
        (cab, cab_decide),
    ]:
        assert act(seen, did, decide, t, lab) == []


def test_the_bank_refuses_what_alice_did_not_allow(lab):
    ride = lab.ride
    seen = ride.Seen()
    allowance = ride.Allowance(
        bank=ride.BANK,
        agent=ride.CONCIERGE,
        account="checking",
        per_ride=ride.dollars(2000),
        created_at=stamp(T0),
    )
    put(seen, ride.ALICE, "3kallow", allowance, T0)
    allowance_ref = seen.all(ride.Allowance)[0][0]
    quote_ref = ride.Ref(uri="at://did:web:hoot.lan/com.example.ride.quote/3kride", cid=_cid())

    def ask(key, **change):
        fields = dict(
            allowance=allowance_ref,
            quote=quote_ref,
            payee="did:web:hoot.lan",
            amount=ride.dollars(1500),
            account="checking",
            created_at=stamp(T0),
        )
        put(seen, ride.CONCIERGE, key, ride.Authorize(**{**fields, **change}), T0)

    ask("3ka")  # within everything: held
    ask("3kb", amount=ride.dollars(3000))  # over the $20 alice allowed a ride
    ask(
        "3kc", allowance=allowance_ref.model_copy(update={"cid": _cid(b"edited")})
    )  # not alice's record
    ask("3kd", payee="did:web:mallory.lan")  # not a payee the bank pays
    act(seen, ride.BANK, lab.bank.decide, T0, lab)
    reasons = {ride.rkey(r.authorize): r.reason for _, r in seen.all(ride.Refusal, by=ride.BANK)}
    assert [ride.rkey(h.authorize) for _, h in seen.all(ride.Hold, by=ride.BANK)] == ["3ka"]
    assert "over the $20.00" in reasons["3kb"]
    assert "changed" in reasons["3kc"]
    assert "not a payee" in reasons["3kd"]


def test_a_company_sends_no_car_on_a_hold_that_is_not_the_bank_s(lab):
    ride = lab.ride
    seen = ride.Seen()
    hoot = "did:web:hoot.lan"
    _, _, decide = lab.company.company("hoot.lan")
    request_ref = ride.Ref(uri=f"at://{ride.CONCIERGE}/com.example.ride.request/3kride", cid=_cid())
    quote = ride.Quote(
        request=request_ref,
        price=ride.dollars(3400),
        eta_minutes=4,
        car="sedan",
        created_at=stamp(T0),
    )
    put(seen, hoot, "3kride", quote, T0)
    quote_ref = seen.all(ride.Quote, by=hoot)[0][0]
    # a "hold" the concierge wrote itself, in its own repo: nobody's money
    fake = ride.Hold(
        authorize=request_ref,
        owner=ride.ALICE,
        account="checking",
        payee=hoot,
        amount=ride.dollars(4000),
        created_at=stamp(T0),
    )
    put(seen, ride.CONCIERGE, "3kride", fake, T0)
    fake_ref = seen.all(ride.Hold, by=ride.CONCIERGE)[0][0]
    booking = ride.Booking(
        request=request_ref, quote=quote_ref, hold=fake_ref, created_at=stamp(T0)
    )
    put(seen, ride.CONCIERGE, "3kride", booking, T0)
    assert act(seen, hoot, decide, T0 + timedelta(minutes=5), lab) == []


def _cid(data: bytes = b"x") -> str:
    return CID.for_raw(data).encode()


def test_a_record_in_a_party_s_channel_counts_as_that_party_s(lab):
    """A channel is a did:key that names nobody; the party's signed Channel
    announcement is what ties it to the party. Until it is tied, a record
    there is nobody's, and no rule acts on it."""
    ride = lab.ride
    seen = ride.Seen()
    channel = "did:key:zQ3shchannelofhoot"
    quote = ride.Quote(
        request=ride.Ref(uri=f"at://{ride.CONCIERGE}/com.example.ride.request/3k", cid=_cid()),
        price=ride.dollars(3400),
        eta_minutes=4,
        car="sedan",
        created_at=stamp(T0),
    )
    put(seen, channel, "3k", quote, T0)
    assert seen.all(ride.Quote, by=ride.HOOT) == []  # whose is it? nobody has said
    seen.owners[channel] = ride.HOOT  # what hoot's signed Channel record says
    [(ref, _)] = seen.all(ride.Quote, by=ride.HOOT)
    assert seen.who(ref) == ride.HOOT and ref.uri.startswith(f"at://{channel}/")


def test_the_cast_file_seals_the_bids():
    """Hoot and Jitney have no tunnel between them, and Jitney none to alice."""
    sys.path.insert(0, str(LAB))
    try:
        sys.modules["peer"] = SimpleNamespace()
        ride = __import__("ride")
    finally:
        sys.path.pop(0)
        sys.modules.pop("peer", None)
    assert ride.JITNEY not in ride.partners(ride.HOOT)  # no tunnel: neither can even knock
    assert ride.JITNEY not in ride.partners(ride.ALICE)
    assert set(ride.partners(ride.ALICE)) == {ride.CONCIERGE, ride.BANK, ride.HOOT, ride.CAB}


def test_custody_walks_every_strong_ref_once_and_names_what_it_cannot_read(monkeypatch):
    """custody.py knows no record types: a strong ref looks the same in any
    record. It checks each step once, follows only what verified, and tells
    a step alice may not read (sealed) from one that is wrong (changed)."""
    monkeypatch.syspath_prepend(str(LAB))
    monkeypatch.setitem(sys.modules, "peer", SimpleNamespace())
    for name in ("ride", "custody"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    custody = __import__("custody")

    def ref(name):
        return {"uri": f"at://did:key:z{name}/com.example.x.step/{name}", "cid": f"bafy{name}"}

    records = {
        "settle": {"hold": ref("hold"), "fare": ref("fare")},
        "hold": {"authorize": ref("ask")},
        "ask": {"quote": ref("quote"), "passed": [ref("lost")]},
        "fare": {"hold": ref("hold"), "booking": ref("bogus")},  # meets the chain again
        "quote": {},
    }

    def look(r):
        name = r["uri"].rsplit("/", 1)[1]
        if name == "lost":
            return "sealed", None
        if name == "bogus":
            return "changed", None
        return "verified", records[name]

    steps = [(d, r["uri"].rsplit("/", 1)[1], v) for d, r, v, _ in custody.walk(ref("settle"), look)]
    assert steps == [
        (0, "settle", "verified"),
        (1, "hold", "verified"),
        (2, "ask", "verified"),
        (3, "quote", "verified"),
        (3, "lost", "sealed"),
        (1, "fare", "verified"),
        (2, "hold", "again"),
        (2, "bogus", "changed"),
    ]


def test_custody_checks_a_second_hash_for_the_same_address(monkeypatch):
    """A step is a record and its hash. The same address named again with
    another hash is a different claim: it is checked, and found changed."""
    monkeypatch.syspath_prepend(str(LAB))
    monkeypatch.setitem(sys.modules, "peer", SimpleNamespace())
    for name in ("ride", "custody"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    custody = __import__("custody")
    hold = {"uri": "at://did:key:zbank/com.example.bank.hold/3k", "cid": "bafyreal"}
    forged = {**hold, "cid": "bafyforged"}
    records = {"bafyreal": {}}

    def look(r):
        if r["uri"] == hold["uri"]:
            return ("verified", records[r["cid"]]) if r["cid"] in records else ("changed", None)
        return "verified", {"a": hold, "b": forged}

    start = {"uri": "at://did:key:zbank/com.example.bank.settlement/3k", "cid": "bafyset"}
    verdicts = [(r["cid"], v) for _, r, v, _ in custody.walk(start, look)]
    assert verdicts == [
        ("bafyset", "verified"),
        ("bafyreal", "verified"),
        ("bafyforged", "changed"),
    ]


def test_the_bank_waits_for_what_it_has_not_seen_and_never_overspends(lab):
    """An allowance not seen yet is a wait, not a refusal. The account named
    must be the account alice allowed. Two holds in one pass can't both
    spend the same money, and money paid out is gone."""
    ride = lab.ride
    seen = ride.Seen()
    allowance = ride.Allowance(
        bank=ride.BANK,
        agent=ride.CONCIERGE,
        account="checking",
        per_ride=ride.dollars(9000),
        created_at=stamp(T0),
    )
    put(seen, ride.ALICE, "3kallow", allowance, T0)
    allowance_ref = seen.all(ride.Allowance)[0][0]
    quote_ref = ride.Ref(uri="at://did:web:hoot.lan/com.example.ride.quote/3kq", cid=_cid())
    unseen = allowance_ref.model_copy(update={"uri": allowance_ref.uri.replace("3kallow", "3knew")})

    def ask(key, **change):
        fields = dict(
            allowance=allowance_ref,
            quote=quote_ref,
            payee="did:web:hoot.lan",
            amount=ride.dollars(9000),
            account="checking",
            created_at=stamp(T0),
        )
        put(seen, ride.CONCIERGE, key, ride.Authorize(**{**fields, **change}), T0)

    ask("3ka")  # $90 of the $250
    ask("3kb")  # another $90: fits
    ask("3kc")  # a third $90 in the same pass: $270 is more than $250
    ask("3kd", allowance=unseen)  # alice's allowance hasn't reached the bank yet
    ask("3ke", account="savings")  # alice allowed checking
    act(seen, ride.BANK, lab.bank.decide, T0, lab)
    held = [ride.rkey(h.authorize) for _, h in seen.all(ride.Hold, by=ride.BANK)]
    reasons = {ride.rkey(r.authorize): r.reason for _, r in seen.all(ride.Refusal, by=ride.BANK)}
    assert held == ["3ka", "3kb"]
    assert "not enough money" in reasons["3kc"]
    assert "3kd" not in reasons  # waits: not refused, not held
    assert "this account" in reasons["3ke"]


def test_a_refused_payee_is_let_go(lab):
    """No money, no ride: when the bank refuses, the concierge stops letting
    the would-be winner read its rides repo."""
    ride = lab.ride
    seen = ride.Seen()
    note = lab.Note(text="a ride to the airport, under $40, on checking", created_at=stamp(T0))
    put(seen, ride.ALICE, "3kride", note, T0)
    act(seen, ride.CONCIERGE, lab.concierge.decide, T0, lab)  # the intent
    act(seen, ride.CONCIERGE, lab.concierge.decide, T0, lab)  # the request
    request_ref = seen.all(ride.Request, by=ride.CONCIERGE)[0][0]
    authorize = ride.Authorize(
        allowance=ride.Ref(uri="at://did:web:alice.lan/com.example.bank.allowance/3k", cid=_cid()),
        quote=ride.Ref(uri="at://did:web:hoot.lan/com.example.ride.quote/3kride", cid=_cid()),
        payee="did:web:hoot.lan",
        amount=ride.dollars(4000),
        account="checking",
        created_at=stamp(T0),
    )
    put(seen, ride.CONCIERGE, "3kride", authorize, T0)
    late = T0 + timedelta(minutes=1)
    act(seen, ride.CONCIERGE, lab.concierge.decide, late, lab)
    assert "did:web:hoot.lan" in lab.last[ride.CONCIERGE].readers["rides"]  # waiting on the bank
    authorize_ref = seen.all(ride.Authorize, by=ride.CONCIERGE)[0][0]
    refusal = ride.Refusal(authorize=authorize_ref, reason="no", created_at=stamp(late))
    put(seen, ride.BANK, "3kride", refusal, late)
    act(seen, ride.CONCIERGE, lab.concierge.decide, late, lab)
    assert lab.last[ride.CONCIERGE].readers["rides"] == {ride.ALICE}
    assert seen.get(request_ref, ride.Request) is not None  # what was asked stays on record


def test_a_hold_keeps_its_account_whatever_happens_to_the_request(lab):
    """The ledger counts each hold by the account written on the hold itself,
    so rewriting the request it answered can't free the money it holds."""
    ride = lab.ride
    seen = ride.Seen()
    hold = ride.Hold(
        authorize=ride.Ref(
            uri="at://did:web:concierge.lan/com.example.bank.authorize/3k", cid=_cid()
        ),
        owner=ride.ALICE,
        account="checking",
        payee="did:web:hoot.lan",
        amount=ride.dollars(20000),
        created_at=stamp(T0),
    )
    put(seen, ride.BANK, "3k", hold, T0)  # the request it answered is nowhere in sight
    assert lab.bank.spoken_for(seen, (ride.ALICE, "checking")) == 20000
