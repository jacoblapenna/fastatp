"""LexiconDoc conformance: lexicon-valid/-invalid vectors and the full
com.atproto.* corpus round-trip (json -> Pydantic -> json, faithfully)."""

import json
from pathlib import Path

import pytest

from fastatp.exceptions import LexiconValidationError
from fastatp.lex.schema import LexiconDoc
from tests.conftest import ATPROTO_LEXICONS

CORPUS = sorted(ATPROTO_LEXICONS.rglob("*.json"))


def test_corpus_present():
    # vendored, so a missing or emptied corpus is a failure, not a skip (an
    # empty CORPUS would otherwise parametrize test_corpus_round_trip to zero)
    assert ATPROTO_LEXICONS.is_dir(), f"lexicon corpus missing at {ATPROTO_LEXICONS}"
    assert len(CORPUS) > 50


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.stem + "/" + p.parent.name)
def test_corpus_round_trip(path: Path):
    original = json.loads(path.read_text())
    doc = LexiconDoc.from_json_dict(original)
    assert doc.to_json_dict() == original


def test_lexicon_valid_vectors(interop_dir):
    cases = json.loads((interop_dir / "lexicon" / "lexicon-valid.json").read_text())
    for case in cases:
        doc = LexiconDoc.from_json_dict(case["lexicon"])
        assert doc.to_json_dict() == case["lexicon"], case["name"]


def test_lexicon_invalid_vectors(interop_dir):
    cases = json.loads((interop_dir / "lexicon" / "lexicon-invalid.json").read_text())
    for case in cases:
        with pytest.raises(LexiconValidationError):
            LexiconDoc.from_json_dict(case["lexicon"])


def test_catalog_round_trip(interop_dir):
    for path in sorted((interop_dir / "lexicon" / "catalog").glob("*.json")):
        original = json.loads(path.read_text())
        doc = LexiconDoc.from_json_dict(original)
        assert doc.to_json_dict() == original, path.name
