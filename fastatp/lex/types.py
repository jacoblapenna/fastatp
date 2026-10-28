"""The Lexicon type palette.

String-format validators ported from the reference @atproto/syntax package
(the interop vectors in reference/atproto-interop-tests are the oracle),
plus the compound data-model types (CidLink, Bytes, Blob) and constraint
helpers, all packaged as Pydantic-compatible types so users never hand-roll
format or constraint validators.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

import regex as _regex
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)
from pydantic_core import core_schema

__all__ = [
    "AtIdentifier",
    "AtUri",
    "Blob",
    "Bytes",
    "Cid",
    "CidLink",
    "Datetime",
    "Did",
    "Handle",
    "KnownValues",
    "Language",
    "LexFormat",
    "MaxGraphemes",
    "MaxLength",
    "MinGraphemes",
    "MinLength",
    "Nsid",
    "RecordKey",
    "Tid",
    "Uri",
    "validate_at_identifier",
    "validate_at_uri",
    "validate_cid",
    "validate_datetime",
    "validate_did",
    "validate_handle",
    "validate_language",
    "validate_nsid",
    "validate_record_key",
    "validate_tid",
    "validate_uri",
]


# ---------------------------------------------------------------------------
# String-format validators. Each returns the (unmodified) value or raises
# ValueError, so they plug directly into pydantic's AfterValidator.
# ---------------------------------------------------------------------------

_DID_RE = re.compile(r"^did:[a-z]+:[a-zA-Z0-9._:%-]*[a-zA-Z0-9._-]$")
_HANDLE_RE = re.compile(
    r"^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$"
)
_TID_RE = re.compile(r"^[234567abcdefghij][234567abcdefghijklmnopqrstuvwxyz]{12}$")
_RECORD_KEY_RE = re.compile(r"^[a-zA-Z0-9_~.:-]{1,512}$")
_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:(?://)?[^\s/][^\s]*$")
_NSID_CHARSET_RE = re.compile(r"^[a-zA-Z0-9.-]*$")


def validate_did(v: str) -> str:
    if len(v) > 2048:
        raise ValueError("DID is too long (2048 chars max)")
    if not _DID_RE.match(v):
        raise ValueError("DID syntax not valid")
    return v


def validate_handle(v: str) -> str:
    if len(v) > 253:
        raise ValueError("handle is too long (253 chars max)")
    if not _HANDLE_RE.match(v):
        raise ValueError("handle syntax not valid")
    return v


def validate_at_identifier(v: str) -> str:
    try:
        return validate_did(v)
    except ValueError:
        pass
    try:
        return validate_handle(v)
    except ValueError:
        raise ValueError("not a valid DID or handle") from None


def validate_nsid(v: str) -> str:
    if len(v) > 253 + 1 + 63:
        raise ValueError("NSID is too long (317 chars max)")
    if not _NSID_CHARSET_RE.match(v):
        raise ValueError("disallowed characters in NSID")
    segments = v.split(".")
    if len(segments) < 3:
        raise ValueError("NSID needs at least three parts")
    for seg in segments:
        if not seg:
            raise ValueError("NSID parts can not be empty")
        if len(seg) > 63:
            raise ValueError("NSID part too long (max 63 chars)")
        if seg[0] == "-" or seg[-1] == "-":
            raise ValueError("NSID parts can not start or end with hyphen")
    if segments[0][0].isdigit():
        raise ValueError("NSID first part may not start with a digit")
    name = segments[-1]
    if name[0].isdigit() or "-" in name:
        raise ValueError("NSID name must be letters/digits with no leading digit")
    return v


def validate_tid(v: str) -> str:
    if len(v) != 13 or not _TID_RE.match(v):
        raise ValueError("TID syntax not valid")
    return v


def validate_record_key(v: str) -> str:
    if v in (".", "..") or not _RECORD_KEY_RE.match(v):
        raise ValueError("record key syntax not valid")
    return v


def validate_uri(v: str) -> str:
    if len(v) > 8192:
        raise ValueError("URI is too long (8192 chars max)")
    if not _URI_RE.match(v):
        raise ValueError("URI syntax not valid")
    return v


# --- datetime ---------------------------------------------------------------

_DATETIME_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[012])-(?P<day>[0-2]\d|3[01])"
    r"T(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d):(?P<second>[0-5]\d|60)"
    r"(?P<frac>\.\d+)?"
    r"(?P<offset>Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    return 30 if month in (4, 6, 9, 11) else 31


def _days_from_civil(y: int, m: int, d: int) -> int:
    """Days since 0000-03-01 in the proleptic Gregorian calendar.

    Howard Hinnant's algorithm; exact for all years >= 0, which Python's
    datetime (years >= 1) cannot represent.
    """
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe


_DT_LOWER_BOUND = 0  # 0000-01-01T00:00:00Z
_DT_UPPER_BOUND = None  # computed lazily: 10000-01-01T00:00:00Z


def validate_datetime(v: str) -> str:
    """Strict atproto Lexicon datetime: RFC 3339 ∩ ISO 8601, timezone required."""
    global _DT_UPPER_BOUND
    if len(v) > 64:
        raise ValueError("datetime is too long (64 chars max)")
    if v.endswith("-00:00"):
        raise ValueError('datetime can not use "-00:00" for UTC timezone')
    m = _DATETIME_RE.match(v)
    if not m:
        raise ValueError("datetime syntax not valid")
    year, month, day = int(m["year"]), int(m["month"]), int(m["day"])
    hour, minute = int(m["hour"]), int(m["minute"])
    second = int(m["second"])
    if second == 60:
        # RFC 3339 grammar allows :60, but the network reference (datetime.ts)
        # rejects leap seconds at its semantic Date parse; agree with the wire.
        raise ValueError("datetime leap second not allowed")
    if day == 0 or day > _days_in_month(year, month):
        raise ValueError("datetime day out of range for month")
    offset = 0
    if m["offset"] != "Z":
        sign = 1 if m["offset"][0] == "+" else -1
        offset = sign * (int(m["offset"][1:3]) * 3600 + int(m["offset"][4:6]) * 60)
    epoch_days = _days_from_civil(year, month, day) - _days_from_civil(0, 1, 1)
    total = epoch_days * 86400 + hour * 3600 + minute * 60 + second - offset
    if _DT_UPPER_BOUND is None:
        _DT_UPPER_BOUND = (_days_from_civil(10000, 1, 1) - _days_from_civil(0, 1, 1)) * 86400
    if total < _DT_LOWER_BOUND:
        raise ValueError("datetime normalized to a negative time")
    if total >= _DT_UPPER_BOUND:
        raise ValueError("datetime year is too far in the future")
    return v


def format_datetime(v: datetime) -> str:
    """Serialize an aware datetime to a conforming atproto datetime string."""
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return v.isoformat().replace("+00:00", "Z")


# --- language (BCP 47) -------------------------------------------------------

_BCP47_RE = re.compile(
    r"^((?P<grandfathered>(en-GB-oed|i-ami|i-bnn|i-default|i-enochian|i-hak|"
    r"i-klingon|i-lux|i-mingo|i-navajo|i-pwn|i-tao|i-tay|i-tsu|sgn-BE-FR|"
    r"sgn-BE-NL|sgn-CH-DE)|(art-lojban|cel-gaulish|no-bok|no-nyn|zh-guoyu|"
    r"zh-hakka|zh-min|zh-min-nan|zh-xiang))"
    # primary language subtag: conventional lowercase, 2-3 letters (optionally
    # with extlang) or 5-8 letters; 4-letter subtags are reserved and rejected
    # (the interop vectors are stricter than plain RFC 5646 well-formedness)
    r"|((?P<language>([a-z]{2,3}(-(?P<extlang>[a-z]{3}(-[a-z]{3}){0,2}))?)"
    r"|[a-z]{5,8})"
    r"(-(?P<script>[A-Za-z]{4}))?"
    r"(-(?P<region>[A-Za-z]{2}|[0-9]{3}))?"
    r"(-(?P<variant>[A-Za-z0-9]{5,8}|[0-9][A-Za-z0-9]{3}))*"
    r"(-(?P<extension>[0-9A-WY-Za-wy-z](-[A-Za-z0-9]{2,8})+))*"
    r"(-(?P<privateUseA>[xX](-[A-Za-z0-9]{1,8})+))?)"
    r"|(?P<privateUseB>[xX](-[A-Za-z0-9]{1,8})+))$"
)


def _has_duplicate_variant_or_singleton(v: str, groups: dict[str, Any]) -> bool:
    if groups.get("grandfathered") or groups.get("privateUseB"):
        return False
    subtags = v.split("-")
    i = 1
    if len(subtags[0]) in (2, 3):
        count = 0
        while count < 3 and i < len(subtags) and re.fullmatch(r"[A-Za-z]{3}", subtags[i]):
            i += 1
            count += 1
    if i < len(subtags) and re.fullmatch(r"[A-Za-z]{4}", subtags[i]):
        i += 1
    if i < len(subtags) and re.fullmatch(r"[A-Za-z]{2}|[0-9]{3}", subtags[i]):
        i += 1
    seen_variants: set[str] = set()
    while i < len(subtags) and re.fullmatch(r"[A-Za-z0-9]{5,8}|[0-9][A-Za-z0-9]{3}", subtags[i]):
        key = subtags[i].lower()
        if key in seen_variants:
            return True
        seen_variants.add(key)
        i += 1
    seen_singletons: set[str] = set()
    while i < len(subtags) and re.fullmatch(r"[0-9A-WYZa-wyz]", subtags[i]):
        singleton = subtags[i].lower()
        if singleton in seen_singletons:
            return True
        seen_singletons.add(singleton)
        i += 1
        while i < len(subtags) and re.fullmatch(r"[A-Za-z0-9]{2,8}", subtags[i]):
            i += 1
    return False


def validate_language(v: str) -> str:
    m = _BCP47_RE.match(v)
    if not m:
        raise ValueError("language tag syntax not valid (BCP 47)")
    if _has_duplicate_variant_or_singleton(v, m.groupdict()):
        raise ValueError("language tag has duplicate variant or singleton subtags")
    return v


# --- CID ---------------------------------------------------------------------

_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {c: i for i, c in enumerate(_BASE58_ALPHABET)}


def base58btc_decode(s: str) -> bytes:
    num = 0
    for ch in s:
        try:
            num = num * 58 + _BASE58_INDEX[ch]
        except KeyError:
            raise ValueError(f"invalid base58 character {ch!r}") from None
    n_leading = len(s) - len(s.lstrip("1"))
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    return b"\x00" * n_leading + body


def base58btc_encode(data: bytes) -> str:
    num = int.from_bytes(data, "big")
    out = []
    while num:
        num, rem = divmod(num, 58)
        out.append(_BASE58_ALPHABET[rem])
    n_leading = len(data) - len(data.lstrip(b"\x00"))
    return "1" * n_leading + "".join(reversed(out))


def _decode_bits(s: str, alphabet: str, bits_per_char: int) -> bytes:
    """RFC 4648 style no-padding decode, strict about trailing bits."""
    index = {c: i for i, c in enumerate(alphabet)}
    buffer = 0
    bits = 0
    out = bytearray()
    for ch in s:
        if ch not in index:
            raise ValueError(f"invalid character {ch!r}")
        buffer = (buffer << bits_per_char) | index[ch]
        bits += bits_per_char
        if bits >= 8:
            bits -= 8
            out.append((buffer >> bits) & 0xFF)
    if bits >= bits_per_char or (buffer << (8 - bits)) & 0xFF:
        raise ValueError("unexpected end of data")
    return bytes(out)


_BASE32_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_BASE64URL_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def multibase_decode(s: str) -> bytes:
    """Decode a multibase string (the bases that appear in atproto data)."""
    if not s:
        raise ValueError("empty multibase string")
    prefix, rest = s[0], s[1:]
    if prefix == "z":
        return base58btc_decode(rest)
    if prefix == "b":
        return _decode_bits(rest, _BASE32_ALPHABET, 5)
    if prefix == "m":
        return _decode_bits(rest, _BASE64_ALPHABET, 6)
    if prefix == "u":
        return _decode_bits(rest, _BASE64URL_ALPHABET, 6)
    if prefix == "f":
        try:
            return binascii.unhexlify(rest)
        except (binascii.Error, ValueError) as e:
            raise ValueError(f"invalid base16: {e}") from None
    raise ValueError(f"unsupported multibase prefix {prefix!r}")


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Read an unsigned LEB128 varint; returns (value, new offset)."""
    value = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("truncated varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def validate_cid_bytes(data: bytes) -> bytes:
    """Validate binary CIDv1 structure: version, codec, well-formed multihash."""
    version, off = read_varint(data, 0)
    if version != 1:
        raise ValueError(f"unsupported CID version {version}")
    _codec, off = read_varint(data, off)
    _hash_code, off = read_varint(data, off)
    hash_size, off = read_varint(data, off)
    if len(data) - off != hash_size:
        raise ValueError("CID multihash length mismatch")
    return data


_CID_RE = re.compile(r"^[a-zA-Z0-9+=]{8,256}$")


def validate_cid(v: str) -> str:
    """Loose syntax check for CID strings (per the interop vectors).

    Charset/length only, plus rejecting CIDv0 ("Qm..."); it deliberately does
    not multibase-decode. Strict binary validation lives in
    :func:`validate_cid_bytes` and is used wherever CIDs are decoded.
    """
    if v.startswith("Qm"):
        raise ValueError("CIDv0 is not supported")
    if not _CID_RE.match(v):
        raise ValueError("CID syntax not valid")
    return v


# --- AT-URI -------------------------------------------------------------------

_ATURI_BAD_CHAR_RE = re.compile(r"[^a-zA-Z0-9._~:@!$&'()*+,;=%/\\\[\]#?-]")
_ATURI_RE = re.compile(
    r"^(?P<uri>at://(?P<authority>[^/?#\s]+)"
    r"(?:/(?P<collection>[^/?#\s]+)(?:/(?P<rkey>[^/?#\s]+))?)?"
    r"(?P<trailing_slash>/)?)"
    r"(?:\?(?P<query>[^#\s]*))?"
    r"(?:#(?P<hash>[^\s]*))?$"
)
_JSON_POINTER_RE = re.compile(r"^/[a-zA-Z0-9._~:@!$&')(*+,;=%\[\]/-]*$")
_PERCENT_RE = re.compile(r"%(..)?")


def _check_percent_encoding(v: str) -> None:
    """Mirror decodeURIComponent(): %XX pairs must be hex and decode to UTF-8."""
    out = bytearray()
    i = 0
    while i < len(v):
        ch = v[i]
        if ch == "%":
            hexpair = v[i + 1 : i + 3]
            if len(hexpair) != 2 or not re.fullmatch(r"[0-9a-fA-F]{2}", hexpair):
                raise ValueError("invalid percent-encoding")
            out.append(int(hexpair, 16))
            i += 3
        else:
            out.extend(ch.encode("utf-8"))
            i += 1
    try:
        out.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("invalid percent-encoding") from None


def validate_at_uri(v: str, strict: bool = True) -> str:
    if len(v) > 8192:
        raise ValueError("AT-URI exceeds maximum length")
    if _ATURI_BAD_CHAR_RE.search(v):
        raise ValueError("disallowed characters in AT-URI")
    m = _ATURI_RE.match(v)
    if not m:
        raise ValueError("AT-URI does not match expected format")
    validate_at_identifier(m["authority"])
    if m["collection"] is not None:
        validate_nsid(m["collection"])
    if m["hash"] is not None:
        if not _JSON_POINTER_RE.match(m["hash"]):
            raise ValueError("AT-URI has invalid fragment")
        _check_percent_encoding(m["hash"])
    if strict:
        if m["trailing_slash"] is not None:
            raise ValueError("AT-URI can not have a trailing slash")
        if m["query"] is not None:
            raise ValueError("AT-URI query part is not allowed")
        if m["rkey"] is not None:
            validate_record_key(m["rkey"])
    return v


# ---------------------------------------------------------------------------
# Pydantic palette types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LexFormat:
    """Annotation marker: identifies the Lexicon string format for export."""

    format: str


def _coerce_datetime(v: Any) -> Any:
    if isinstance(v, datetime):
        return format_datetime(v)
    return v


Did = Annotated[str, AfterValidator(validate_did), LexFormat("did")]
Handle = Annotated[str, AfterValidator(validate_handle), LexFormat("handle")]
AtIdentifier = Annotated[str, AfterValidator(validate_at_identifier), LexFormat("at-identifier")]
Nsid = Annotated[str, AfterValidator(validate_nsid), LexFormat("nsid")]
AtUri = Annotated[str, AfterValidator(validate_at_uri), LexFormat("at-uri")]
Cid = Annotated[str, AfterValidator(validate_cid), LexFormat("cid")]
Tid = Annotated[str, AfterValidator(validate_tid), LexFormat("tid")]
RecordKey = Annotated[str, AfterValidator(validate_record_key), LexFormat("record-key")]
Uri = Annotated[str, AfterValidator(validate_uri), LexFormat("uri")]
Language = Annotated[str, AfterValidator(validate_language), LexFormat("language")]
Datetime = Annotated[
    str,
    BeforeValidator(_coerce_datetime),
    AfterValidator(validate_datetime),
    LexFormat("datetime"),
]


# --- constraint helpers -------------------------------------------------------


def _grapheme_len(s: str) -> int:
    return len(_regex.findall(r"\X", s))


@dataclass(frozen=True)
class _StringConstraint:
    """Base for string constraint annotations usable in Annotated[...]."""

    limit: int

    def _check(self, v: str) -> str:
        raise NotImplementedError

    def __get_pydantic_core_schema__(
        self, source_type: Any, handler: Callable[[Any], core_schema.CoreSchema]
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(self._check, handler(source_type))


@dataclass(frozen=True)
class MaxLength(_StringConstraint):
    """Lexicon maxLength: UTF-8 byte count, not Python len()."""

    def _check(self, v: str) -> str:
        if len(v.encode("utf-8")) > self.limit:
            raise ValueError(f"string exceeds {self.limit} UTF-8 bytes")
        return v


@dataclass(frozen=True)
class MinLength(_StringConstraint):
    """Lexicon minLength: UTF-8 byte count, not Python len()."""

    def _check(self, v: str) -> str:
        if len(v.encode("utf-8")) < self.limit:
            raise ValueError(f"string is shorter than {self.limit} UTF-8 bytes")
        return v


@dataclass(frozen=True)
class MaxGraphemes(_StringConstraint):
    """Lexicon maxGraphemes: grapheme cluster count."""

    def _check(self, v: str) -> str:
        if _grapheme_len(v) > self.limit:
            raise ValueError(f"string exceeds {self.limit} graphemes")
        return v


@dataclass(frozen=True)
class MinGraphemes(_StringConstraint):
    """Lexicon minGraphemes: grapheme cluster count."""

    def _check(self, v: str) -> str:
        if _grapheme_len(v) < self.limit:
            raise ValueError(f"string is shorter than {self.limit} graphemes")
        return v


@dataclass(frozen=True)
class KnownValues:
    """Lexicon knownValues: advisory open enumeration (never fails validation)."""

    values: tuple[str, ...]

    def __init__(self, values: Any) -> None:
        object.__setattr__(self, "values", tuple(values))


# --- compound data-model types --------------------------------------------------


class CidLink:
    """A link to content-addressed data: ``{"$link": "<cid>"}`` in JSON."""

    __slots__ = ("ref",)

    def __init__(self, ref: str) -> None:
        self.ref = validate_cid(ref)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CidLink) and other.ref == self.ref

    def __hash__(self) -> int:
        return hash((CidLink, self.ref))

    def __repr__(self) -> str:
        return f"CidLink({self.ref!r})"

    @classmethod
    def _validate(cls, v: Any) -> CidLink:
        if isinstance(v, CidLink):
            return v
        if isinstance(v, str):
            return cls(v)
        if isinstance(v, dict) and set(v.keys()) == {"$link"} and isinstance(v["$link"], str):
            return cls(v["$link"])
        raise ValueError("cid-link must be a CID string or {'$link': <cid>}")

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls._validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda v: {"$link": v.ref}
            ),
        )


class Bytes:
    """Raw bytes: ``{"$bytes": "<base64, no padding>"}`` in JSON."""

    __slots__ = ("data",)

    def __init__(self, data: bytes) -> None:
        self.data = bytes(data)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Bytes) and other.data == self.data

    def __hash__(self) -> int:
        return hash((Bytes, self.data))

    def __repr__(self) -> str:
        return f"Bytes({self.data!r})"

    def encode(self) -> str:
        return base64.b64encode(self.data).decode("ascii").rstrip("=")

    @classmethod
    def _validate(cls, v: Any) -> Bytes:
        if isinstance(v, Bytes):
            return v
        if isinstance(v, (bytes, bytearray)):
            return cls(bytes(v))
        if isinstance(v, dict) and set(v.keys()) == {"$bytes"} and isinstance(v["$bytes"], str):
            raw = v["$bytes"]
            try:
                return cls(base64.b64decode(raw + "=" * (-len(raw) % 4), validate=True))
            except (binascii.Error, ValueError):
                raise ValueError("invalid base64 in $bytes") from None
        raise ValueError("bytes must be raw bytes or {'$bytes': <base64>}")

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls._validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda v: {"$bytes": v.encode()}
            ),
        )


class Blob(BaseModel):
    """The atproto blob object (typed form).

    The deprecated legacy form ``{"cid": ..., "mimeType": ...}`` is accepted on
    input and normalized to the typed form on output.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    type: str = Field(default="blob", alias="$type", frozen=True)
    ref: CidLink
    mime_type: str = Field(alias="mimeType")
    size: int

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy(cls, v: Any) -> Any:
        if isinstance(v, dict) and "$type" not in v and "cid" in v and "mimeType" in v:
            # deprecated pre-typed blob form; size is unknown in that format
            return {
                "$type": "blob",
                "ref": {"$link": v["cid"]},
                "mimeType": v["mimeType"],
                "size": -1,
            }
        return v

    def model_post_init(self, __context: Any) -> None:
        if self.type != "blob":
            raise ValueError("blob $type must be 'blob'")
