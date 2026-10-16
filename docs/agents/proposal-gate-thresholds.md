# Proposal — The BRD §5.2 Gate Thresholds (Brier ≤ 0.18, Coverage ≥ 80%)

**Status:** PROPOSED — awaiting the owner's decision (ADR-004: thresholds are the
owner's frozen call, never the runner's). Nothing below moves any number; it
presents the measured case and the options. Every figure is traceable to
committed bytes (`models/lgbm_direction_v1.calibration.json` → `provenance`,
`tests/run_evals.py` coverage line, `docs/agents/evals.md` §2) per the
Zero-Hallucination eval.

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
| Q1 — Brier ≤ 0.18 ceiling | keep / re-scope to ___ / retire in favour of calibration-relative ___ | ☐ |
| Q2 — Coverage ≥ 80% floor | keep (bar path) / move to close path / lower to ___ | ☐ |
| Q3 — Graded set for dims 4–5 | keep (tail-only) / representative + tail slice / representative only | ☐ |

Until this page is signed, `docs/agents/evals.md` §3 #4–#5 and BRD §5.2 stand
exactly as frozen, the runner keeps publishing the reds, and
`tests/proof/baseline_scorecard.json`'s `gates` block keeps tolerating exactly
them (`--regression-exit`).
