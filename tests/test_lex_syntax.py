"""Conformance tests for the Lexicon string-format validators.

Driven by the CC-0 interop vectors in reference/atproto-interop-tests/syntax.
Each format has a valid file (every line must pass) and one or more invalid
files (every line must fail).
"""

import pytest

from fastatp.lex.types import (
    validate_at_identifier,
    validate_at_uri,
    validate_cid,
    validate_datetime,
    validate_did,
    validate_handle,
    validate_language,
    validate_nsid,
    validate_record_key,
    validate_tid,
    validate_uri,
)
from tests.conftest import read_vector_lines

VALIDATORS = {
    "did": validate_did,
    "handle": validate_handle,
    "atidentifier": validate_at_identifier,
    "nsid": validate_nsid,
    "aturi": validate_at_uri,
    "cid": validate_cid,
    "tid": validate_tid,
    "recordkey": validate_record_key,
    "uri": validate_uri,
    "language": validate_language,
    "datetime": validate_datetime,
}

# (format, vector file stem, should pass)
CASES = [
    ("did", "did_syntax_valid", True),
    ("did", "did_syntax_invalid", False),
    ("handle", "handle_syntax_valid", True),
    ("handle", "handle_syntax_invalid", False),
    ("atidentifier", "atidentifier_syntax_valid", True),
    ("atidentifier", "atidentifier_syntax_invalid", False),
    ("nsid", "nsid_syntax_valid", True),
    ("nsid", "nsid_syntax_invalid", False),
    ("aturi", "aturi_syntax_valid", True),
    ("aturi", "aturi_syntax_invalid", False),
    ("cid", "cid_syntax_valid", True),
    ("cid", "cid_syntax_invalid", False),
    ("tid", "tid_syntax_valid", True),
    ("tid", "tid_syntax_invalid", False),
    ("recordkey", "recordkey_syntax_valid", True),
    ("recordkey", "recordkey_syntax_invalid", False),
    ("uri", "uri_syntax_valid", True),
    ("uri", "uri_syntax_invalid", False),
    ("language", "language_syntax_valid", True),
    ("language", "language_syntax_invalid", False),
    ("language", "language_parse_invalid", False),
    ("datetime", "datetime_syntax_valid", True),
    ("datetime", "datetime_syntax_invalid", False),
    ("datetime", "datetime_parse_invalid", False),
]


def test_datetime_year_zero_matches_ts_reference():
    """Pin an edge no interop vector covers: bare year-0000 datetimes.

    The TS reference (reference/atproto/packages/syntax/src/datetime.ts)
    defines the valid range as "the years 0000 and 9999" and errors only
    when a datetime "normalizes to a negative year". So bare 0000-...Z is
    VALID, while 0000-01-01T00:00:00+01:00 normalizes to year -1 and is
    invalid (it appears in datetime_syntax_invalid.txt; covered above).
    The MarshalX SDK rejects bare year-0000 — a divergence on this uncovered
    edge; ours matches the reference.
    """
    validator = VALIDATORS["datetime"]
    validator("0000-01-01T00:00:00Z")
    validator("0000-01-01T00:00:00.000Z")
    with pytest.raises(ValueError):
        validator("0000-01-01T00:00:00+01:00")  # normalizes to year -1


@pytest.mark.parametrize(("fmt", "stem", "expect_ok"), CASES)
def test_syntax_vectors(interop_dir, fmt, stem, expect_ok):
    validator = VALIDATORS[fmt]
    lines = read_vector_lines(f"{stem}.txt")
    assert lines, f"no vectors loaded from {stem}.txt"
    failures = []
    for value in lines:
        try:
            validator(value)
            ok = True
        except ValueError:
            ok = False
        if ok != expect_ok:
            failures.append(value)
    assert not failures, f"{fmt}/{stem}: {len(failures)} mismatches: {failures[:10]!r}"
