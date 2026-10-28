# sensorthings_interop — two implementations, one wire format

[Meitheal](https://sensorthings.dev) (Stephan Hügel,
[@urschrei](https://github.com/urschrei)) publishes ten real environmental
monitoring networks — ~5k+ stations of water levels, air quality, flood
warning, marine buoys — as `dev.sensorthings.*` lexicons on atproto: signed
records on a public firehose, no API key, DIDs that survive server
migration. It is independent, production, **non-social** atproto — living
proof of the tenet that the market emerges above the protocol.

Its companion package [`atproto-sensorthings`](https://pypi.org/project/atproto-sensorthings/)
(same author, built on the [MarshalX `atproto` SDK](https://github.com/MarshalX/atproto))
flattens repo CAR exports to geo-referenced tables, offline.

This demo composes the two ecosystems:

1. **FastATP** builds a river-gauge station as user-authored
   `dev.sensorthings.*` records (we ship no schemas — the `app.bsky.*` rule
   applies identically), signs them with a **slug-DID pen** — an on-device
   sensor identity, the sovereign half a custodial PDS deployment doesn't
   attempt — and `export_car()`s the repo.
2. The **unmodified** `atproto-sensorthings` package reads that CAR and
   produces correct tables: geoPoint E7 integers to WGS84 degrees, AT-URI
   references joined across records, scaled integers descaled.

Neither implementation knows the other exists. They share nothing but the
published wire format — which is exactly the point.

```
pip install atproto-sensorthings   # dev-only; never a FastATP dependency
python demo.py
```

Meitheal's architecture (custodial PDS, generated tooling) and FastATP's
(on-device pens, user-defined lexicons) are different, valid deployments of
the same protocol; this demo is what their composition looks like.
