"""Cross-ecosystem interop with atproto-sensorthings.

The UNMODIFIED ``atproto-sensorthings`` package (Meitheal / Stephan Hügel,
https://sensorthings.dev — built on the MarshalX ``atproto`` SDK) flattens a
FastATP-exported repo CAR. Neither implementation knows the other exists;
they share only the published wire format — conformance evidence no
self-test can provide.

Dev-only: skips cleanly when the package is not installed (plain-pip
installs stay green; it is never a FastATP dependency).
"""

import sys
from pathlib import Path

import pytest

pytest.importorskip("atproto_sensorthings")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "examples"))

from sensorthings_interop.demo import main  # noqa: E402


def test_unmodified_flattener_reads_fastatp_export(tmp_path):
    result = main(tmp_path)

    # a slug-DID (twin-only) sensor identity signed everything
    assert result["did"].startswith("did:web:sensors.example.com:")

    [station] = result["stations"]
    assert station["station"] == "Demo River Gauge"
    # #geoPoint E7 integers decoded to WGS84 degrees by THEIR code
    assert station["longitude"] == pytest.approx(-6.26031)
    assert station["latitude"] == pytest.approx(53.349807)
    assert station["altitude_m"] == pytest.approx(12.0)
    assert station["operator"] == "Example Waterways"

    [ds] = result["datastreams"]
    assert ds["unit"] == "m" and ds["property"] == "Water Level"
    assert ds["sensor"] == "Pressure transducer"  # AT-URI ref joined by THEIR code

    # scaled integers (data-model: no floats on the wire) descaled by THEIR code
    values = [r["value"] for r in result["readings"]]
    assert values == [pytest.approx(1.234), pytest.approx(1.241), pytest.approx(1.198)]
    assert [r["quality"] for r in result["readings"]] == [None, "good", None]
    assert all(r["station"] == "Demo River Gauge" for r in result["readings"])
