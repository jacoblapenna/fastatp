# 0003: Bring your own database

- Date: 2026-11-17
- Status: accepted
- Tenets served: ship primitives, not applications (4); no trusted third
  party (1); transport-agnostic core (5)

## Problem

A repo is signed blocks plus one pointer to the newest commit, and a hosted
repo also carries a firehose seq. The blocks already went through
`BlockStore`, so an app could keep them anywhere. The pointer did not:
`Repo(..., head_cid=)` took it from the caller, `Identity` wrote it to a
`repo_head` file, the hosted example kept it in memory, and the seq was a
counter in `_HostedRepo`. So "my repos live in my database" meant every app
wrote its own head bookkeeping, nothing stopped two processes from
committing on the same head (every commit is a link in one signed chain, so
two writers fork it), and every restart sent every follower a `FutureCursor`.
The first user wants its repos in its own Postgres beside the rest of its
data, with one peer process now and several behind the same repos later.

## Decision

**The head is a compare-and-swap, not a held lock.** `HeadStore` is three
methods keyed by DID: `load`, `swap(did, expected, new)`, `delete`. A commit
reads the head, builds and signs the new commit, writes its blocks, then
advances the head in one statement: from the head it built on to the new
one, only if it still is that head. The database's atomicity for a
single-row update decides who wins. The loser reloads the new head and
retries on it (bounded); a write the winner already made byte for byte is a
no-op; a `create` whose path now holds other bytes fails as it always did;
contention beyond the retries is `HeadMoved`, carrying the current head. No
row is held across the signature, and nothing is held through a crash.

**The seq rides on the same row.** A commit's seq is the one its swap
assigned; every other frame takes the next through the same swap, so a
number is durable before its frame goes out. The genesis is seq 0, the start
of the stream. Gaps are allowed (a number allocated for a frame that never
left the process); reuse never is. A restarted peer continues from the last
number it allocated and followers' cursors stay valid.

**Storage is one decision.** `Storage` bundles `blocks`, `heads` and
`blobs`; a deployment picks one database for all three, never split, since
the swap only protects a repo whose blocks every process can see. A
`Storage` serves one repo. `SqliteStorage(directory)` is the built-in and
what `Identity.bootstrap(state_dir=)` uses unasked; bringing your own is a
class with the same three attributes over the app's adapters, passed again
on every boot the way a hardware signer is. The package ships no adapter and
depends on no driver.

**Garbage collection is per commit and per repo, never a sweep.** A commit
or import deletes only the blocks it orphaned (reachable from the head it
replaced and not from the new one), so repos sharing a table and processes
sharing a repo leave each other's blocks alone. `collect_garbage()` is the
sweep, for a store the repo has to itself. A shared table keys blocks by
`(did, cid)` so one block two repos hold by content is never one row.

**The doorman's cache is for speed, never authority.**
`TunnelIdentityResolver(lookup=)` consults the builder's address table
first; a hit names a caller only after the doorman confirms the named repo
still holds a live capability for that DID whose key derives that address,
and a miss or a failed confirmation falls back to the walk, which stays the
behaviour with no lookup.

**The head store is async; the block and blob stores stay synchronous.**
The MST walks blocks many times per commit; the head is one round-trip per
commit, exactly where the commit path already awaits. A Postgres storage
holds two handles to one database.

## Why not the alternatives

- **A held lock (`SELECT ... FOR UPDATE` across the commit).** Every other
  process queues behind a hardware signature, and a crash leaves a lock the
  database has to time out. The swap holds nothing and a crash leaves only
  unreachable blocks.
- **A separate seq store.** Head and seq could then disagree after a crash,
  and the server would need a second durable store with its own swap. One
  row keeps them one atomic step.
- **Blocks here, heads there.** A swap on the head protects nothing if a
  process cannot see the blocks the new head reaches. One database, by
  rule.
- **Sweep garbage collection in shared storage.** Reachability computed from
  one repo's head, or one process's stale view of it, deletes what another
  head still reaches. Per-commit orphans are scoped by construction.
- **A cache the doorman trusts.** A stale entry would admit a rotated-out
  key or a revoked DID. Confirming every hit against the live record makes
  a wrong entry cost one lookup and nothing else.
- **Async block stores now.** It means rewriting the MST, and is its own
  decision for its own reason.

## Consequences and limits

- Several identical processes behind one database can never fork a repo;
  one commit per head wins and the loser retries on the current head.
- What the store does not do: refresh one process's in-memory repo from
  another's commits, or fan a commit out to subscribers connected to another
  process. An app that runs several processes builds that on the head
  store (`LISTEN/NOTIFY`, polling the head). Choosing which process follows
  which producer is app policy.
- A narrow race remains between one process's swap and its orphan
  deletion: another process can land a commit that reuses an orphaned block
  by content. The next pull or push repairs it; an adapter that defers
  deletes removes it.
- `Identity` keeps its small files in the `state_dir` under either choice:
  the marker, the chain, a software pen. Under bring-your-own no sqlite is
  created there.
- The tutorial's reboot story follows from the built-in path alone: an
  identity on a `state_dir` resumes its head and seq with no new argument.
