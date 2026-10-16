# 2026-10-16 — Gap-free feature runs, and a coverage gate that diagnoses itself

Two owner commands, both data-contract rather than threshold work (ADR-011 lineage).

## 1. Feature histories may not span a calendar gap (ADR-011 decision 14)

**Decided:** extend decision 11's within-vintage rule from the *label* side to the
*feature* side — measured cost first, on the committed corpus (11,279 candidate
rows): gap-free runs cost **398 rows** (199 each of RELIANCE and SBIN, the two
symbols whose vintages jump `202003 → 202204`); the alternative readings (stale
windows bridging the hole) were rejected as violations of the same data contract.

**Implemented:** `feature_window_dates` in `tests/evals/train_direction_model.py`
— a row builds only when its 200-session history, ending at its own session,
lies inside a single gapless run of the merged series (overlapping vintages
chain; a hole no fetch covers ends a run). Dropped rows counted in
`provenance.corpus.feature_window`. Seam:
`tests/test_train_direction_model.py::TestFeatureHistoryNeverCrossesARun` (5 —
including the cross-asset beta/correlation window, which is run-bound because
`common_return_windows` intersects with the run-restricted prefix).

**Effect:** corpus 11,267 → **10,869** rows; OOF Brier 0.263231 → **0.262975**
(base 0.244724); cone containment 76.51/82.41/89.30 → **76.02/82.11/89.23**
bar/close/terminal, pooled 77.32 → **77.10**; sector share 99.73 → **99.72%**;
sector block atoms moved (energy 1,119 → 920, bank 3,700 → 3,501 — the dropped
rows were in those two sectors' histories). Stage-4 evidence re-recorded (6
rows); suite **590/590**; gates: 4 **0.483** (was 0.533), 5 **0.0%** (6/6 miss).

## 2. Gate 5's red carries its own diagnosis

**Decided:** for each cone miss, print where the realised excursion sits in the
percentile of the pool *that record's cone actually read* — its sector block or
the pooled fallback, named either way.

**Implemented:** `run_evals.pool_percentile` + the coverage line in
`gate_coverage`; reads the calibration bytes stage 4 ships (through
`stage4.samples_for`, so the diagnosis follows the shipped selector, never a
parallel copy). Seam: `tests/test_run_evals.py::TestConeMissDiagnosis` (7:
low/high-side, pooled fallback named, covered row ⇒ no diagnosis, no calibration
⇒ old sentence, entry-less miss, shipped calibration on a real record).

**Fresh per-miss line (all six miss):** SCEN-001 high +63.17% = 99.9th pct
(^CNXENERGY favourable, floor +12.81%); SCEN-002 −8.35% = 12.5th (^NSEBANK);
SCEN-003 −17.05% = 1.7th (^NSEBANK, floor −6.26%); SCEN-004 −17.57% = 4.5th
(^CNXINFRA); SCEN-006 −8.69% = 11.3th (^NSEBANK); SCEN-008 −43.48% = 1.8th
(^CNXINFRA, floor −9.94%). Also verified: close-path grading still leaves all
six at 0/6 with the fresh cones.

## Ruled out / rejected

- Keeping hole-bridging feature rows (stale 200-session windows) — a feature
  computed across a hole is not a measurement of 200 sessions.
- Rewording the red instead of computing the percentile (the old prose
  percentile table was a side calculation; now the runner prints it from bytes).

## Watch

- Any retrain moves per-fold coverages and the six records' cone bounds — the
  numbers in evals.md §2, TICK-004, BRD §5.2 and actionplan are re-measured
  together; a partial update is a zero-hallucination violation.
- `pooled_block` lives under `provenance.corpus`, while
  `oof_cone_pooled_coverage_pct` lives under `provenance.cv` — easy to grep the
  wrong one.
- Corpus row count is now 10,869; the historical 11,267/6,294 figures stay in
  dated entries only (actionplan decision entries keep their own-time numbers
  with an appended re-measure note).
