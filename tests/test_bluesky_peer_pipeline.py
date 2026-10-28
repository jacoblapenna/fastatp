"""The bluesky_peer composition pipeline, green in CI (no network).

curator (gated slug DID) -> poster (capability, persona pen in-process) ->
public firehose -> verifying consumer; image blob serves; segmentation
holds; the wire audit finds no secret because none exists.
"""

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "examples"))

from bluesky_peer.pipeline.demo import DEMO_PNG, SELECTIONS, run_demo  # noqa: E402


def test_pipeline_end_to_end(tmp_path):
    summary = asyncio.run(run_demo("alice.pipeline.example", str(tmp_path / "state")))

    # the persona is the pretty subdomain DID; the curator is a slug twin of
    # the SAME domain, hosted gated on the same process
    assert summary["persona"] == "did:web:alice.pipeline.example"
    assert summary["curator"].startswith("did:web:alice.pipeline.example:")
    assert summary["curator"] != summary["persona"]

    # one post per curator selection, all seen by the public verifying consumer
    assert len(summary["posts"]) == len(SELECTIONS)
    assert summary["profile"]["displayName"] == "Sovereign Peer"
    assert summary["blob_cid"].startswith("bafkrei")  # raw-codec CID (0x55)
    assert summary["frames_audited"] > len(SELECTIONS)

    # the demo artwork really is a PNG
    assert DEMO_PNG.startswith(b"\x89PNG")


def test_bytes_padding_idiom_all_length_classes():
    """The poster's unpadded-base64 restore must work for every payload
    length class — blindly appending '==' breaks when the encoded length is
    already a multiple of 4 (byte length divisible by 3)."""
    import base64

    for n in (66, 67, 68):
        data = bytes(range(256))[:n]
        b64 = base64.b64encode(data).decode().rstrip("=")
        assert base64.b64decode(b64 + "=" * (-len(b64) % 4)) == data
