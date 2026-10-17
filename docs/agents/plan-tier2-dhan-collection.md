# Plan — Tier-2 Dhan Collection Campaign (recorded 5m fixtures + depth snapshots)

**Status:** §7 **SIGNED by the owner (2026-10-16)** — Q1–Q5 all at their
recommendations. Phase 1 (offline-verifiable client alignment, fixture flags,
`.env.example`) is authorized; Phases 0/2/3 are operator runs gated on Dhan
credentials. Stage 8's frozen contract
([TICK-008](../tickets/TICK-008-dhan-precision.md), [BRD §8.1](../../BRD.md))
changes only through §7.

---

## 1. Why this campaign exists

Stage 8 ships the **daily-tier** execution zone on every record and waives the
Dhan half offline by construction. All 58 recorded `out_8_dhan.json` artifacts
read `precision_source: "daily_tier1"`, `waived: true`,
`waived_reason: "dhan:not-requested"` — deterministic bytes, which is exactly
what the proof root needs, and also exactly why neither of this stage's two
Tier-2 promises has ever been graded:

* **The intraday refinement** — `anchored_vwap` and `orderblock_support`
  recomputed from 5-minute structure instead of the daily prefix — is never
  computed, so gate 6 grades the daily zone only.
* **`spread_impact_cost_pct` is never populated**, and stage 9 already *reads*
  it: `score_precision` does `float(doc.get("spread_impact_cost_pct") or 0.0)`
  and returns `1 − clip(spread / 0.5)`. Verified by execution, not by reading:

  | payload | score |
  | :--- | :--- |
  | `{optimal_limit_buy: None}` | 0.0 |
  | `{optimal_limit_buy: 99.0, spread: null}` (today's every artifact) | **1.0** |
  | `{optimal_limit_buy: 99.0, spread: 0.05}` | 0.9 |
  | `{optimal_limit_buy: 99.0, spread: 0.90}` | 0.0 |

  **An absent spread earned maximum precision credit.** That is an honesty
  defect on its own (§10.4's rule is that absence is disclosed and
  deducted, never rewarded), and it was the first thing this campaign fixed —
  before a single fixture is collected: landed 2026-10-16, TDD red-first, with
  the seam `TestScorePrecisionNeverRewardsAbsentSpread`
  (`tests/test_9_decision_engine.py`) pinning 0.0 for an absent spread, 1.0
  for a *measured* zero-cost one, and §10.4's waived-source deduction.

So "grade real evidence" means: committed, provenance-carrying 5m bars and
depth snapshots that stage 8 can run over **offline** (byte-deterministic,
V10-compared exactly like every other stage's fixtures), plus a grading
criterion that reads them.

---

## 2. What the broker actually serves (researched 2026-10-16)

| Need | Documented endpoint | Notes |
| :--- | :--- | :--- |
| 5-minute candles | `POST /v2/charts/intraday` | body `securityId`, `exchangeSegment`, `instrument`, `interval ∈ {1, 5, 15, 25, 60}` (**no 10**), `oi`, `fromDate`, `toDate` (`"YYYY-MM-DD HH:MM:SS"`); **5 years** of intraday history; ≤ 90 days per request; response is *parallel arrays* `open/high/low/close/volume/timestamp` (epoch) + `open_interest` |
| Daily candles | `POST /v2/charts/historical` | to the scrip's inception; `toDate` **non-inclusive** |
| Depth snapshot | `POST /v2/marketfeed/quote` | body `{"NSE_EQ": [securityId]}`; depth at `data.<segment>.<securityId>.depth.buy/sell`, **5 levels** of `{price, quantity, orders}`; ≤ 1,000 instruments; **1 request/second** |
| Live depth stream | `wss://api-feed.dhan.co` (Full packet) | 5 levels, 5,000 instruments/connection, ≤ 100 per subscription message; 20-/200-level feeds are separate live streams |

* **There is no historical depth endpoint.** Spread evidence can only ever be
  captured *prospectively*, by polling or streaming, and recorded by us.
* Rate limits: Data APIs **5 req/s and 100,000 req/day**; quote APIs **1 req/s**.
  Documented over-limit errors are `DH-904` and data-API `805`; the docs do
  **not** map them to an HTTP status, so the collector must not hard-code a 429
  assumption (stage 8 already waives on any `HTTPError`, which is the right
  shape).
* Auth: `access-token` header (individual tokens expire in **24 hours**);
  historical candles are **Data APIs** and may require a subscribed data plan
  (`DH-902`; the profile exposes `dataPlan`/`dataValidity`).
  Sources: <https://dhanhq.co/docs/v2/historical-data/>, …/market-quote/,
  …/live-market-feed/, …/full-market-depth/, …/releases/, …/authentication/,
  …/annexure/.

### The gap between that and the shipped client

Each row is closed by recording a **real** fixture and making the client match
it — never by editing a doc to match the code:

| # | Shipped in `scripts/8_dhan_precision.py` | Documented API | Phase 1 landed? |
| :--- | :--- | :--- | :--- |
| host | `api.dhantrade.com` | `api.dhan.co` | ☐ needs a recorded fixture |
| intraday path | `/v2/charts/historical` (the **daily** route) | `/v2/charts/intraday` | ☐ needs a recorded fixture |
| request body | `security_id`, `interval`, `oi` | `securityId`, `exchangeSegment`, `instrument`, `interval`, `fromDate`, `toDate` | ☐ needs a recorded fixture |
| candle response | a `candles` list of dicts/tuples | parallel OHLCV arrays + epoch `timestamp` | ☑ parser accepts the documented parallel-array shape, pinned by a mock (epoch read in IST) |
| depth request | `fetcher(url, {}, {})` — no auth, no security id | `access-token` header + `{"NSE_EQ": [securityId]}` | ☑ client sends the documented auth + body (mock-pinned) |
| depth response | `data.<id>.bid` / `.ask` scalars | `data.<seg>.<id>.depth.buy[0].price` / `sell[0].price` | ☑ `spread_from_quote` prefers the 5-level book, flat scalars as fallback (mock-pinned) |

**Phase 1 (offline, landed 2026-10-16)** closed the three rows it can close
without a broker in the loop: the client now speaks the documented request and
parses the documented response shapes (mock-pinned seams), and stage 8 runs
offline over recorded evidence via `--intraday-fixture` / `--depth-fixture`,
which the validation chain can therefore pass without touching the network —
the daily tier stays the fallback when a fixture is absent and every half that
did not run is named in `waived_reason`. `.env.example` carries the two
credential keys (`.env` is gitignored; the token never enters a fixture,
provenance block or log).

**What Phase 1 cannot close** is the first three rows: a mock can pin a shape,
but only a **recorded real** response proves the host, path and body are the
ones Dhan actually serves — "never by editing a doc to match the code." That
closes in Phase 2 (backfill) once the operator's `--live` run records the first
real fixtures; it needs `DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN`, which Phase 1
was deliberately run without.

---

## 3. Scope: what gets collected, and what honestly cannot

* **62 proof rows, 58 dataset records, 31 distinct symbols.**
* **Retention boundary:** 57 of 62 rows have `T_rec ≥ 2021-10-16` (five years
  before today). The five older rows — `SCEN-001`, `SCEN-002` (2020-03, Covid)
  and `SCEN-009`, `SCEN-010`, `SCEN-011` (2019) — are **outside the documented
  intraday retention**. They keep the daily tier and gain an explicit
  `missing_inputs: dhan:intraday:outside-retention` reason: disclosed, never
  proxied, never silently waived. (Phase 0 probes the real boundary — the docs
  once said "5 trading days", so the claim is verified, not assumed.)
* **Point-in-time split, which is a schema decision (§7 Q-…), not a campaign
  side-effect.**
  * **Input side** — 5m bars for sessions **≤ T_rec** (the re-anchoring window):
    `replay_inputs.intraday_fixture`, V3 path-checked, V4/V5 hash-frozen, V7
    look-ahead scanned (every bar ≤ T_rec), V10 byte-compared.
  * **Outcome side** — the T_rec session's post-entry bars and the forward
    window (intraday MAE, first-touch fill simulation): `scoring.intraday_tape`,
    which **must not** appear in `replay_inputs` (V6 blindness) and is read only
    by `$derive`-style rules, exactly like the daily forward tape.
* **Depth is prospective-only.** Backfilled rows get
  `spread_impact_cost_pct: null` with `dhan:no-depth-history`; real spread
  evidence starts the day the capture starts (§4 Phase 3).

---

## 4. Phases

### Phase 0 — preflight probe (operator run, no writes)

One session, one script run, prints and commits nothing but a disclosure:

1. Profile → `dataPlan` / `dataValidity` (is the Data API even entitled?).
2. One `/v2/charts/intraday` request per vintage — 2022, 2023, 2024, 2025 —
   plus **one pre-2021 negative control** (`SCEN-009`, 2019-01-25) to *measure*
   the retention boundary rather than trust it.
3. One `/v2/marketfeed/quote` request for one roster symbol; print the raw
   depth shape (this is what the parser will be pinned to).
4. Report status codes and the two response shapes. Fails loudly on any
   unexpected shape; writes nothing.


**Tool (landed 2026-10-16):** `tests/evals/dhan_preflight.py` —
`python tests/evals/dhan_preflight.py --security-id <id> > preflight.json`. It
derives the vintages from the proof manifest itself (the earliest session of each
year the roster pins, with the pre-2021 rows as the control), probes the URLs
*stage 8 uses* through stage 8's own candle parser and `spread_from_quote`,
throttles itself to the documented limits (<= 5 req/s data, <= 1 req/s quote),
writes nothing, and never prints the token. Exit codes: 0 = disclosure produced
(a vintage the broker does not serve is a *result*, with its HTTP status), 2 =
`ERR_NO_CREDENTIALS` (nothing attempted), 3 = `ERR_UNEXPECTED_SHAPE` (a 200 whose
body the client cannot read stops the run — loud, and still disclosed on stdout).
Seam: `tests/test_dhan_preflight.py` (9 tests, all offline over an injected
fetcher). **Not yet run against the broker:** this workspace holds no
`DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN`, so the run fails closed and no disclosure
is committed until the operator runs it — a mock can pin a shape, never a
reachability finding.

### Phase 1 — make the client match the documented API (offline-verifiable)

1. Correct host, path, body keys, response parsing (parallel arrays + epoch
   timestamps) and the depth request/response per §2 — behind the existing
   `--live` gate, with the **waiver tri-state and the `dhan_cache.db` 15-minute
   TTL contract unchanged**.
2. Add `--intraday-fixture` / `--depth-fixture` so the stage runs offline over
   recorded evidence the way every other stage does; `--live` remains the only
   network path, so the validation chain stays deterministic and V10 keeps
   byte-comparing.
3. **`score_precision` honesty fix (TDD, red first — landed 2026-10-16):** an absent spread scores
   `0.0`, and a waived source is deducted from `max_possible_score` per §10.4 —
   never `1.0`. This is independent of every later phase and landed first.
4. `.env.example` with `DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN` (TICK-008's open
   item), `.env` gitignored; the token never appears in fixtures, provenance
   blocks, logs or chat.

### Phase 2 — backfill collection (operator run, rate-limit aware)

* **Roster:** the 57 reachable rows.
* **Per row:** (a) the ≤T_rec input window — `N_INPUT_SESSIONS = 10` sessions of
  5m bars before T_rec, enough to re-anchor AVWAP and the demand block at 5m
  resolution; (b) the outcome window — T_rec session + forward bars **until the
  first stop/target touch or 22 sessions** (pruning rule: the tape is scanned
  first-touch, so bars after resolution carry no information and no bytes).
* **Volume:** 57 rows × 23 session-days ≈ 1,311 session-days ≈ 98k 5m bars
  (~12 MB) before pruning, ~5–7 MB after. Committed under `tests/proof/fixtures/<id>/`
  like every other fixture, hashed by `manifest.sha256`.
* **Request budget:** ≤ 90 days per request ⇒ roughly 31 symbols × 15–30 ranged
  requests = **450–900 requests**, minutes of wall clock at ≤ 5 req/s, far under
  the 100k/day ceiling. The collector prints its request count and aborts if the
  preflight failed.
* **Tool:** `tests/evals/collect_dhan_fixtures.py` — dry run by default,
  `--apply` writes, one provenance block per fixture (`origin: recorded`,
  endpoint, the exact request body, `fetched_at`, and the symbol→`securityId`
  entry with its source), refuses to write an input-side bar whose timestamp is
  > T_rec, and re-freezes hashes + baseline (V12) exactly like the other seeding
  tools.
* **`securityId` map:** 31 symbols, sourced from Dhan's instrument/symbol list
  and committed with a per-entry source — the `SECTOR_INDEX_MAP` discipline:
  an unmapped symbol keeps the daily tier and says so, never a guessed id.

### Phase 3 — prospective depth capture (the only way spread can ever grade)

* A market-hours collector polling `/v2/marketfeed/quote` at ≤ 1 req/s (or the
  WebSocket Full packet, 100 instruments per subscription) for the roster
  symbols, writing timestamped depth snapshots to
  `tests/fixtures/dhan/depth/<YYYY-MM-DD>/<SYMBOL>.json` with the same
  provenance block.
* Cadence is a decision (§7 Q4): recommendation is **one snapshot at advisory
  time plus every 5 minutes for the first hour**, because that is the window
  gate 6's fill logic actually lives in.
* These snapshots are *inputs* to a spread metric, so they follow the same
  point-in-time rule: a snapshot taken after the advisory time is outcome-side.

### Phase 4 — grading (decisions, §7)

* Intraday-refined zone vs daily zone MAE (the comparison [BRD §13.2](../../BRD.md)
  already lists as an open line), and a spread/impact distribution per class.

---

## 5. Invariants this campaign must not break

1. **Free-first (ADR-003):** Dhan stays the Tier-2 precision fallback. No
   daily-path behavior may start depending on a fixture this campaign did not
   collect.
2. **`--live` never enters the validation chain**; every committed fixture makes
   stage 8 byte-deterministic and V10-compared.
3. **E1/E4:** recorded or disclosed-absent, never typed. A missing fixture
   waives with its reason; an unreachable row says *why* it is unreachable.
4. **V6 blindness:** outcome-side bars live under `scoring`, never
   `replay_inputs`; V7 scans the input side for look-ahead.
5. **Rate limits and token lifetime are collection facts**, enforced by the
   collector (≤ 5 req/s candles, ≤ 1 req/s quotes, bounded totals), not by hope.
6. **No secrets in the repo, the fixtures, the provenance or the logs.**

---

## 6. Acceptance criteria for the campaign itself

- [ ] Preflight report committed as a disclosure: per-vintage reachability, the
      measured retention boundary, the data-plan status, and one real depth
      payload shape.

      *Tool landed 2026-10-16 (`tests/evals/dhan_preflight.py`; seam
      `tests/test_dhan_preflight.py`). It fails closed without credentials —
      verified in this workspace: exit 2, `ERR_NO_CREDENTIALS`, nothing written —
      so this criterion stays open until the operator's own run produces a real
      disclosure. Nothing was invented to fill it in.*
- [ ] ≥ 1 reachable row carries `fixtures/<id>/intraday_prefix.json` with every
      bar ≤ T_rec, hash-frozen and byte-compared by V10 on a fresh offline run.
- [ ] Stage 8 offline over that fixture emits `precision_source: "dhan_intraday"`
      with a non-null `anchored_vwap` / `orderblock_support` computed from the
      5m bars (and the daily tier stays the fallback when the fixture is absent).
      *Fixture-mode plumbing landed 2026-10-16 (Phase 1): `--intraday-fixture`
      and `--depth-fixture` make stage 8 refine offline over a recorded
      response with zero network, daily tier still the fallback, every
      non-run half named in `waived_reason` — seams
      `TestOfflineTier2Fixtures` in `tests/test_8_dhan_precision.py`. This
      stays open until it runs over a **recorded real** fixture (Phase 2), the
      only thing that closes the host/path/body rows of §2's gap table.*
- [ ] `score_precision` seam: absent spread → **0.0** (red first), and no
      artifact scores full precision credit for absence.
- [ ] ≥ 1 depth snapshot fixture recorded; the spread computed from
      `depth.buy[0]` / `depth.sell[0]` of the documented shape.
- [ ] The 5 out-of-retention rows carry `dhan:intraday:outside-retention` in
      their artifacts.
- [ ] Full invariant suite green; gate 6 re-banked deliberately if its numbers
      move (`run_evals.py --re-freeze-gates`), `--regression-exit` clean.
      *Phase 1 landed with the full suite green (97 consumer tests across
      `test_proof_manifest` / `test_9_decision_engine` /
      `test_8_dhan_precision` / `test_run_pipeline` / `test_chain_contract`):
      the daily-tier `out_8_dhan.json` bytes are unchanged, so V10's
      byte-compare still passes and gate 6's number did not move. Re-banking
      becomes relevant only when Phase 2's recorded fixtures change what the
      artifact claims.*

---

## 7. Decision points for the owner

| # | Question | Recommendation | Signed |
| :--- | :--- | :--- | :--- |
| Q1 | Where does intraday-refined grading live? | A **disclosed sub-criterion of gate 6** (same limit object, BRD §13.2's open comparison line) rather than a new gate | ☑ 2026-10-16 |
| Q2 | The spread metric: honesty fix now, threshold later? | Fix `score_precision`'s 1.0-for-absence immediately (no threshold needed); publish the spread distribution per class before any pass/fail number is attached | ☑ 2026-10-16 |
| Q3 | The 5 out-of-retention rows, and pre-capture spread on every backfilled row | Disclosed ungraded on the daily tier with the reason named — never proxied, never back-filled from a different symbol or date | ☑ 2026-10-16 |
| Q4 | Depth capture cadence and roster | Advisory-time snapshot + every 5 minutes for the first hour, roster symbols only, ≤ 1 req/s (WebSocket alternative if the roster grows) | ☑ 2026-10-16 |
| Q5 | Point-in-time schema split for the new fixtures (§3) | Input side = `replay_inputs.intraday_fixture` (bars ≤ T_rec, V3/V4/V5/V7/V10), outcome side = `scoring.intraday_tape` (post-entry, V6-blind, `$derive`-read only) — the same split every other tape already obeys | ☑ 2026-10-16 |
