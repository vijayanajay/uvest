# Proposal — The BRD §5.2 Gate Thresholds (Brier ≤ 0.18, Coverage ≥ 80%)

**Status:** APPROVED — **signed 2026-10-16 by the owner's command** ("Sign the
Brier/coverage threshold proposal's Q1-Q3 and run the recommended option C
representative-set campaign"). The frozen decision follows the recommendation
below: **Q1 keep the Brier ≤ 0.18 ceiling** (re-scoping before measuring on a
representative set would tune the bar to a tail), **Q2 keep the coverage
≥ 80% floor**, **Q3 re-scope the graded set to representative + tail slice**
(option C: the crisis rows stay in the denominator as a disclosed tail slice;
the roster's clean / inverted-rr rows join the graded set behind recorded
stage-4 evidence). Options B (threshold re-scope) and D (event redesign)
remain open for a later decision exactly as §4 sequences them — this
signature authorizes C's campaign, not a threshold edit. Every figure below
was reproducible from committed bytes at signing time (each record's recorded
`out_8_dhan.json`, its pinned forward tape, and `dataset_refs`'s frozen MAE
rules; the harness reproduced the banked readings before scoring any
alternative).

---

## 1. The question

Eval dimensions 4 and 5 have never passed on any shipped model, before or after
the corpus was sourced, corrected and re-measured five times (labels, panel,
pools, feature runs, gate diagnosis). The runner's verdict has been **published
red by policy** throughout, and the pre-commit hook is now baseline-aware so
the reds are banked rather than bypassed (evals.md §4). That settles *how* to
live with the reds; it does not settle *whether the thresholds measure what
they were written to measure*. Three sub-decisions:

* **Q1 (Brier ceiling 0.18).** Keep it, re-scope it, or retire it in favour of
  a calibration-relative statement.
* **Q2 (Coverage floor 80%).** Keep it on the bar path, move it to the close
  path, or lower it to the nominal level of the object it grades.
* **Q3 (Graded-set composition).** Keep gates 4/5 graded over the catalog's
  crisis/hazard tail, or re-scope the graded set to be representative of the
  corpus the pools come from.

## 2. The evidence (measured 2026-10-16, `--cv-only` over 10,869 gap-free rows)

**Dimension 4 — direction.**
* Out-of-fold Brier **0.2630** against a no-skill base rate of **0.2447** at the
  corpus's own up-share (0.5726): the model is *slightly worse than a constant
  predictor*. Out-of-fold accuracy 37.65% against a 39.76% majority-class share.
  Per-fold Brier 0.248–0.340 (`provenance.cv.per_fold`).
* Gate 4 grades six calibrated predictions, all on the catalog's crisis/hazard
  tapes: **0.483** against ≤ 0.18. The booster is confidently bullish
  (P(up) 0.60–0.84) into five down-tapes — a tail sample of the corpus's own
  error, concentrated, not a second measurement of skill.
* Conclusion the numbers support: **22-session daily-bar direction with the
  frozen 13-feature vector carries no signal on this corpus.** The ceiling of
  0.18 presumes a model that beats its base rate; no such model has existed
  here.

**Dimension 5 — range coverage.**
* Out-of-fold containment of the shipped envelope: **76.02%** bar path (the
  realization the gate grades), **82.11%** close path, **89.23%** terminal,
  **77.10%** pooled-block; sector-conditioned pools on 99.72% of rows. A
  `P10/P90` object is *nominally* an ~80% interval: on the bar path the corpus
  reads just under the nominal, on the close path just over.
* Gate 5 grades the same envelopes against six crisis tapes: **0.0%**, 6/6
  miss, each miss now self-diagnosing (`1.7th`–`12.5th` percentile adverse,
  `99.9th` favourable, against the exact pool its cone read). Close-path
  grading of the same six: **0/6** as well. The red is a property of *which
  tapes are graded* (tail events: −43.48%, −17.57%, −17.05% against a
  nine-year sectoral distribution whose own `p10` is ≈ −10%), not of the
  envelope's construction.

**The one sentence that frames every option:** the thresholds were written for
a *skillful* model graded over a *representative* set; the shipped state is an
unskillful model (measured) graded over an *adversarial* set (curated by
design — the 58-trade catalog exists to trip breakers).

## 3. Options

| # | Option | Effect on the scorecard | Effort | Tradeoff |
| :--- | :--- | :--- | :--- | :--- |
| **A** | **Keep both thresholds and the graded set untouched.** The reds stay published and banked; the hook tolerates them by design. | Permanently red dims 4–5 (dims 1–3, 7 stay green; 6 grades on its own evidence). | Zero. | The scorecard never reads SUITE PASSED; the red stops being news and becomes wallpaper. Honest, but it forgoes the question. |
| **B** | **Re-scope the thresholds to measured reality** (e.g. Brier ≤ 0.25 ≈ base rate; coverage ≥ 75% bar path — the nominal level of the object). | Dims 4–5 could pass on the current corpus. | Small (BRD §5.2 amendment). | Lowering a bar to what the model does is the anti-pattern evals exist to stop; the ceiling would certify skill the model does not have (0.2630 > 0.2447). Only defensible if reframed as *tracking* limits, not skill claims — and then it is no longer a pass/fail gate. |
| **C** | **Re-scope the graded set to be representative** — a disclosed benchmark-authoring campaign adding in-window non-crisis records (the roster's clean/inverted-rr rows already carry pinned tapes inside `row_window`) to gates 4/5, keeping thresholds. | Gate 5 should approach the OOF 76–82% band; gate 4 moves toward 0.26–0.35 — likely still red at 0.18. | Medium (authoring + evidence recording per the frozen campaign rules; no spec change). | The crisis tapes must stay in the denominator (they are real tail evidence) or be explicitly re-scoped as a *separate tail gate* — a smaller graded set cannot quietly become the whole claim. |
| **D** | **Re-scope the event** (BRD §5.2 amendment + relabel + retrain): 22-session up/down carries no signal; the artifact already computes `prob_target_hit_22d` (the target-versus-stop race), and shorter horizons are one relabel away. Grade dimension 4 against the event the engine actually prices. | Unknown until measured — the trainer is a one-command re-run, so the experiment is cheap even if the result is another honest red. | Medium (label rule change, re-record, re-measure). | Changes what Core Prediction 1 *means*; every doc that says "direction (1-month)" moves with it. |

## 4. Recommendation (the runner's opinion, not its call)

**C now, D next, A as the interim state; B only if C and D together leave the
thresholds unattainable.**

1. Run C as a disclosed campaign: gates 4/5 grade a corpus-representative set
   (crisis rows retained and reported as their own tail slice, as gate 2 does
   per class). This answers "is the envelope/skill claim true for the
   distribution?" without touching a frozen number.
2. If C's representative grading still shows no skill (the base-rate Brier
   says it will), open D: the *event* is what needs redesigning, and the
   evidence gathered here is the justification, not the thresholds' failure.
3. B is the last resort and should be recorded as a redefinition ("tracking
   limits") rather than a relaxation — with a BRD amendment saying so.

## 5. Decision points

| Question | Decision | Signed |
| :--- | :--- | :--- |
| Q1 — Brier ceiling 0.18 | **keep** (as interim; re-visit after C's measurement; D next if C shows no skill) | ☑ 2026-10-16 |
| Q2 — Coverage floor 80% | **keep** (bar path, as frozen) | ☑ 2026-10-16 |
| Q3 — Graded set for dims 4–5 | **representative + tail slice** (option C campaign) | ☑ 2026-10-16 |

Until this page is signed, `docs/agents/evals.md` §3 #4–#5 and BRD §5.2 stand
exactly as frozen, the runner keeps publishing the reds, and
`tests/proof/baseline_scorecard.json`'s `gates` block keeps tolerating exactly
them (`--regression-exit`).

---

## 6. Outcome — option C run, 2026-10-16 (same day, after signing)

**The campaign.** `tests/evals/seed_context_records.py --apply` authored one
`uvest-market-context/1` bundle per eligible roster row from the committed
tier-1 index corpus (`tests/fixtures/history/indices_daily.json`, `origin:
recorded`, every series point inside `[first session of the row's own OHLCV
prefix, T_rec]`, `close → value` as the only transformation), declared each
where stage 4 reads it, and recorded the stage through
`record_stage4_evidence.py --apply`, which reads the promoted claims *out of
the recorded bytes*. Of the 25 clean/inverted-rr rows: **21 joined the graded
set**; 2 were left unbundled because `SECTOR_INDEX_MAP` pins no sector index
for their symbol (`TITAN`, `BAJFINANCE` — never a guessed index); 2 were left
unbundled because their forward tape runs past the trained corpus window
(`GB-2025-0031`, `GB-2025-0032`) — Q3 signed an **in-window** set, and gate 5's
frozen scope would exclude those tapes anyway. Both exclusions are printed by
the seeder and named in the dataset's `meta.notes`.

**The scorecard, before → after** (same thresholds, same frozen rules):

| Gate | Before (6 crisis tapes) | After (21 representative + 6 tail) | Threshold | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| 4 — Brier | **0.483** over 6 | **0.214** over 27 · slices: representative **0.137**, tail **0.483** | ≤ 0.18 | still FAIL |
| 5 — Coverage | **0.0%** (0/6) | **55.6%** (15/27) · slices: representative **71.4%**, tail **0.0%** | ≥ 80% | still FAIL |

Both gate lines carry the slice counts and scores on every verdict
(`Gate.coverage`, seam `tests/test_run_evals.py::TestGradedSetSlices`), so a
red over the tail can no longer be read as a red over the corpus, or the other
way round. The tail keeps every one of its rows — nothing was removed from any
denominator.

**What the numbers say, stated at their measured strength.**

* The headline moved a long way and is still red, exactly as §2 predicted:
  the red was concentrated in *which tapes were graded*.
* The representative coverage slice (**71.4%**) sits just under the out-of-fold
  **76.02%** bar-path band — the graded set now behaves like the corpus the
  pools came from instead of like its worst 2 %.
* The representative Brier slice (**0.137**) reads *below* the 0.18 ceiling
  while the tail (**0.483**) keeps the headline above it. **That is not a skill
  claim**: 21 curated BUY advisories have an up-skewed label mix, and the
  model's measured skill remains its out-of-fold number (**0.2630** vs a
  **0.2447** base rate — still no signal on this corpus). The slice says where
  the error sits; the out-of-fold number says how much skill exists.
* Per §4's sequencing, C is now measured, so the next decision is **D** (grade
  dimension 4 against the event the engine actually prices, `prob_target_hit_22d`)
  — the evidence above is the justification for opening it, not a reason to
  touch the thresholds.

**Re-bank.** `python tests/run_evals.py --re-freeze-gates` banked the changed
scorecard deliberately (9 gates), `--regression-exit` then reports
`0 regression(s), 0 improvement(s)` and exits 0 — the state the pre-commit hook
runs. The full invariant suite is green (643/643).

---

## Decision D outcome (run 2026-10-16)

D was opened by the paragraph above and signed by the owner the same day with
its three sub-decisions: a **conservative race estimator** (first touch of the
±`RACE_BAND_PCT` band, `stage4.first_touch_outcome`), **race labels at the
reference band** (the only geometry plain-OHLCV corpus rows can state), and
**TIME counted as not-hit** (no row dropped from any denominator).

Executed end to end: booster retrained on `TARGET_FIRST` / `STOP_FIRST` /
`NEITHER` (5,952 / 4,712 / 205 of 10,869 rows); stage-4 evidence re-recorded
and re-promoted for all 27 graded rows with `predicted_probability` now
carrying `prob_target_hit_22d`; every stage-12 report regenerated from those
bytes; gate 4 renamed **Race Calibration Brier Score**. Thresholds and graded
set untouched — same 27 rows, same ≤ 0.180 / ≥ 80% lines.

| Gate | After C | After D | Threshold | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| 4 — Race Calibration Brier | 0.214 (rep 0.137 / tail 0.483) | **0.376** (rep **0.435** / tail **0.169**) | ≤ 0.18 | still FAIL |
| 5 — Coverage | 55.6% (rep 71.4% / tail 0.0%) | **51.9%** (rep **61.9%** / tail **16.7%**) | ≥ 80% | still FAIL |

Stated at measured strength: the model's out-of-fold race Brier is **0.3268**
against a **0.2477** base rate at a **0.5476** race-win share — the relabel
moved the event to the one the engine prices, and the skill verdict is
unchanged (none). The one structural cost is the sector pools, thinned by the
race classes (`oof_sector_pool_share_pct` **99.72% → 32.21%**, cone OOF
bar-path **77.3%**), disclosed in `provenance.corpus.sector_blocks` and
ADR-011 decision 15 rather than tuned back. With C and D both measured at
frozen thresholds, B and A remain the documented fallbacks per §4.
