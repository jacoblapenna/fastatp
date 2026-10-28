// "Sign up via Bluesky" — documentation-grade example.
//
// THIS FILE IS A RECIPE, NOT A RUNNABLE MODULE IN THIS REPO. The OAuth
// client is the BUILDER's dependency, installed in the builder's app:
//
//     npm install @atproto/oauth-client-browser
//
// No OAuth code or dependency ever enters the fastatp package. The session
// below lives between the user and THEIR host, is used once to deposit one
// record, and never touches the substrate.

import { BrowserOAuthClient } from "@atproto/oauth-client-browser";

const DELEGATION_NSID = "dev.fastatp.auth.delegation";

// 1. OAuth to the user's OWN host (their PDS decides the login UX).
//    client_metadata is your app's published OAuth metadata document.
const oauth = await BrowserOAuthClient.load({
  clientId: "https://your-app.example/oauth/client-metadata.json",
  handleResolver: "https://bsky.social",
});
const session = await oauth.signIn(handleTheUserTyped, { scope: "atproto transition:generic" });

// 2. Deposit the delegation record in THEIR repo, through THEIR host.
//    `appPenDid` is your app-side pen's did:key — e.g. from the FastATP
//    browser bundle: (await connection_zero(domain)).pen_did — minted
//    non-extractable, never leaving the device.
await session.fetchHandler("/xrpc/com.atproto.repo.createRecord", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({
    repo: session.did, // their identity, their repo
    collection: DELEGATION_NSID,
    rkey: "your-app-pen", // one live delegation per pen; overwrite = re-vouch
    record: {
      $type: DELEGATION_NSID,
      issuer: session.did, // must equal the repo it lives in
      subject: appPenDid,
      note: "your-app.example sign-up",
    },
  }),
});

// The OAuth session's job is done. Discard it; keep nothing bearer-shaped.

// 3. Your app (any side — this needs no auth at all) consumes BY PRESENCE:
//    a fresh sync.getRecord proof, verified against their DID document.
//    With FastATP server-side that is exactly Peer.verify() on the returned
//    CAR slice (plc roots resolve via the read-only plc_resolver plug-in).
const proof = await fetch(
  `${theirPds}/xrpc/com.atproto.sync.getRecord` +
    `?did=${session.did}&collection=${DELEGATION_NSID}&rkey=your-app-pen`,
);
// -> POST proof to your backend; it runs Peer.verify(proof, expected_signer=their DID)
//    and, on success, treats appPenDid as delegated by session.did — for as
//    long as the record stays present. The user deletes it to revoke.
//    Absence is revocation; there is nothing else to expire or propagate.
