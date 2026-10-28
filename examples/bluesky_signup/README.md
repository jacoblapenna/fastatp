# "Sign up via Bluesky" — Case-1 delegation

A user who already has an atproto identity (their own `did:web`, or a
Bluesky `did:plc`) brings it to your app the way "Sign in with Google"
works — except nothing bearer-shaped is ever minted for *your* substrate.
The flow deposits **one record** in the repo the user already has, and your
app consumes it **by presence**:

1. The user approves your app at *their own* host via atproto OAuth.
2. Your app (client-side) calls `com.atproto.repo.createRecord` to write a
   `dev.fastatp.auth.delegation` record — "this identity vouches for this
   app pen" — into *their* repo. Their PDS signs the commit; custodial
   users have no accessible pen, and don't need one: **presence in their
   signed repo is the proof** (DNS-TXT-shaped).
3. Your app verifies by fetching a fresh `com.atproto.sync.getRecord` proof
   and checking it against their DID document. Revocation = the user
   deletes the record from their repo. Absence is revocation, same as
   everywhere else in FastATP.

See `signup.example.js` for the composition. Note what it imports:

- **`@atproto/oauth-client-browser` is YOUR dependency, not FastATP's.**
  No OAuth code or dependency exists anywhere in this package, by design.
  The OAuth session lives entirely between the user and
  *their* host, is used once, and never touches the substrate — the
  substrate only ever sees the resulting signed record.
- The delegation record fields are the shipped lexicon's
  (`fastatp/authz/delegation.py`): `issuer` (their DID), `subject` (your
  app pen's `did:key`), optional `expires_at`, reserved `scope`.

## The custody caveat — disclose it, never hide it

Case-1 inherits the custody trust of the identity's home: a custodial host
(all of `bsky.social`) *could* forge the delegation, because it holds the
user's signing key. Sovereignty is total from the app's device pens down;
the root is exactly as sovereign as the user's existing arrangement. Apps
should say so. `did:plc` roots resolve via the sanctioned read-only
`plc_resolver` plug-in — resolution only, never lifecycle
writes.
