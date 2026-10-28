"""CIDv1 decode rejections, multi-byte varints, and accessor contracts."""

import pytest

from fastatp.cid import CID, DAG_CBOR_CODEC


def test_decode_rejects_non_v1():
    # version varint 0 (not CIDv1), then a well-formed dag-cbor sha-256 tail
    raw = b"\x00\x71\x12\x20" + b"\x00" * 32
    with pytest.raises(ValueError, match="only CIDv1 is supported"):
        CID(raw)


def test_decode_rejects_multihash_length_mismatch():
    # size varint says 32, only 10 digest bytes follow
    raw = b"\x01\x71\x12\x20" + b"\x00" * 10
    with pytest.raises(ValueError, match="multihash length mismatch"):
        CID(raw)


def test_multi_byte_codec_varint_roundtrips():
    # codec 0x0200 needs a two-byte varint (exercises the continuation branch)
    cid = CID.create(0x0200, b"data")
    assert cid.codec == 0x0200
    assert CID.decode(cid.encode()) == cid


def test_immutable():
    cid = CID.for_dag_cbor(b"x")
    with pytest.raises(AttributeError, match="immutable"):
        cid._raw = b"y"


def test_decode_is_idempotent_on_a_cid():
    cid = CID.for_dag_cbor(b"x")
    assert CID.decode(cid) is cid


def test_accessors():
    cid = CID.for_dag_cbor(b"payload")
    assert cid.version == 1
    assert cid.codec == DAG_CBOR_CODEC
    # multihash = hash-code (0x12) + size (0x20) + 32-byte digest
    assert cid.multihash[:2] == b"\x12\x20"
    assert len(cid.multihash) == 34
    assert repr(cid).startswith("CID('b")
