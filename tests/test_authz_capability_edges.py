"""Capability selection edges: fail-closed expiry, skips, and supersession.

These guard the authorization plane's "newest live capability wins" contract and
its fail-closed behavior against malformed or half-deleted records.
"""

from fastatp.authz.capability import Capability, live_capabilities, rkey_sort_key
from fastatp.tid import encode_tid

ALICE = "did:web:alice.example"
BOB = "did:web:bob.example"


class _StubRepo:
    """Minimal repo_like: list_records + get_record over an in-memory dict."""

    def __init__(self, records: dict[str, dict | None]) -> None:
        self._records = records

    def list_records(self, collection: str):
        return [(rkey, None) for rkey in self._records]

    def get_record(self, collection: str, rkey: str):
        return self._records.get(rkey)


def _cap(subject: str, expires_at: str | None = None) -> dict:
    return Capability(subject=subject, actions=["subscribe"], expires_at=expires_at).to_json_dict()


def test_unparseable_expiry_fails_closed():
    # a persisted record whose expiry can't be parsed is treated as expired
    cap = Capability.model_construct(
        subject=ALICE, actions=["subscribe"], expires_at="garbage-not-a-datetime"
    )
    assert cap.is_expired() is True


def test_missing_record_is_skipped():
    # listed but get_record returns None (deleted between list and read)
    repo = _StubRepo({encode_tid(1000, 0): None})
    assert live_capabilities(repo) == {}


def test_expired_capability_is_skipped():
    repo = _StubRepo({encode_tid(1000, 0): _cap(ALICE, expires_at="2020-01-01T00:00:00Z")})
    assert live_capabilities(repo) == {}


def test_newest_rkey_supersedes_for_same_subject():
    older = encode_tid(1_000, 0)
    newer = encode_tid(2_000, 0)
    repo = _StubRepo({older: _cap(ALICE), newer: _cap(ALICE), encode_tid(1_500, 0): _cap(BOB)})
    live = live_capabilities(repo)
    assert set(live) == {ALICE, BOB}
    assert live[ALICE][0] == newer  # newest TID wins the supersession


def test_rkey_sort_key_tid_and_non_tid():
    tid = encode_tid(5_000, 0)
    assert rkey_sort_key(tid) == (5_000, tid)
    # a non-TID rkey sorts before every real TID (fails safe, never "newest")
    assert rkey_sort_key("literal-rkey") == (0, "literal-rkey")
