"""LexiconDoc conformance: lexicon-valid/-invalid vectors and the full
com.atproto.* corpus round-trip (json -> Pydantic -> json, faithfully)."""

import json
from pathlib import Path

import pytest

from fastatp.exceptions import LexiconValidationError
from fastatp.lex.schema import LexiconDoc
from tests.conftest import REPO_ROOT

_ATPROTO_LEXICONS = REPO_ROOT / "reference" / "atproto" / "lexicons" / "com" / "atproto"
CORPUS = sorted(_ATPROTO_LEXICONS.rglob("*.json")) if _ATPROTO_LEXICONS.is_dir() else []


def test_corpus_present():
    if not _ATPROTO_LEXICONS.is_dir():
        pytest.skip("reference/atproto/lexicons not present")
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
