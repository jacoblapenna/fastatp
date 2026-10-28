"""FastATP exception hierarchy.

Everything raised by FastATP derives from :class:`FastATPError`, so callers can
catch one type at the boundary. Resolution, verification, and lexicon errors
have their own branches because they are handled differently in practice
(retry / reject / fix-your-schema).
"""

from __future__ import annotations


class FastATPError(Exception):
    """Base class for all FastATP errors."""


# --- DID resolution ---------------------------------------------------------


class DIDError(FastATPError):
    """Base class for DID-related errors."""


class DIDNotFound(DIDError):
    """No resolver could produce a document for the DID."""


class DIDDocumentInvalid(DIDError):
    """A document was found but is not a valid (atproto-shaped) DID document."""


class DIDResolutionTimeout(DIDError):
    """Resolution did not complete within the configured timeout."""


# --- Verification -----------------------------------------------------------


class VerificationError(FastATPError):
    """Base class for signed-record verification failures."""


class SignatureInvalid(VerificationError):
    """The signature does not verify against the resolved public key."""


class SignerMismatch(VerificationError):
    """The record's issuer does not match ``expected_signer``."""


class NotAuthorized(VerificationError):
    """The ``authorize()`` hook rejected the (issuer, nsid) pair."""


class UnknownLexicon(VerificationError):
    """The record's ``$type`` is not present in the Lexicon registry."""


# --- Lexicon / schema -------------------------------------------------------


class LexiconError(FastATPError):
    """Base class for Lexicon schema errors."""


class LexiconValidationError(LexiconError):
    """Record data does not conform to its Lexicon schema."""


class LexiconExportError(LexiconError):
    """A Pydantic construct has no Lexicon equivalent and cannot be exported."""


# --- Crypto -----------------------------------------------------------------


class CryptoError(FastATPError):
    """Key parsing, encoding, or signing failure."""


# --- Repo / sync (v0.3) -----------------------------------------------------


class RepoError(FastATPError):
    """Base class for repository errors."""


class FirehoseErrorFrame(RepoError):
    """The producer sent an event-stream error frame (``op: -1``).

    Unlike a malformed frame (skip it) or a transport failure (reconnect),
    an error frame is a deliberate protocol signal — ``FutureCursor``,
    ``ConsumerTooSlow`` — that the consumer should recover from explicitly
    (checkpoint refetch / cursor reset), never silently drop. The three-way
    triage is a production lesson (see docs/COMPARISON-ATPROTO-SDK.md).
    """

    def __init__(self, error: str, message: str = "") -> None:
        super().__init__(f"firehose error frame: {error}: {message}")
        self.error = error


class CommitGap(RepoError):
    """A verified commit does not extend this consumer's local state.

    The event itself is internally consistent — the mismatch is between its
    ``since``/``prevData`` and what this mirror holds, i.e. events were
    missed. The remedy is state recovery (checkpoint refetch), not skipping:
    every subsequent commit would fail the same way.
    """


class DataModelError(FastATPError):
    """Data violates the atproto data model (floats, bad compound types, ...)."""


class IdentityError(FastATPError):
    """Identity bootstrap / durability-contract failure: a durable
    name (``domain=``) without durable state, a corrupt state_dir, etc."""


# --- Authorization / enforcement (v0.4) --------------------------------------


class AuthzError(FastATPError):
    """Authorization-plane failure (granting, revoking, capability state)."""


class EnforcementError(AuthzError):
    """Enforcement-plane failure (WireGuard interface, keys, service entries)."""
