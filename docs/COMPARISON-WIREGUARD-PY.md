# How FastATP differs from the Python WireGuard ecosystem

This document compares FastATP's WireGuard enforcement plane against the Python
WireGuard ecosystem, alongside a hardening audit of FastATP's own surface. One
thing this comparison needs that the atproto SDK comparison did not: **a license
wall down the middle.**

## Authority ladder

1. **WireGuard itself** — the whitepaper and the observed behavior of
   `wg`(8) / the kernel module, probed live in a network-namespace harness.
   This is the wire oracle: our `WgToolInterface` shells out to `wg`, so
   `wg`'s actual semantics are ground truth. Every pinned behavior below is
   backed by a netns probe, not by any package's code.
2. `PHILOSOPHY.md` — governs what our layer IS: an enforcement plane where
   presence on the peer list is authorization and absence is revocation. Not a
   VPN manager.
3. The three Python packages — production prior art, tertiary.

## Candidates (SHAs + licenses)

| Package | Studied SHA | License (verified) | Posture |
| --- | --- | --- | --- |
| `wireguard` (Fictive Kin, github.com/fictive-kin/wireguard) | `97b273a89bce919cfd82c14ef34eb45c7bf1a8c9` | **MIT** (LICENSE.txt) | full study + ADAPT (attributed) |
| `django-wireguard` (thatsed, gitlab.com/thatsed/django-wireguard) | `7151e679ceef5d8813eb4d329e11d407e3e9294d` | **MIT** (`LICENCE` file, © Alessandro Romani; + setup.cfg) | full study + ADAPT (attributed) |
| `python-wireguard` (jarnoaxel, github.com/jarnoaxel/python-wireguard) | `a28d0f64a8697e7f1b2194678d81eae1c5543a0b` | **GPL-3.0-or-later** (LICENSE) | **READ-ONLY** — behavioral observation only |

`django-wireguard` carries a standalone license file — `LICENCE` (British
spelling), MIT, © Alessandro Romani — and attributions credit him. The GPL
clone yielded zero copied lines (grep-audited; see §7).

## The license wall

- **MIT** (`wireguard`, `django-wireguard`): ADOPT with credit, ADAPT test
  cases with attribution in the test docstring.
- **GPL** (`python-wireguard`; also `wireguard-tools` itself, GPL-2): read to
  understand behavior and incident history; findings recorded as prose
  observations with citations. **Nothing copied.** Every test in areas the
  GPL package touches was authored from independently-probed `wg` behavior
  in the netns harness, never from their code.

---

## Headline

The overlap is thin — expected, since all three are VPN-management tools and
FastATP is an enforcement plane — and *that thinness is a finding*. The
concrete value landed in four places:

1. **Our own hardening.** Two real fixes (error-taxonomy leak; `(none)`
   allowed-ips parse) plus five audit items proven, several against a **real
   tunnel** — including the headline concern, that revocation stops an
   established session mid-flight, not just the peer list.
2. **A rootless CI story.** The WireGuard kernel module works inside a
   rootless user+net namespace (`unshare --user --net --map-root-user`), so
   the netns proof runs with **no sudo and no privileged container** — on
   this Pi and on a stock GitHub `ubuntu-latest` runner.
3. **Validation of the subprocess bet.** All three packages, in different
   ways, document why FastATP shells out to `wg` instead of binding the
   kernel stack (§5).
4. **A small, honest set of adapted tests** (§6): one cross-impl X25519 known-answer pair,
   an allowed-ips normalization vector table, negative key-validation cases.

**Zero ADOPTs of their code.** Every package's object model is the
config-file / VPN-management approach FastATP's enforcement-plane design exists to avoid.

---

## 1. Key handling

- **Generation:** Fictive Kin uses PyNaCl (libsodium); we use `cryptography`
  x25519. I verified **byte-exact agreement**: their two published PyNaCl
  private keys derive, through our `cryptography` path, to their exact
  published public keys. That is now a cross-impl interop KAT
  (`test_x25519_known_answer_cross_impl`, attributed). DIVERGE on library
  (CLAUDE.md: `cryptography` only, no new primitive dep), ADAPT the vectors.
- **Validation:** Fictive Kin has **no key validator** and decodes with
  non-strict base64 (`test_config.py:125` accepts `"something"` as a public
  key); `django-wireguard` is IPv4-only and validates only v4. FastATP
  rejects strictly (32 bytes, 44-char standard base64) at every entry point.
  DIVERGE (ours is correct-by-construction); their laxity adapted as our
  negative cases (`test_key_validation_rejects`).

## 2. Config / URI model

DIVERGE, wholesale (live-`wg set` only; tenet 5 socket-free core).
All three model a peer as a long-lived object serialized to a `wg-quick`
`.conf` file (Fictive Kin), a Django row (`django-wireguard`), or a netlink
device (`python-wireguard`). FastATP publishes reachability as *data* (public
key + tunnel IP in a `WireGuardFirehose` DID service entry) and enforces by
live `wg set` mutation only. Fictive Kin's config layer is also where their
shipping wounds cluster (non-deterministic key ordering fixed in `59cfb77`;
blank-line/`os.linesep` churn in `06ce96f`/`8b549a4`; an **IPv6 endpoint
corruption bug**, `b5bbb3f`, where a bracket-less v6 endpoint gets `:port`
appended to produce a different valid address) — failure classes that cannot
exist in a system that never writes config text and treats endpoints as
optional/unused. AllowedIPs: their auto-`/32`/`/128` derivation matches ours;
their "route to self / whole subnet" gateway features are OUT (we pin one
`/32`|`/128` host route per peer — the doorman routes on it).

## 3. `wg` interaction / the hardening audit

The audit's seven items, each closed with a landed test or a documented
non-issue. "Probe" = observed live in the rootless netns harness.

| # | Item | Probe result | Resolution |
| --- | --- | --- | --- |
| 1 | `wg show allowed-ips` parsing | key`\t`ips; multiple CIDRs space-separated; empty peer prints `(none)` | **FIX:** `peers()` normalizes `(none)`→`""`; multi-CIDR + v6 parse pinned (`test_peers_parses_multi_cidr_and_none_rows`) |
| 2 | key validation strictness | `wg` itself rejects malformed keys (nonzero + stderr) | already strict; unpadded / whitespace / wrong-length / `"something"` rejection pinned |
| 3 | IPv6 end to end | v6 allowed-ips + bracketed v6 endpoint accepted by `wg`; a v6 tunnel pings across after grant | unit path pinned + **real v6 tunnel** via the reconciler (`test_wireguard_rootless_netns`) |
| 4 | revocation latency | granted v4 tunnel carries ping; `wg set peer remove` on the ESTABLISHED session stops packets (no re-handshake) | **proven on a real tunnel** through `reconciler.revoke` (`test_wireguard_rootless_netns`) |
| 5 | reconciler races / idempotency | — | reconcile is a no-op when converged; a partial-failure grant (records committed, `wg` fails) heals on next reconcile; both pinned. Concurrent `reconcile()` is out of contract (the reconciler OWNS its interface, single-writer — docstring) |
| 6 | error taxonomy | an injected runner raising `CalledProcessError` escaped raw | **FIX:** `WgToolInterface._invoke` maps every runner's `CalledProcessError`/`FileNotFoundError` to `EnforcementError` at the boundary (`test_wg_failure_becomes_enforcement_error`) |
| 7 | endpoint roaming | `wg` learns the endpoint from the handshake | reconciler derives allowed-ips from the tunnel IP only and never sends the external endpoint; roaming assumption confirmed safe (`test_endpoint_roaming_never_pinned`) |

Two of these (1, 6) were real defects; the fixes are in `fastatp/authz/wireguard.py`.

## 4. Test harness

The `django-wireguard` Docker harness billed as the gate's prize **does not
exist in the form implied**: its kernel-touching tests are `@tag('net')`,
its `Dockerfile.test` is unprivileged and not wired into CI, and
`.gitlab-ci.yml` runs the suite with `--exclude-tag net` — so its enforcement
path had **zero CI coverage** for the project's life. FastATP's
`test_wireguard_netns.py` (real handshake + ping gated by grant/revoke) is
strictly more rigorous than anything in any of the three. The lesson is
inverted and ADOPTed: **run the netns suite in CI, don't quarantine it** —
see the shipping study.

## 5. Shipping study — staying raw WireGuard, portably

The three packages embody three strategies; each teaches how (or how not) to
ship kernel-adjacent code on PyPI.

- **Avoid the stack** (Fictive Kin, pure config/key text): portable
  everywhere because it never touches the kernel — but still drags PyNaCl +
  `click` + `subnet-utils` and ships a `.conf` generator. FastATP's pure tier
  (keys, URIs, service entries, reconciler *logic*) is **more** portable:
  `cryptography` only, no config text at all.
- **Bind the stack** (`python-wireguard`, GPL, observation-only): ctypes over
  a hand-compiled `.so`. The observed costs of the road we don't take:
  packaging decoupled from the compiler (`make` outside `pip`; a real
  "forgot to compile → empty wheel" window in their history); a mistagged
  `py3-none-any` wheel that carries a glibc-x86-64 `.so` and installs-then-
  fails off-platform; a genuine C buffer overflow (`strcpy`→`snprintf`, their
  #22/#23) dragging flawfinder+CodeQL into CI; netlink errors calling
  `exit(1)` and killing the host process; tests that require root + kernel
  module + fd-capture of C `perror`. And it *still* shells out to `ip` for
  addressing — so "pure bindings" is a fiction at the edges. Most tellingly,
  it has **no live single-peer removal** — FastATP's core revocation
  primitive — because that needs the `WGPEER_REMOVE_ME` path they never wired
  up, whereas it's one `wg set … remove` line for us.
- **Contain the stack** (`django-wireguard`, privileged Docker): the CI
  cautionary tale above.

**Platform matrix (subprocess-only design — the portability bet, verified
where reachable):**

| Platform | `wg`/`wg set`,`wg show` grammar | Kernel path | FastATP needs |
| --- | --- | --- | --- |
| Linux | same | in-kernel module | `wireguard-tools` + `iproute2`; root **or** rootless `unshare` |
| macOS | same `wg`(8) CLI | wireguard-go (userspace, brew) | `wireguard-tools`; the CLI grammar is identical (subprocess design holds) |
| Windows | `wg.exe` same subcommands | wireguard-nt | `wg.exe` on PATH; interface mgmt differs but `wg set/show` grammar matches |

The subprocess boundary means FastATP inherits whatever platform support the
installed userspace tools provide, with **no compiler, no wheel matrix, no C
surface** of its own. **Graceful degradation is a contract, pinned by test**
(`test_graceful_degradation_without_wg_binary`): with no `wg` on PATH the
package imports, the enforcement API constructs, key ops work, and only the
first real interface call fails — as one clear `EnforcementError` naming
`wireguard-tools`. The browser gate is the doorman alternative
where WG is unavailable.

**Rootless netns proof** (`tests/test_wireguard_rootless_netns.py`): runs the
enforcement proof **rootless under `unshare`** — no root, no privileged
container. It skips cleanly where `wg`, the kernel module, or user namespaces
are absent.

## 6. Attributed test ledger (MIT only, attributed in each test)

| Source (MIT) | Cases | FastATP test |
| --- | --- | --- |
| fictive-kin/wireguard `test_json.py` | two X25519 priv→pub known-answer pairs (PyNaCl origin) | `test_x25519_known_answer_cross_impl` |
| fictive-kin/wireguard `test_config.py:125`, `test_peers.py` | `"something"` and v4/v6 sample addresses (inverted into negative + `/32`/`/128` cases) | `test_key_validation_rejects`, `test_ipv6_tunnel_ip_end_to_end`, `test_ipv4_allowed_ips_is_slash_32` |
| django-wireguard `test_package.py` (comma-list clean; missing-interface; key round-trip) | separator/whitespace/trailing-comma normalization table; missing-iface→error; b64 round-trip | `test_add_peer_normalizes_allowed_ips`, `test_missing_binary_becomes_enforcement_error`, `test_wireguard_keypair_roundtrip_invariant` |

Nothing from `python-wireguard` (GPL) is reused; its contributions are the
prose observations in §5 and the behaviors the netns harness independently
re-probed.

## 7. GPL grep-audit

FastATP's source is grepped against the `python-wireguard`
clone for copied identifiers, string literals, and structure. Result: **zero
GPL-derived lines.** The netns behaviors the GPL study pointed at (live peer
removal tears down the session; allowed-ips is replace-not-append and
idempotent; key format; missing-peer packet drop; endpoint roaming) were each
re-probed directly with `wg` in the harness and pinned from that observation.

## 8. Divergence → tenet table

| Divergence (ours stays) | Tenet / primitive that requires it |
| --- | --- |
| Live `wg set` mutation, never a `.conf`/wg-quick lifecycle | Enforcement plane, not config management |
| Publish reachability as DID-document data; enforce separately | Tenet 2 (data, not calls); the doorman |
| No PSK | Tenet 7 (no shared secrets) |
| Subprocess to `wg`, not C bindings / netlink | Tenet 5 (socket-free, dependency-light core); pure-`any` wheel; no C surface |
| Endpoints optional/unused (peers roam) | Identity is the tunnel IP the doorman routes on, not the external endpoint |
| Always-on strict key validation | records/keys are the trust boundary in signed mail |
| WG is an optional adapter (graceful degradation) | The enforcement-plane design + the browser-gate alternative |
