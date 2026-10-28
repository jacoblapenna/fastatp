"""FastATP: AT Protocol identity and signed records, transport-agnostic."""

from importlib import metadata as _metadata

from fastatp import exceptions
from fastatp.authz.attestation import Attestation
from fastatp.did.document import DIDDocument, build_did_document
from fastatp.did.plc import PLCResolver
from fastatp.did.resolver import (
    ContinuityResolver,
    DIDResolver,
    WebResolver,
    chain,
    web_resolver,
)
from fastatp.exceptions import (
    CryptoError,
    DIDDocumentInvalid,
    DIDNotFound,
    DIDResolutionTimeout,
    FastATPError,
    LexiconError,
    LexiconExportError,
    LexiconValidationError,
    NotAuthorized,
    RepoError,
    SignatureInvalid,
    SignerMismatch,
    UnknownLexicon,
    VerificationError,
)
from fastatp.identity import Identity
from fastatp.lexicon import Lexicon, parse_record
from fastatp.peer import Peer
from fastatp.peerapp import Envelope, PeerApp, PrivateChannel
from fastatp.repo.repo import Repo, WriteOp
from fastatp.repo.sync import RepoMirror
from fastatp.server.did_server import DIDServer
from fastatp.server.peer_server import PeerServer, RepoHandle

try:
    __version__ = _metadata.version("fastatp")  # pyproject.toml is the single source
except _metadata.PackageNotFoundError:  # pragma: no cover - source tree without an install
    __version__ = "0.0.0"

__all__ = [
    "Attestation",
    "ContinuityResolver",
    "CryptoError",
    "DIDDocument",
    "DIDDocumentInvalid",
    "DIDNotFound",
    "DIDResolutionTimeout",
    "DIDResolver",
    "DIDServer",
    "Envelope",
    "FastATPError",
    "Identity",
    "Lexicon",
    "LexiconError",
    "LexiconExportError",
    "LexiconValidationError",
    "NotAuthorized",
    "PLCResolver",
    "Peer",
    "PeerApp",
    "PeerServer",
    "PrivateChannel",
    "Repo",
    "RepoError",
    "RepoHandle",
    "RepoMirror",
    "SignatureInvalid",
    "SignerMismatch",
    "UnknownLexicon",
    "VerificationError",
    "WebResolver",
    "WriteOp",
    "build_did_document",
    "chain",
    "exceptions",
    "parse_record",
    "web_resolver",
]
