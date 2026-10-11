# UNDERSTANDING.md — What UVEST Is and What It Must Be
*The owner's philosophy, written down so no future decision can silently abandon it.*

---

## 1. The Owner's Actual Requirement (validated against this repo)

The owner rejected the current spec on four specific points:

1. **The BRD never mentions the user having a hit rate or a probability framework at all.**
   The system's only job is to score a *recommendation*, not to model what *the user
   actually does*. If the user makes money 55% of the time, the system's entire role
   becomes: evaluate the *incremental* tips only as a **(filter → edge-magnifier →
   acting tool)** — reinforce tips when they match the user's proven edge, reject
   only the small minority the user is bad at, and tell the user **where price may
   go after entry, when it is going well, when it is going badly, and where to
   exit**. A recommendation given by a "senior analyst" (with F&O background) that
   wins ~60% of the time has **no maths problem** — the system's job is to amplify
   that person's edge, not to police it.
2. **The system must not reject trades wholesale.** Current spec: any breakdown of
   technicals, fundamentals or flow with a below-average reading contributes such a
   heavy negative weight that the trade is discarded despite the fact that *every
   trade is weak on *some* dimension*. The system shall understand **why** a
   dimension is low, understand its **complementary interplay** with the other
   dimensions, and only when the composite picture is genuinely negative-expectancy
   show it the door — telling the user **why**, at what % probability, and what data
   point would **make it acceptable**.
3. **The system must follow the trade after entry.** Daily triage, pre-defined at
   entry: "is the trade going good or bad, and should it be cancelled / soft-exited?"
4. **The system must prove itself by running itself on the past.** A frozen suite of
   business tests that replay real historical recommendations with **no look-ahead
   bias** — point-in-time only — and produce a confusion matrix of TP / FP / TN / FN.

The owner's calibration is stated plainly:

> **"If the probability of making money is even >55%, we go — but the system
> tracks every confirmed trade day by day and says honestly whether the edge
> still stands."**

---

## 2. The Statistical Frame

- **P(win) = 55% with R:R ≈ 1:2 implies expectancy ≈ `0.55 × 2R − 0.45 × 1R = +0.65R`
  per trade.** This is an enviable edge. The system's job is to *estimate and defend*
  that number, not to hold trades to a higher moral standard than the market offers.
- **The base rate is the anchor.** The recommender's historical hit rate is the
  denominator; the machine adds or subtracts from it. The machine's contribution is
  measured as **conditional improvement**: how much does P(win) change when the
  machine agrees vs. disagrees. That delta is the only number that proves the
  system earns its place.
- **A calibrated probability can be wrong.** It must never be invented. Every
  number shown to the user is `p_win` with a confidence and a premise.

### 2.1 Verdicts are probability statements, not pass/fail grades

The retired BRD triplet (`GO` / `CONDITIONAL_GO` / `NO_GO`), superseded by ADR-006, was too coarse and too final.
Freeze this vocabulary instead:

| Verdict | When used | Required JSON fields |
| :--- | :--- | :--- |
| **GO** | Positive expectancy at the trade's own geometry; risks are known. | `p_win`, thesis, size, risk ledger |
| **GO_WEAK** | Positive expectancy but ≥1 dimension unquantifiable (waived stage). | same + `confidence_deficit` |
| **WAIT** | Edge exists, entry/timing is wrong. Not a rejection. | `recheck_condition` (e.g. `price <= limit_zone`) |
| **AVOID** | Composite picture negative-expectancy *for this framework*. | `avoid_reason`, `what_would_flip_me` |
| **HOLD** | Post-entry: thesis intact, path compatible with target. | daily updated `p_win` |
| **PRESSURE** | Post-entry: thesis intact but path deteriorating (unrealized gains eroding). | `cancel_risk`, `soft_exit_zone` |
| **EXIT** | Post-entry: premise invalidated. | `exit_zone`, `invalidation_reason` |

**Every non-GO verdict carries `what_would_flip_me`.** A verdict without a flip
condition is rejection without education — exactly what the owner forbids.

**Prohibited:** treating any single low score as justification for rejection. Low
scores must be *explained*: "delivery% is at −2σ but OI buildup is +8% over 3 days —
conflicting; that combination historically resolves X% of the time."

---

## 3. Daily Stewardship — the trade becomes the system's responsibility

Once the user confirms entry, the day-1 thesis is **frozen** in `uvest_history.db`:
verdict, p_win, premises, kill levels, target geometry. Each day:

1. **Premise check.** Was the *reason the trade was taken* still true? Stronger or
   weaker than yesterday? Each premise gets a direction (`reinforced|intact|weakening|broken`).
2. **Re-estimate P(win)** — Bayesian update of the frozen prior with the day's
   evidence. Answer honestly: "still >55%? now 48%? now 70%?"
3. **Publish the tri-state:** `HOLD` / `PRESSURE` / `EXIT`, and on PRESSURE/EXIT
   state exactly one of: *soft exit* (exit today, risk reduced), *tighten* (stop to
   yesterday's low, re-score tomorrow), *stop exit* (thesis dead, take the hit).
4. **No daily re-judging of the entry.** The entry asked "should I take this?" The
   daily question is "should I *keep* this?" — posterior update, not a re-run of the
   entry gate.

**These daily outputs are themselves labeled data points** (predicted p_win vs
realized path) — the training set for stage 11's calibration is built from
stewardship, post hoc autopsies alone are too slow and too coarse.

### 3.1 The daily "can I still make money?" report card (frozen format)

Every confirmed trade gets this exact card, every trading day, until resolution.
No field is optional; missing data becomes `confidence_deficit`, never silence.

```json
{
  "trade_id": "GB-2022-0001",
  "as_of": "2022-04-19",
  "days_held": 6,
  "days_left_of_horizon": 16,
  "still_chance_pct": 61.0,
  "chance_delta_since_entry": +2.0,
  "unrealized_r_multiple": 0.6,
  "premises": [
    {"premise": "delivery accumulation holds", "state": "reinforced",
     "evidence": "delivery 58% vs 20d-avg 34%, z=+3.0"},
    {"premise": "sector holds above 50DMA", "state": "intact", "evidence": "NIFTY IT MRS +1.2"}
  ],
  "cancellation_call": "HOLD",
  "recommended_action": null,
  "action_levels": {
    "soft_exit_zone": 2755.0,
    "tighten_stop_to": 2748.0,
    "hard_stop": 2704.0
  },
  "what_would_cancel_me_tomorrow": [
    "close < 20EMA while delivery% < 30%",
    "sector MRS turns negative",
    "close below anchor VWAP for 2 consecutive sessions"
  ],
  "confidence_deficit": 0.0
}
```

Field semantics:

- `still_chance_pct` — the system's honest posterior P(reach target before stop).
  This is the direct answer to the owner's daily question "is there still a chance
  to make money?" It is a number, never an adjective.
- `chance_delta_since_entry` — decay or build-up of the edge, visible *as it
  happens*, not in a post-mortem. A decay of >15pp from the frozen prior, or the
  posterior dropping below the owner's 55% floor for 3 consecutive sessions,
  auto-promotes the card to `PRESSURE` (with `recommended_action` — the user
  still decides).
- `recommended_action` on PRESSURE/EXIT is exactly one of: `SOFT_EXIT` (book out
  today at market, keep remaining profit / cut remaining risk), `TIGHTEN` (stop
  to yesterday's low, re-score tomorrow), `STOP_EXIT` (thesis dead, exit whatever
  the tape pays).
- `action_levels` are **computed at entry** and carried unchanged until a premise
  explicitly breaks — the card never improvises hindsight levels mid-selloff.
- `unrealized_r_multiple` — profit/loss expressed in initial-risk units so a
  +0.6R position is comparable across every trade regardless of ₹ size.

**These cards are themselves the labeled dataset.** Each day's `still_chance_pct`
vs the realized forward path is one calibration observation; stage 11 learns
from hundreds of these rather than only end-of-trade autopsies.

---

## 4. The Frozen Backtest Suite — the system's business proof

### 4.1 The no-lookahead architecture (non-negotiable)

Every historical recommendation is replayed under the same constraint a live user
faces: **the engine sees only data with timestamp ≤ T_rec**.

1. The trade is reconstructed from the actual tape as it stood on T_rec
   (real close, real ATR, real band).
2. The full pipeline (0 → 9) runs **only** on the ≤-T data slice; the harness
   physically splits each trade into an isolated `content/` (trade + ≤T data) and an
   `outcome/` file (forward tape, joined only after verdict). Verdict process and
   outcome never share a process or a directory tree.
3. The verdict is stamped; the trade plays out on the actual forward tape.
4. A confusion-matrix quadrant plus the money number (expectancy of verdict set)
   is produced. Nothing is synthesized: prices come from bhavcopy / yfinance.

### 4.0 Blindness rule — the engine must not know the final price (frozen)

The owner's exact requirement: *"the system would be unaware of the final price
even though we have that data."* This is enforced structurally, in three layers:

1. **Data isolation at the filesystem level.** Each replayed trade gets its own
   sandbox: `replay/{trade_id}/content/` holds the canonical trade JSON + every
   fixture the pipeline stages read (OHLCV prefix, bhavcopy fixture, fundamentals
   fixture — all truncated at T_rec). `replay/{trade_id}/outcome/` holds the
   forward tape and nothing else. The two directories are written by different
   tool invocations and the scoring step runs **after** every verdict process has
   exited — outcome data physically cannot be in memory when the verdict forms.
2. **Fixture truncation is done by the harness, not the stages.** A stage script
   receives only the truncated fixture path; it has no parameter to ask for "the
   full series". The existing `curate.py` convention (prefix ≤ rec_date, forward
   joined only for `outcome()`) is exactly this pattern extended.
3. **Leakage audit (gate S6's teeth).** After every replay, the scorer re-derives
   each stage's output from the `content/` files alone and diffs it against the
   recorded verdict-stack; any output that requires post-T data to recompute is
   a hard failure of the suite, not of a trade. SHA-checksums of every fixture
   are recorded at verdict time and re-verified at scoring time — a mutated
   fixture invalidates the replay.

### 4.0b How the past recommendations get generated (the analytical-trader scan)

There is no historical archive of Univest cards, so the suite **manufactures each
trade the way a senior analyst would have on that morning** — from real data,
point-in-time, honoring what an advisory actually knew on T_rec:

**Step 1 — The daily scan (recency-selected, exactly as an advisory desk scans):**

- Universe: the liquid NSE F&O names (the same symbol list `curate.py` already
  pins, extendable; ~14 names, ~10 liquid sectors).
- For every anchor date in a frozen calendar (e.g. every 15th of the month,
  spread across 2022–2026 — already the `ANCHORS` list in `curate.py`), scan the
  tape *as of that date only* for setups a real analyst would flag:
  - **Breakout scan:** close crosses 20-day box high on delivery% > 20d-avg +1σ.
  - **Pullback scan:** uptrend stock (above 50&200 EMA) tagging the 20-EMA or
    anchor VWAP with delivery support.
  - **Gap-up follow-through scan:** gap > 1×ATR held above VWAP into close.
  - **Distribution scan:** 5-day high with delivery% < −1σ and OI build-down —
    the analyst *also* screens for what looks attractive but isn't (this is how
    bull-trap and too-good-to-be-true scenarios get authored honestly).
- Every scan hit produces a candidate **exactly as an advisory would publish it**:
  entry = band around T_rec close (±0.1%, the `entry_band()` convention already
  frozen), stop = X·ATR below (X frozen per scenario class), target = R-multiple
  of stop distance, lot size = real NSE contract size.
- **The builder never looks at what happens next.** It uses only prefix data;
  candidate selection is deterministic given the ≤-T tape (same inputs → same
  trade → reproducible forever), so rows can't be silently re-picked after
  seeing outcomes.

**Step 2 — The class quotas (frozen, ~35 trades v1):**

| Class | How rows are *found* by the scan | Quota |
| :--- | :--- | :--- |
| TP-leaning (clean bullish) | breakout/pullback scans in confirmed uptrends, delivery confirmed | 8 |
| FP-targeted (coin-flip edge) | breakout near multi-day resistance OR against weakening sector — *scans that would go out anyway* | 5 |
| Bull-trap / FP factory | strength scans on decaying delivery/OI — the trap a real analyst sometimes also calls | 5 |
| Sector-bleed | pure stock-scans where the sector index was already distributing at T_rec | 4 |
| Hazard-under-green | scans that pass everything except a stage-6 hazard present at T_rec (pledge / CFO-PAT was already public) | 4 |
| Macro-crash window | any scan hit inside frozen stress windows (e.g. Mar-2020, Jun-2022, expiry-VIX-week anchors) | 4 |
| Marginal band | scan hits whose historical analog p_win lands 55–62% — the exact owner threshold | 5 |

**Step 3 — Four-quadrant balance is guaranteed by *forward banding*, not by
cheating.** After replay, the scorer tags each resolved trade by realized
outcome (forward target-first vs stop-first, with the fenced ambiguity rule
"both hit inside the window ⇒ LOSS" already frozen in `outcome()`). The quotas
above are picked so all four quadrants can occur in practice; the *actual*
quadrant per row is whatever the engine's verdict turned out to be. **TP/FP/TN/FN
counts are never manufactured** — the scan manufactures *situations*, the engine
manufactures *verdicts*, and the matrix is the honest cross of the two.

**Step 4 — Reproducibility freeze.** Every authored trade id encodes
`GB-{year}-{seq}`, binds to a recorded `--record` bundle (the repo's existing
byte-exact recording infra), and its SHA-manifest is committed. Any later change
that alters a row's verdict must be a *deliberate re-freeze* (version bump in
`meta.schema_version`), never a silent re-curate.

Already 80% true in this repo: `tests/evals/curate.py` implements exactly this split
(prefix ≤ rec_date for stages, forward slice joined only for scoring, cached fetches,
real OHLCV, deterministic record IDs). It needs to be promoted from *curator of a
golden dataset* to *executor of the business proof*, and extended from stages 0–2 to
the full 0–9 chain.

### 4.2 Confusion-matrix quadrants, defined from expectancy

| Verdict | Outcome | Quadrant | What it measures |
| :--- | :--- | :--- | :--- |
| GO | WIN | **TP** | Engine stamped a real winner |
| GO | LOSS | **FP** | Engine wrong — the *most valuable datapoint in the suite* |
| AVOID | LOSS | **TN** | Correctly avoided |
| AVOID | WIN | **FN** | Engine rejected a winner — *the cost of being too strict* |

- TP + FN ratio tells you if the filter is too tight. FP count tells you if too
  loose. Neither is "bad" — both are **dial readings**.
- WAIT / GO_WEAK are excluded from quadrant scoring but tracked separately: how often
  does a WAIT's recheck_condition trigger into a would-be win?

### 4.3 The scenario variety the suite must contain — designed like an analyst

Include **losing versions of winning trades** — that's the entire point of the
suite. A suite of only winners proves the suite, not the system.

| Scenario class | Signal shape (what engine sees at T) | The trap being tested |
| :--- | :--- | :--- |
| Clean bullish | trend + high delivery + OI buildup + clean fundamentals | Baseline: does it stamp TP? |
| Coin-flip edge | flow says accumulation, technicals near resistance | does the engine price the conflict honestly, or over-reject? |
| Bull trap | rising price, delivery% collapsing, OI shrinking | must AVOID (or WAIT at worst) |
| Sector bleed | stock fine, sector distributing | does macro context earn its weight? |
| Fundamentals hazard | pledge / CFO-PAT inversion, everything else green | hazard discovery must not be drowned out by technicals |
| Everything green, tape crashes | VIX spike, expiry week, FII exodus | macro gate's true positive test |
| Marginal scores (55–62%) | nothing obviously wrong, nothing obviously right | the exact 55% threshold the owner set — does GO here actually make money? |
| Too-good-to-be-true | multi-year best DTW analog match ever + everything perfect | is the engine seduced by its own confidence? |

Scenarios are selected from **real past dates** (anchor dates like
`2022-04-15`, `2023-10-15` are already used in `curate.py` — extend this pattern),
never synthesized. Target: 3–5 per scenario class ≈ 25–40 replayed trades for
version 1 (expand later), chosen at **point-in-time** from the freshest public data.

### 4.4 Frozen gates (decided once — never silently changed)

> Bootstrap floor for v1: **no gate is graded until the suite has ≥ 30 replayed
> trades across ≥ 5 scenario classes.** Before that, gates emit [SKIP]
> (same honesty as the current evals runner), never a fabricated pass.

1. **S1 — Filter delta (the 55% gate):** every replayed trade stamped GO must have
   entered with `p_win > 55%`, and the GO bucket's *realized* win frequency must be
   > 50% — the engine's 55% bar must mean actual above-coin-flip odds after the
   fact. If the engine says 55%+ but the GO bucket realizes 45%, the threshold is
   miscalibrated and the suite FAILS: the owner's rule is honored only if the
   number behind it is honest. Plus: GO bucket expectancy > all-tips baseline.
2. **S2 — Calibration:** |predicted p_win − realized bucket frequency| ≤ 10pp absolute
   per verdict bucket.
3. **S3 — Per-regime honesty:** GO bucket expectancy positive in *each* regime
   bucket (VIX band, sector, cap, week-type). No aggregate-only masking.
4. **S4 — AVOID cost accounting:** FN count and forgone expectancy published per
   regime — how much money was left on the table by rejection.
5. **S5 — Stewardship Value-Add (Cancel superiority):** the daily cards are graded too.
   Evaluated across losing setups ($N \ge 25$) with a $1.0\times\text{Daily ATR}$ noise buffer.
   The counterfactual exit at the card's `soft_exit_zone` (first PRESSURE day) must
   reduce cumulative portfolio drawdown by **$\ge 15.0\%$** compared to hard stop-loss exits
   (net of wrong cancels where the trade recovered), matching BRD §15.3 and `business_test_cases.md` §6.
   If holding to the stop beats the daily card on average, stewardship is noise — this gate is the
   mathematical proof of the "cancel / soft off" value the owner asked for.
6. **S6 — No-lookahead integrity:** blindness per §4.0 — fixture-SHA re-verify +
   prefix-recompute diff. Any leak is a suite failure, not a trade failure.
7. **S7 — Marginal-band honesty:** within the engine-estimated 55–62% p_win band,
   realized frequency must sit inside the calibration band and expectancy must be
   ≥ 0 — the exact "even 55% is enough" edge case, tested in isolation.

### 4.5 The test framework this repo already runs — and how the suite slots in

The repo has two harnesses today; the backtest suite extends them, never forks them.

**Layer 1 — Invariant tests (`tests/run_all_tests.py`, < 300 ms, exit 0/1).**
The `unittest`-based runner discovers `tests/test_*.py` and verifies the frozen
contract layer on every commit (wired into git hooks):

| Existing seam | What it enforces |
| :--- | :--- |
| `test_contract_consistency.py` | BRD §9.1 ↔ `prompt.md` §2 ↔ `docs/tickets/INDEX.md` ↔ ticket files never drift apart |
| `test_prompt_integrity.py` | the master prompt mirrors the frozen contract |
| `test_repo_hygiene.py` | generated artifacts (`out_*.json`, `current_trade.json`, `uvest_history.db`) gitignored; spec artifacts (golden dataset, OHLCV fixtures) committable |
| `test_recorder.py` | the `--record` replay-bundle layout, byte-exact stdout, append-only manifest |
| `test_0/1/2/3/5_*.py` | each implemented stage's frozen math seams (exact values at 4dp) |

For the suite, the suite's own files join this layer:

- `tests/test_replay_harness.py` — sandbox (`content/` vs `outcome/`) is
  structurally isolated; fixture truncation happens at T_rec; SHA manifests
  written at verdict time re-verify at scoring time (gate S6's teeth). Runs in
  Layer 1 (fast, offline, synthetic 50-bar micro-fixtures) on every commit so a
  blindness regression breaks CI immediately.
- `tests/test_scoring_gates.py` — S1–S7 gate arithmetic graded against synthetic
  verdict stacks (fast unit seam for the gate logic itself, separate from the
  slow empirical runs).
- `tests/test_contract_consistency.py` extended with one row: the scenario-class
  quotas table in this document (§4.0b Step 2) must match `meta.class_counts_expected`
  in the scenario manifest — same drift-guard the BRD table already gets.

**Layer 2 — Behavioral evals (`tests/run_evals.py`, golden dataset, offline deterministic).**
Today it grades the 31-record golden dataset with 7 gates that emit `[SKIP]` on
absent inputs rather than fabricating numbers (option-rejection, hard breakers,
zero-hallucination, Brier, cone coverage, limit-MAE, HTML validity). The suite
**replaces this runner's gate list wholesale** with S1–S7 and its dataset model:

| Old gate (evals.md §3) | Fate in the new suite |
| :--- | :--- |
| Option rejection | stays as a stage-0 unit test (Layer 1) — a per-trade verdict decision doesn't belong here |
| Hard circuit breakers | subsumed: composite-negative-expectancy edges surface as S1/S7 failures, structural impossibilities stay Layer-1 |
| Zero hallucination | stays, but the judge changes: report-text-vs-JSON differ, not a curator-typed boolean |
| Brier ≤ 0.18 | becomes S2's calibration band on p_win buckets (same math, honest buckets) |
| Cone coverage ≥ 80% | becomes part of S2 evidence (cone vs realized path) |
| Limit-MAE reduction ≥ 75% | becomes `flip_table` evidence feeding S1; fill-rate reported beside it |
| HTML validity | stays as a stage-12 unit test (Layer 1) |

**Layer 3 — The scenario generator (`tests/evals/curate.py`, promoted).**
It already does the hard part: cached point-in-time yfinance fetches (one fetch
per symbol-month under `tests/fixtures/history/`), prefix ≤ T_rec for the
engine and a strictly-later forward slice for scoring, deterministic record IDs,
byte-exact recorded bundles via `_recorder.py`'s `--record auto` flag (which
writes `tests/fixtures/SYMBOL_DATE/` with an append-only manifest). Promotion
plan, in ticket order:

1. `generate_scenarios` — implement the §4.0b scans (breakout / pullback /
   gap-hold / distribution-looking-strength) over the frozen `ANCHORS` ×
   `SYMBOLS` grid, emit `tests/evals/scenario_manifest.json` with the class
   quotas and per-row provenance (scan rule that fired, fixture SHAs).
2. `replay` — run stages 0→9 per candidate with fixtures truncated at T_rec,
   sandboxed per §4.0; stage-4/6/8 fixtures join as those engines land
   (until then those stages honor the waiver path — the suite still runs, but
   with declared `confidence_deficit`).
3. `score` — join `outcome/`, compute the confusion matrix, expectancy per
   bucket, and grade S1–S7 exactly as `run_evals.py` grades gates today
   (`[PASS]/[SKIP]/[FAIL]`, exit 0/1/2 semantics preserved) into
   `tests/evals/scorecard.json`.
4. `stewardship-sim` — the S5 counterfactual: re-run each stopped-out trade
   day-by-day through the report card (each day's card sees only ≤-that-day
   data), diff the counterfactual soft-exit P&L against the stop exit.

Bootstrap order respects the existing model: Layer 1 stays green on every
commit; Layer 2's runner is *extended* first (SKIP semantics), and Layer 3
fills 3–5 rows per class before any S-gate stops being `[SKIP]`. **The
≥30-replayed-trades / ≥5-class floor is enforced in code** in the extended
runner, not by convention.

What each artifact is for, in one line: Layer 1 keeps the *contract* honest,
Layer 2 keeps the *math* honest, Layer 3 keeps the *business claim* honest —
and the S1–S7 scorecard is the only output with authority to say "ready for
operation".

---

## 5. What changes per stage (nothing is thrown away)

| Stage | Before | Now |
| :--- | :--- | :--- |
| 0–1 (parse, math) | unchanged | same truth statements |
| 2 (ATR/trend) | regime report | also supplies daily re-estimation inputs |
| 3 (DTW analogs) | "win rate" | **analog forward-path distribution at trade's own geometry** → a p_win contribution |
| 4 (LightGBM) | 1 classification | **regime classifier** → historical base rate of this shape, not a vote |
| 5 (flow matrix) | entry scoring | also daily-premise evidence; conflict (score vs flow) must be *quantified*, not averaged away |
| 6 (fundamentals) | hazard rejection | hazard discovery; hazards get a stated p_win *floor*, not just a penalty |
| 7 (macro) | entry scoring | also feeds daily re-estimation |
| 8 (precision) | limit zone | + fill probability for the limit zone (needs it to mean "when would entry be good if I wait") |
| 9 (synthesis) | 100-pt rubric | **p_win + thesis + expectancy + verdict + flip conditions** |
| 10 (daily triage) | zone buckets | **the Bayesian stewardship loop of §3** |
| 11 (post-mortem) | only-on-loss autopsy | calibration trainer fed by *daily* stewardship data, not only resolutions |
| 12 (HTML) | scorecard | probability dashboard: p_win cone, premises and their day-updates, cancel_risk gauge |

### 5.1 Rejection vs. Rescaling of Scores (Reconciled with ADR-005)

Rejection is generally reserved for *composite* negative expectancy or fatal tail-risk hazards.
A single low dimension (e.g. delivery marginally below mean, minor indicator divergence) does not
justify wholesale rejection; it lowers `p_win` by its confidence-derived contribution and may trigger
`WAIT` if an optimal entry limit or flip condition is achievable.

However, as formally resolved in **ADR-005 (domain.md)**, **un-exitable catastrophic hazards cannot be "priced"
into a continuous expectation**. In Indian equities and futures, when promoter pledge exceeds $25\%$,
lender collateral dumping triggers cascading lower circuits with zero bid liquidity in cash and broker RMS
fire-sale liquidations in futures. The theoretical stop loss cannot execute on the real tape. Therefore,
Promoter Pledge $>25\%$ (along with F&O Ban, Inverted R:R $<1.0$, Options ingestion, and Futures binary earnings $<7\text{d}$)
is retained as an absolute hard safety circuit breaker (**`🔴 AVOID`**).

To preserve transparency without violating market physics, the engine never emits a silent rejection:
every `AVOID` explicitly reports the hazard diagnostic and emits an actionable `what_would_flip_me`
(e.g., *Promoter de-pledging below 25% or structural debt restructuring*).

---

## 6. The Operating Principles (freeze)

1. **Hard AVOID on Fatal Circuit Breakers (ADR-005).** Un-exitable tail risks (Promoter Pledge $>25\%$,
   F&O Ban, Options, Binary Futures Earnings $<7\text{d}$) trigger hard `AVOID`. Every other dimension is
   continuously priced into $P_{\text{win}}$ and carries an explicit `what_would_flip_me` condition.
2. **Confidence is reported with its absence.** Missing data lowers confidence —
   `confidence_deficit` states it; no silent penalty, no silent pass.
3. **Every verdict carries a flip condition**; nothing is a bare rejection.
4. **No recommendation is too small to be tracked.** A confirmed trade is owned,
   daily, until resolution.
5. **The engine wins by delta, not by volume.** Fewer, honestly-calibrated opinions
   beat more hedged ones.
6. **Numbers first, context second, never a naked number.** A number without a
   confidence, a premise, and a flip condition is a hallucination wearing a lab
   coat.
7. **The engine may be wrong — never *unaware*.** Wrong-but-calibrated is fixable;
   wrong-but-silent is a design failure.
8. **>55% is the owner's threshold, not a moral standard.** GO at 56% is GO; the
   stewardship then tells the truth daily about whether the edge still stands.

---

*Document Precedence & Authority:*  
This document captures foundational design philosophy and domain rationale. In accordance with the
Matt Pocock engineering lifecycle (AGENTS.md), formal engineering specifications, mathematical schemas,
verdict state models, and circuit breakers are frozen in **[BRD.md](file:///d:/Code/uvest/BRD.md)** and
**[domain.md](file:///d:/Code/uvest/docs/agents/domain.md)** (ADRs), while empirical regression verification is
frozen in **[business_test_cases.md](file:///d:/Code/uvest/business_test_cases.md)**. In any conflict, the frozen
ADRs, BRD.md, and business_test_cases.md take precedence.
