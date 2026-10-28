"""The package exposes its declared public API."""

import fastatp


def test_public_exports():
    for name in (
        "Peer",
        "Lexicon",
        "DIDServer",
        "DIDResolver",
        "PLCResolver",
        "build_did_document",
        "web_resolver",
        "chain",
        "DIDDocument",
        "exceptions",
    ):
        assert hasattr(fastatp, name), name


def test_exceptions_hierarchy():
    e = fastatp.exceptions
    assert issubclass(e.DIDNotFound, e.FastATPError)
    assert issubclass(e.SignatureInvalid, e.VerificationError)
    assert issubclass(e.LexiconExportError, e.LexiconError)
    assert issubclass(e.NotAuthorized, e.VerificationError)
