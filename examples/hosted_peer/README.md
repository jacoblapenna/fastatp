# Hosted sovereign peers — the end-to-end example

This is `docs/HOSTED-PEERS.md` running: an "average user" app where **no
secret ever crosses the wire, and there is no login wall** — composed
entirely from shipped primitives. Everything here is *example edge code*;
the substrate ships mechanisms, never policy (PHILOSOPHY.md).

## Pieces

- **`builder.py`** — what the app builder runs:
  - a neutral **shelf** (`DIDServer` + shelf intake) hosting
    twin-only DID documents and continuity chains,
  - a **`PeerServer`** with the **browser gate** installed,
  - two example routes: `POST /signup` (connection zero, host side: verify
    the genesis CAR against the shelved chain head and host it **gated**)
    and `POST /push-car` (the example "router": re-verify the pen-signed
    CAR against the *current* chain head and replace the hosted state — the
    host can drop or delay, never forge).
- **`client.mjs`** — the browser side, driven under Node 22 using the exact
  wasm bundle the wheel ships (`fastatp/browser/static/`, built by
  `scripts/build-wasm.sh`; Node has the same WebCrypto and fetch a browser
  has).

Run it yourself:

```bash
python examples/hosted_peer/builder.py           # shelf :8100, peers :8101
node examples/hosted_peer/client.mjs http://127.0.0.1:8101 http://127.0.0.1:8100 /tmp/hosted-demo
cat /tmp/hosted-demo/report.json
```

(Or run the whole thing as a test: `uv run pytest tests/test_hosted_peer_e2e.py`.)

## What the client does, in HOSTED-PEERS.md terms

1. **Connection zero, fully client-side**: mints a non-extractable WebCrypto
   P-256 pen (it never exists off-device, in any form), a paper rotation
   root (shown once, retained by nothing), a twin-only genesis whose slug —
   the identity's *name* — is computed locally, and the genesis repo CAR.
2. **One POST to the shelf, one to the builder.** Shelf rental, not account
   creation: at no instant does the builder hold a secret or sign anything.
3. **Writes happen client-side**, signed by the pen: a self-capability
   (`subscribe`), a content record, and a **delegation** for device 2
   (`dev.fastatp.auth.delegation`). The pen-signed CAR is pushed;
   the router verifies and re-hosts it.
4. **Device 2 syncs** by passing the signed-challenge gate: its pen resolves
   to the identity **through the live delegation chain** — presence IS
   authorization.
5. **A stranger pen is refused** despite a perfectly valid challenge:
   holding a signature is not an identity.
6. **Severance**: device 1 *deletes* the delegation record and pushes.
   Device 2's next connect — fresh, valid challenge — is refused.
   **Absence is revocation**: no revocation lists, no token
   expiry, nothing to propagate. If the record isn't there, the pen isn't
   anyone.

## The enrollment ceremony, honestly simulated

"Device 2" here is a second WebCrypto keypair in the same Node process. On
real hardware the ceremony is two devices exchanging exactly the two strings
this example exchanges: the new device shows its `pen_did` (a QR code); an
existing device writes the delegation record naming it. Public key one way,
record presence the other — nothing else moves.

## Honest limits of the example

- Reads are checkpoint CARs (`getRepo`); a production router would also emit
  firehose frames per import so subscribers see updates live.
- The builder keeps its stores in dicts; production shelves/routers persist
  (the substrate's SqliteBlockStore and your own database compose here).
- Admission policy (who may sign up, rate limits, payment) is deliberately
  absent — that is the builder's sovereign edge policy, never substrate.
