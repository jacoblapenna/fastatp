"""Two independent implementations composing over nothing but the wire format.

Meitheal (https://sensorthings.dev — Stephan Hügel, https://github.com/urschrei)
publishes real environmental monitoring networks as ``dev.sensorthings.*``
lexicons on atproto: signed records, public firehose, no API key. Its
companion PyPI package ``atproto-sensorthings`` (same author, built on the
MarshalX ``atproto`` SDK) flattens repo CAR exports to tables, offline.

This demo builds a small sensor network with FastATP — records signed by a
slug-DID pen (an on-device "sensor" identity), hosted in a FastATP repo —
exports it with ``export_car()``, and hands the export to the UNMODIFIED
``atproto-sensorthings`` package. Its tables come out correct: the strongest
kind of conformance evidence, because neither implementation knows the other
exists. They share only the published wire format.

The record shapes below are USER-AUTHORED raw dicts following Meitheal's
published ``dev.sensorthings`` conventions (scaled integers, never floats —
which the atproto data model requires anyway; ``#geoPoint`` locations as
``latE7``/``lonE7``/``altMm``). FastATP ships no ``dev.sensorthings``
schemas — the same rule as ``app.bsky.*``: the library is the type system,
the market above it defines the records.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastatp import crypto
from fastatp.crypto import encode_did_key
from fastatp.did.continuity import build_genesis
from fastatp.repo.repo import Repo

NS = "dev.sensorthings"


def _uri(did: str, collection: str, rkey: str) -> str:
    return f"at://{did}/{collection}/{rkey}"


def build_station_repo(domain: str = "sensors.example.com") -> Repo:
    """A FastATP repo holding one river-gauge station's SensorThings records."""
    pen = crypto.generate_keypair("k256")
    rotation = crypto.generate_keypair("k256")
    did, _genesis = build_genesis(
        domain,
        pen.public_key(),
        [encode_did_key(rotation.public_key())],
        bind_pretty=False,  # a twin-only slug DID: the on-device sensor identity
    )
    repo = Repo(did, pen)

    thing: dict[str, Any] = {
        "$type": f"{NS}.thing",
        "name": "Demo River Gauge",
        "description": "a FastATP-signed river monitoring station",
        "properties": {"operator": "Example Waterways", "stationType": "river gauge"},
        # #geoPoint: integer degrees*1e7 and millimetres (Dublin-ish)
        "location": {"latE7": 533_498_070, "lonE7": -62_603_100, "altMm": 12_000},
    }
    repo.put_record(f"{NS}.thing", "station-1", thing)
    thing_uri = _uri(did, f"{NS}.thing", "station-1")

    prop = {
        "$type": f"{NS}.observedProperty",
        "name": "Water Level",
        "definition": "http://vocab.example.org/waterLevel",
    }
    repo.put_record(f"{NS}.observedProperty", "water-level", prop)

    sensor = {"$type": f"{NS}.sensor", "name": "Pressure transducer"}
    repo.put_record(f"{NS}.sensor", "transducer-1", sensor)

    datastream = {
        "$type": f"{NS}.datastream",
        "name": "Water level at Demo River Gauge",
        "thing": thing_uri,
        "observedProperty": _uri(did, f"{NS}.observedProperty", "water-level"),
        "sensor": _uri(did, f"{NS}.sensor", "transducer-1"),
        "unitOfMeasurement": {"symbol": "m", "name": "metre"},
        # scaled integers: real value = value * 10**-resultScaleFactor
        "resultScaleFactor": 3,
    }
    repo.put_record(f"{NS}.datastream", "water-level-ds", datastream)
    ds_uri = _uri(did, f"{NS}.datastream", "water-level-ds")

    batch = {
        "$type": f"{NS}.observationBatch",
        "datastream": ds_uri,
        "observations": [
            # 1.234 m, 1.241 m, 1.198 m — as scale-3 integers
            {"t": "2026-07-15T10:00:00Z", "result": _numeric(1234)},
            {"t": "2026-07-15T10:15:00Z", "result": _numeric(1241), "q": f"{NS}.quality#good"},
            {"t": "2026-07-15T10:30:00Z", "result": _numeric(1198)},
        ],
    }
    repo.put_record(f"{NS}.observationBatch", "batch-1", batch)
    return repo


def _numeric(value: int) -> dict[str, Any]:
    return {"$type": f"{NS}.result#numericResult", "value": value}


def main(out_dir: str | Path = ".") -> dict[str, Any]:
    """Build, export, and flatten with the unmodified atproto-sensorthings."""
    out = Path(out_dir)
    repo = build_station_repo()
    car_path = out / "station.car"
    car_path.write_bytes(repo.export_car())

    # ---- the other implementation's turn: nothing below is FastATP ----
    from atproto_sensorthings import core

    flat = core.read_car(car_path)
    stations = core.station_records(flat)
    datastreams = core.datastream_records(flat)
    readings = list(core.observation_rows(flat))

    print(f"exported : {car_path} ({car_path.stat().st_size} bytes), did={repo.did}")
    print(f"stations : {[(s['station'], s['longitude'], s['latitude']) for s in stations]}")
    print(f"streams  : {[(d['datastream'], d['unit']) for d in datastreams]}")
    print(f"readings : {[(r['time'], r['value']) for r in readings]}")
    return {
        "did": repo.did,
        "stations": stations,
        "datastreams": datastreams,
        "readings": readings,
    }


if __name__ == "__main__":
    main()
