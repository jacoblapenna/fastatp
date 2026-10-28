# hygiene — key custody, backup, rotation, and recovery

The operational companion to [`bluesky_peer`](../bluesky_peer/README.md): what
lives on disk, which bytes are secret, what to put in a password manager, what
to back up (and how, without corrupting a live SQLite file), and the two very
different roads back when something is lost.

Nothing here is Bluesky-specific — this is the custody story for any FastATP
`did:web` identity.

## The two pens

| Pen | File | Lives | Lost ⇒ |
| --- | --- | --- | --- |
| **Signing pen** | `state/signing.key` | on the box, `0600` | rotate to a new one (routine) |
| **Rotation pen** | *nowhere on the box* | password manager / cold | **recovery path 1 is gone forever** |

The signing pen is everyday authority: it signs every record and commit. The
rotation pen is *recovery* authority: it is the only key that can append a
page to the continuity chain, i.e. the only key that can say "same identity,
new signing key". It is printed exactly once at mint
(`bootstrap_identity.py`) and never persisted unless you loudly opt in with
`persist_rotation_key=True` — don't. A box compromise must not also be a
rotation-authority compromise.

Rotating the signing pen does **not** change the rotation pen:
`Identity.rotate()` carries `rotationKeys` over unchanged, so the same cold
pen stays the recovery authority across every rotation. (Rotation-pen
succession is not exposed by the current API; if the rotation pen itself is
compromised you are choosing between racing the attacker and re-genesis.)

## What is in `state/`

| File | Secret? | What it is | Changes |
| --- | --- | --- | --- |
| `signing.key` | **yes** | the signing pen (raw scalar, `0600`) | only on rotation |
| `rotation.key` | **yes** | only exists if you opted in — it shouldn't | never |
| `identity.json` | no | DID + curve metadata; **the "I exist" marker** | never |
| `chain.json` | no | the rotation-signed DID-document chain | only on rotation |
| `repo_head` | no | current commit CID | **every commit** |
| `blocks.sqlite` | no | records + MST + commits (SQLite, WAL) | every commit |
| `blobs.sqlite` | no | blob bytes (SQLite, WAL) | every blob |

Two files people forget, and why they matter:

- **`identity.json`** is the first thing `Identity.bootstrap()` checks. If it
  is missing, bootstrap does not fail — it silently **mints a brand-new
  identity** (new keys, new genesis, new twin) under your same domain. An
  incomplete restore therefore looks like a hostile key swap to everyone
  verifying continuity. `restore.py` refuses to proceed without it.
- **`repo_head`** points at a commit that must exist in `blocks.sqlite` and
  verify under `signing.key`. The three are one consistent unit; never back
  them up at different instants.

## Custody plan

**Password manager (cold, tiny, ~never changes):**

- the **rotation pen** — its own entry, printed once at mint;
- `signing.key` (optional but recommended — survives losing the box between
  backups; re-copy it after any rotation);
- `identity.json` and `chain.json` fit in a note field and cost nothing.

**Ordinary backups (rsync/NAS/whatever — the hot set):**

- `blocks.sqlite` + `blobs.sqlite` + `repo_head`, snapshotted **together**
  (`backup.py` below).

If a backup archive contains `signing.key`, encrypt the archive (`age`,
`gpg`); a plaintext copy on a NAS or in a private repo *is* the leaked-pen
scenario.

**The two non-file assets:** the **domain registration** (for `did:web` the
domain *is* the identity — no backup recovers a lapsed domain) and the
**static host source** (`did.json`, `atproto-did`, `chain.json` — the CDN is
serving, not archival; keep the source files backed up too). `chain.json` is
public and only changes on rotation, so a periodic `curl` of your own
published copy is a fine independent mirror — daily is plenty.

## Backing up live SQLite (the WAL caveat)

Both stores open with `PRAGMA journal_mode=WAL`: recent writes live in
`-wal`/`-shm` sidecar files until checkpointed. A plain `cp`/`rsync` of the
`.sqlite` file from under a running peer copies a **stale or torn** database.

`backup.py` uses SQLite's online backup API, which folds the WAL in and emits
one consistent file per database. That fixes per-file torn copies — but
cross-file consistency (`repo_head` ↔ `blocks.sqlite`, and the store
garbage-collects blocks unreachable from the current root on every commit)
still requires that **nothing is writing during the snapshot**. Same
single-writer discipline as `post.py`: stop the peer, snapshot, restart.

## The two recovery paths

**Path 1 — rotation (the designed path).** You still hold the rotation pen.
`rotate.py` appends a rotation-signed page to the chain, swaps the signing
key, and re-signs the repo head. Same DID, same twin, same record CIDs —
every strong ref anyone holds to your posts still matches. Continuity
verifiers see a legitimate, pre-authorized event, not an anomaly. This works
even if the whole box burned down: `chain.json` was public (CDN, backups) and
`rotate()` needs only the chain plus the cold pen; content comes back from
the hot-set backup.

**Path 2 — re-genesis (the honest fallback).** Rotation pen gone. Re-run
`bootstrap_identity.py` against an empty state dir: new keys, new genesis,
new twin — nothing cryptographically links old to new. Plain `did:web`
resolution (relays, the AppView) is stateless and accepts the new key
silently; FastATP continuity peers see exactly what it is — an unauthorized
key swap under a durable name — and fail closed on the old twin. Your
*content* is still recoverable: records are content-addressed and signatures
live only in the top commit, so restoring the backed-up records and
re-signing under the new pen brings every byte back with identical record
CIDs. What does not come back is continuity: the identity is visibly new.

The difference between the two paths is one cold key. Guard it accordingly.

## Never serve `state/`, never commit `state/`

- **git:** the repo `.gitignore` ignores `examples/**/state/` and
  `examples/**/static/`. If you build your own deployment, ignore your state
  dir before first mint — `signing.key` in git history is a leak you can't
  fully retract.
- **web:** the peer exposes the repo only through the `com.atproto.sync.*`
  XRPC routes; the raw files must never sit behind a static-file route. Web
  frameworks serve only allowlisted directories — SvelteKit serves `static/`,
  Next.js and CRA serve `public/`, nginx/Caddy serve their configured root —
  so the rule that covers all of them: keep `state/` **outside the web
  root**, ideally outside the served project entirely (e.g.
  `/var/lib/fastatp/state`, mode `0700`). Do not nest it under `static/`,
  `public/`, `dist/`, or a server document root. Note the irony hazard: this
  example's `--out ./static` directory is *meant* to be published; `state/`
  sits one typo away. Keep them far apart.

## The scripts

All three are standalone; run them from this directory with the peer
**stopped** (single writer).

```bash
# consistent snapshot of state/ -> a fresh directory
python backup.py --state ../bluesky_peer/state --dest /backups/alice-2026-07-19
python backup.py --state ../bluesky_peer/state --dest ... --exclude-secrets

# restore a snapshot into a (new) state dir; refuses incomplete backups
python restore.py --backup /backups/alice-2026-07-19 --state ../bluesky_peer/state

# path 1: new signing pen, same identity; prompts for the cold rotation pen
# (domain and curve come from the state dir itself — nothing to retype wrong)
python rotate.py --state ../bluesky_peer/state \
    --pds https://YOUR-TUNNEL-HOSTNAME --out ../bluesky_peer/static
```

After `rotate.py`: republish `--out` to your static host (the old `did.json`
and `chain.json` are stale the moment rotation succeeds), restart the peer,
and update the password-manager copy of `signing.key` if you keep one.
