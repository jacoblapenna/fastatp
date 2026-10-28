"""Strict DAG-CBOR codec: rejection branches and canonical-encoding edges.

The decoder's contract is to REJECT non-canonical or malformed bytes — that is
what lets FastATP trust repo bytes produced by other implementations. Each case
below is a byte sequence crafted to trip exactly one guard. The encoder cases
cover the minimal-width integer heads and the compound-type ($link/$bytes, CID,
Bytes, CidLink) paths.
"""

import base64

import pytest

from fastatp.cid import CID
from fastatp.dagcbor import decode, encode
from fastatp.exceptions import DataModelError
from fastatp.lex.types import Bytes, CidLink

# --- decoder rejections -------------------------------------------------------


@pytest.mark.parametrize(
    "data, message",
    [
        (b"", "truncated"),  # _byte() past the end
        (bytes([0x1C]), "invalid additional info"),  # major 0, additional 28
        (bytes([0x61, 0xFF]), "invalid UTF-8 in string"),  # text(1) + bad byte
        (bytes([0xA1, 0x01]), "map keys must be strings"),  # map(1), int key
        (bytes([0xA1, 0x61, 0xFF]), "invalid UTF-8 in map key"),  # map(1), bad key bytes
        (bytes([0xC6]), "tag 6 is not allowed"),  # tag 6, only 42 allowed
        (bytes([0xD8, 0x2A, 0x00]), "tag 42 content must be a byte string"),
        (bytes([0xD8, 0x2A, 0x41, 0x01]), "identity multibase prefix"),  # no 0x00 prefix
        (bytes([0xD8, 0x2A, 0x42, 0x00, 0x00]), "invalid CID in tag 42"),  # prefix ok, CID bad
        (bytes([0xF7]), "simple value 23 is not allowed"),  # major 7, additional 23
    ],
)
def test_decode_rejects_noncanonical(data, message):
    with pytest.raises(DataModelError, match=message):
        decode(data)


def test_decode_rejects_trailing_bytes():
    # one valid item (integer 0) followed by a stray byte
    with pytest.raises(DataModelError, match="trailing bytes"):
        decode(bytes([0x00, 0x00]))


# --- encoder edges ------------------------------------------------------------


def test_encode_rejects_out_of_range_integer():
    with pytest.raises(DataModelError, match="out of CBOR range"):
        encode(2**64)  # one past the unsigned-64 ceiling


@pytest.mark.parametrize(
    "value",
    [0, 23, 24, 255, 256, 65535, 65536, 2**32 - 1, 2**32, 2**64 - 1, -1, -256, -(2**32)],
)
def test_integer_heads_roundtrip_at_every_width(value):
    # exercises each minimal-width head on both the encode and decode side
    assert decode(encode(value)) == value


def test_encode_dollar_link_form_becomes_tag42():
    cid = CID.for_dag_cbor(b"node")
    assert decode(encode({"$link": cid.encode()})) == cid


def test_encode_rejects_invalid_dollar_link():
    with pytest.raises(DataModelError, match=r"invalid \$link CID"):
        encode({"$link": "not-a-cid"})


def test_encode_dollar_bytes_form_roundtrips():
    payload = b"hello bytes"
    encoded = encode({"$bytes": base64.b64encode(payload).decode().rstrip("=")})
    assert decode(encoded) == payload


def test_encode_bytes_and_cidlink_objects():
    assert decode(encode(Bytes(b"raw"))) == b"raw"
    cid = CID.for_dag_cbor(b"linked")
    assert decode(encode(CidLink(cid.encode()))) == cid


def test_encode_rejects_unsupported_type():
    with pytest.raises(DataModelError, match="no DAG-CBOR encoding"):
        encode(object())


def test_encode_rejects_float():
    with pytest.raises(DataModelError, match="floats are not allowed"):
        encode(1.5)
