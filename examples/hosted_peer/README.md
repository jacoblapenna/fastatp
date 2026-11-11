# Hosted sovereign peers — the end-to-end example

This is `docs/HOSTED-PEERS.md` running: an "average user" app where **no
secret ever crosses the wire, and there is no login wall** — composed
entirely from shipped primitives. Everything here is *example edge code*;
the substrate ships mechanisms, never policy (PHILOSOPHY.md).

## Pieces

- **`builder.py`** — what the app builder runs:
  - a neutral **shelf** (`DIDServer` + shelf intake) hosting
    twin-only DID documents and continuity chains,
  - a **`PeerServer`** with the **browser gate** installed, hosting each
    repo **gated with owner pull**: the pen that writes it (read from the
    chain head on every knock) and the identity itself (a device speaking
    for it through a delegation record) read it back with no capability
    record; everyone else needs one,
  - two example routes: `POST /signup` (connection zero, host side: verify
    the genesis CAR against the shelved chain head and host it)
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

1. **Connection zero, fully client-side**: the app mints a non-extractable
   WebCrypto P-256 pen with `generate_pen()` and keeps the handle (storing
   it in IndexedDB is "keep me signed in"), then `connection_zero(domain,
   pen)` mints a paper rotation root (shown once, retained by nothing), a
   twin-only genesis whose slug — the identity's *name* — is computed
   locally, and the genesis repo CAR.
2. **One POST to the shelf, one to the builder.** Shelf rental, not account
   creation: at no instant does the builder hold a secret or sign anything.
3. **Writes happen client-side**, signed by the pen: a content record and a
   **delegation** for each enrolled device (`dev.fastatp.auth.delegation`).
   The pen-signed CAR is pushed; the router verifies and re-hosts it. Then
   device 1 **reads its own repo back as its owner**: one knock
   (`sign_challenge(pen, nonce, repo_did)`), no capability record, no
   delegation. The owner is not a subscriber.
4. **Device 2 syncs** with the same knock plus `iss`: its pen resolves to
   the identity **through the live delegation chain**, and the identity
   reads its own repo — presence IS authorization.
5. **A stranger pen is refused** despite a perfectly valid knock:
   holding a signature is not an identity.
6. **Severance**: device 1 *deletes* the delegation record and pushes.
   Device 2's next connect — fresh, valid knock — is refused.
   **Absence is revocation**: no revocation lists, no token
   expiry, nothing to propagate. If the record isn't there, the pen isn't
   anyone.
7. **Recovery**: every device is lost. A fresh browser mints a pen that is
   nobody yet. The user types the paper secret; `rotation_did_from_secret`
   checks it against the chain's rotation key on the page. `rotate_pen`
   signs a new chain page naming the new pen, and the **longer chain** goes
   to the shelf (an edit or a rollback is refused). The new pen then
   **pulls the repo as its owner before it has signed anything**, because
   the builder's rule reads the chain head; restores it (verifying the head
   against the *old* pen, which signed it); prunes the lost devices'
   delegations; writes; pushes — verified against the *new* head. The old
   pen can neither pull nor push: nobody's owner, and its commits no longer
   verify.

## The enrollment ceremony, honestly simulated

Every "device" here is a WebCrypto pen minted in the same Node process with
`generate_pen()`; none needs a repo of its own for any of this, because the
knock is about the repo being asked for. On real hardware the ceremony is
two devices exchanging exactly the two strings this example exchanges: the
new device shows its `pen_did` (a QR code); an existing device writes the
delegation record naming it. Public key one way, record presence the other
— nothing else moves. Recovery is likewise simulated by minting a new pen
and feeding the paper secret the first phase printed; on real hardware the
secret comes from the drawer.

## Honest limits of the example

- Reads are checkpoint CARs (`getRepo`); a production router would also emit
  firehose frames per import so subscribers see updates live.
- The builder keeps its stores in dicts; production shelves/routers persist
  (the substrate's SqliteBlockStore and your own database compose here).
- Admission policy (who may sign up, rate limits, payment) is deliberately
  absent — that is the builder's sovereign edge policy, never substrate.
