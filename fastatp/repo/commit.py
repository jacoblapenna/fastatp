"""Signed repository commits.

A commit is the signed root of the repo: ``{did, version: 3, data: <MST root
CID>, rev: <TID>, prev: null}`` plus ``sig`` (compact low-S ECDSA over the
canonical DAG-CBOR of the unsigned object). Present-state repos never point
``prev`` at an older commit — there is no walkable history chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastatp import dagcbor
from fastatp.cid import CID
from fastatp.crypto import PrivateKey, PublicKey, sign_message, verify_signature
from fastatp.exceptions import RepoError
from fastatp.lex.types import validate_did, validate_tid

COMMIT_VERSION = 3


@dataclass(frozen=True)
class Commit:
    did: str
    data: CID
    rev: str
    sig: bytes
    version: int = COMMIT_VERSION
    prev: CID | None = None

    def unsigned_map(self) -> dict[str, Any]:
        return {
            "did": self.did,
            "rev": self.rev,
            "data": self.data,
            "prev": self.prev,
            "version": self.version,
        }

    def to_map(self) -> dict[str, Any]:
        return {**self.unsigned_map(), "sig": self.sig}

    def to_dag_cbor(self) -> bytes:
        return dagcbor.encode(self.to_map())

    def cid(self) -> CID:
        return CID.for_dag_cbor(self.to_dag_cbor())

    def verify(self, public_key: PublicKey) -> bool:
        return verify_signature(public_key, dagcbor.encode(self.unsigned_map()), self.sig)

    @classmethod
    def create(cls, did: str, data: CID, rev: str, signing_key: PrivateKey) -> Commit:
        validate_did(did)
        validate_tid(rev)
        unsigned = {
            "did": did,
            "rev": rev,
            "data": data,
            "prev": None,
            "version": COMMIT_VERSION,
        }
        sig = sign_message(signing_key, dagcbor.encode(unsigned))
        return cls(did=did, data=data, rev=rev, sig=sig)

    @classmethod
    def from_map(cls, obj: Any) -> Commit:
        if not isinstance(obj, dict):
            raise RepoError("commit must be a map")
        try:
            if obj["version"] != COMMIT_VERSION:
                raise RepoError(f"unsupported commit version {obj['version']!r}")
            did, data, rev, sig = obj["did"], obj["data"], obj["rev"], obj["sig"]
            prev = obj.get("prev")
        except KeyError as e:
            raise RepoError(f"commit is missing field {e}") from None
        # crafted commit blocks must fail here, not as AttributeError later
        if (
            not isinstance(did, str)
            or not isinstance(data, CID)
            or not isinstance(rev, str)
            or not isinstance(sig, bytes)
            or not (prev is None or isinstance(prev, CID))
        ):
            raise RepoError("commit field has the wrong type")
        return cls(did=did, data=data, rev=rev, sig=sig, prev=prev)

    @classmethod
    def from_dag_cbor(cls, data: bytes) -> Commit:
        return cls.from_map(dagcbor.decode(data))
