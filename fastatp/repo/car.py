"""CARv1 (Content Addressable aRchive) encode/decode.

Layout: varint-prefixed DAG-CBOR header ``{"roots": [CID], "version": 1}``,
then blocks as ``varint(len(cid_bytes + data)) || cid_bytes || data``.
"""

from __future__ import annotations

from fastatp import dagcbor
from fastatp.cid import CID
from fastatp.exceptions import DataModelError, RepoError
from fastatp.lex.types import read_varint


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def encode_car(roots: list[CID], blocks: dict[CID, bytes]) -> bytes:
    """Encode a CARv1: header roots + blocks in the given dict order."""
    header = dagcbor.encode({"roots": roots, "version": 1})
    out = bytearray()
    out += _varint(len(header))
    out += header
    for cid, data in blocks.items():
        cid_bytes = bytes(cid)
        out += _varint(len(cid_bytes) + len(data))
        out += cid_bytes
        out += data
    return bytes(out)


def decode_car(data: bytes) -> tuple[list[CID], dict[CID, bytes]]:
    """Decode a CARv1 into (roots, blocks). Block CIDs are verified."""
    try:
        header_len, offset = read_varint(data, 0)
    except ValueError as e:
        raise RepoError(f"invalid CAR: {e}") from None
    try:
        header = dagcbor.decode(data[offset : offset + header_len])
    except DataModelError as e:
        raise RepoError(f"invalid CAR header: {e}") from None
    offset += header_len
    if not isinstance(header, dict) or header.get("version") != 1:
        raise RepoError("invalid CAR header (expected version 1)")
    roots = header.get("roots")
    if not isinstance(roots, list) or not all(isinstance(r, CID) for r in roots):
        raise RepoError("invalid CAR header roots")

    blocks: dict[CID, bytes] = {}
    while offset < len(data):
        try:
            section_len, offset = read_varint(data, offset)
        except ValueError as e:
            raise RepoError(f"invalid CAR section: {e}") from None
        section = data[offset : offset + section_len]
        if len(section) != section_len:
            raise RepoError("truncated CAR section")
        offset += section_len
        try:
            # CIDv1 prefix: version varint, codec varint, then multihash
            _, pos = read_varint(section, 0)
            _, pos = read_varint(section, pos)
            _, pos = read_varint(section, pos)  # hash code
            digest_len, pos = read_varint(section, pos)
            cid_end = pos + digest_len
            cid = CID(section[:cid_end])
        except ValueError as e:
            raise RepoError(f"invalid CID in CAR section: {e}") from None
        block = section[cid_end:]
        expected = CID.create(cid.codec, block)
        if expected != cid:
            raise RepoError(f"CAR block does not hash to its CID ({cid})")
        blocks[cid] = block
    return roots, blocks
