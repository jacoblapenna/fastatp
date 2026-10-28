"""The board_rendezvous example completes a private exchange.

Drives examples/board_rendezvous.py and asserts these properties: the reply
reaches the requester, is NOT visible to a second board lurker, every hop
verified against the poster's DID, and the channel expires per TTL."""

import importlib.util
from pathlib import Path

_EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "board_rendezvous.py"
_spec = importlib.util.spec_from_file_location("board_rendezvous", _EXAMPLE)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot load example module from {_EXAMPLE}")
board_rendezvous = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(board_rendezvous)


async def test_board_rendezvous_end_to_end():
    r = await board_rendezvous.run_board_rendezvous()

    # the private reply reached the requester
    assert r["reply"] == "all clear"

    # isolation: a second board lurker (who saw every public post) is refused
    # the private reply by the channel doorman
    assert r["snoop_refused"] is True

    # every hop was verified against the poster's own DID (no re-signing relay)
    kinds = [what for what, _who in r["verified_hops"]]
    assert "service verified want ad from" in kinds
    assert "requester verified engagement from" in kinds
    assert "requester read reply from channel" in kinds
    # the engagement was verified as the SERVICE's, the reply as the channel's
    hops = dict(r["verified_hops"])
    assert hops["requester verified engagement from"] == board_rendezvous.SERVICE_DID
    assert hops["requester read reply from channel"] == r["channel_did"]

    # the channel self-cleans on its TTL
    assert r["channel_expired"] is True
    assert r["channel_unroutable_after_ttl"] is True
