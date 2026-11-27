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
    return SimpleNamespace(clock=clock, Note=__import__("lexicons").Note, **modules)


def put(seen, did, key, record, at):
    """A record lands in did's repo, and this test's one shared view sees it at `at`."""
    value = datamodel.to_json(dagcbor.decode(record.to_dag_cbor()))
    seen.add(did, f"{record.nsid}/{key}", value, at=at)


def act(seen, did, decide, at, lab):
    """One pass of an agent's loop: write what it decides, if not already written."""
    lab.clock["now"] = at
    wrote = []
    for key, record in decide(seen, at):
        if f"at://{did}/{record.nsid}/{key}" not in seen.records:
            put(seen, did, key, record, at)
            wrote.append((record.nsid.rsplit(".", 1)[1], key))
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
    assert act(seen, jitney, jitney_decide, t, lab) == []  # closed: no late bid

    # the bank checks alice's own allowance, its limit and the balance, then holds
    assert act(seen, bank, lab.bank.decide, t, lab) == [("hold", "3kride")]
    assert act(seen, bank, lab.bank.decide, t, lab) == []  # answered once, either way

    # booked with the winner, the losing bid on the record
    assert act(seen, concierge, lab.concierge.decide, t, lab) == [("booking", "3kride")]
    booking = seen.all(ride.Booking, by=concierge)[0][1]
    assert [ride.author(r) for r in booking.passed] == [jitney]

    # hoot checks the hold in the bank's repo, then sends its car; jitney does nothing
    assert act(seen, jitney, jitney_decide, t, lab) == []
    assert act(seen, hoot, hoot_decide, t, lab) == [("dispatch", "3kride")]

    # the car drives by its own clock: four minutes out, the curb, seventeen minutes in
    wrote = act(seen, cab, cab_decide, t, lab)
    assert wrote == [("position", "3kride-a00")]
    t += lab.cab.MINUTE * (lab.cab.TO_PICKUP + lab.cab.RIDE)
    wrote = act(seen, cab, cab_decide, t, lab)
    assert ("arrival", "3kride") in wrote and ("dropoff", "3kride") in wrote
    assert len([w for w in wrote if w[0] == "position"]) == lab.cab.TO_PICKUP + lab.cab.RIDE + 1

    # the fare, then the bank pays it from the hold and lets the rest go
    assert act(seen, hoot, hoot_decide, t, lab) == [("fare", "3kride")]
    assert act(seen, bank, lab.bank.decide, t, lab) == [("settlement", "3kride")]
    settlement = seen.all(ride.Settlement, by=bank)[0][1]
    assert (str(settlement.paid), str(settlement.released)) == ("$34.00", "$6.00")

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
        authorize=request_ref, payee=hoot, amount=ride.dollars(4000), created_at=stamp(T0)
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
