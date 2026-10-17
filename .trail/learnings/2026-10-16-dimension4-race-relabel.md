# 2026-10-16 — Decision D: dimension 4 relabeled onto the target-vs-stop race (+ stage 9 score_precision)

## What was decided

- **Stage 9 `score_precision`** (TDD, seam first): an *absent* spread now
  scores **0.0**, not 1.0 — a missing measurement cannot earn full credit.
  Seam: `tests/test_9_decision_engine.py::TestScorePrecisionNeverRewardsAbsent…`.
- **Decision D executed** (option D of `docs/agents/proposal-gate-thresholds.md`,
  three sub-decisions signed in session):
  1. **Conservative race estimator** — `stage4.first_touch_outcome`, first
     touch of the ±`RACE_BAND_PCT` (3%) band. It is now also what
     `tests/evals/dataset_refs.resolution_of` aliases, so corpus label,
     benchmark resolution, curator seed scan and scaffolder tape comparison
     share one scan and cannot disagree.
  2. **Race labels at the reference ±3% band** — the only geometry plain-OHLCV
     corpus rows can state (no target/stop in the corpus).
  3. **TIME counted as not-hit** — no row dropped from any denominator.
- Booster classes: `TARGET_FIRST` / `STOP_FIRST` / `NEITHER` =
  **5,952 / 4,712 / 205** over 10,869 rows; calibration key
  `prob_target_hit_22d`; gate renamed **Race Calibration Brier Score**.

## What was done end to end

Trainer + stage 4 + tests (40 trainer / 54 stage-4 seams green) → booster
retrained → all **27** stage-4 artifacts re-recorded and re-promoted
(`record_stage4_evidence.py`) → every stage-12 report regenerated from those
bytes (57 stale renders on disk were gitignored generated state, rebuilt) →
scorecard re-measured → docs (evals.md, BRD §5.2, domain/ADR-011 decision 15,
TICK-004, actionplan D item, proposal D-outcome table) → gates re-banked.

## What was measured (same thresholds, same 27 rows)

| | After C | After D |
| :--- | :--- | :--- |
| Gate 4 (≤ 0.18) | 0.214 (rep 0.137 / tail 0.483) | **0.376** (rep 0.435 / tail 0.169) |
| Gate 5 (≥ 80%) | 55.6% (rep 71.4 / tail 0.0) | **51.9%** (rep 61.9 / tail 16.7) |

- OOF race Brier **0.3268** vs base **0.2477** at race-win share **0.5476**
  (accuracy 49.58%): **still no skill** — a relabel measures a different
  event, not new skill. Published, not smoothed.
- Cone OOF bar-path containment **77.3%** (close 82.84%, terminal 89.7%,
  pooled 78.8%).

## Ruled out / costs carried openly

- **Threshold edits**: none. Both gates re-banked red by design
  (`--re-freeze-gates` = 9 gates; `--regression-exit` = 0/0 exit 0).
- **Gate 4's slice inversion** (tail now *ahead* of representative) is real
  signal about the graded set, not a bug — investigate before reading the
  rep slice as skill.
- **Sector-pool thinning**: race classes left 205 `NEITHER` rows across 9
  sectors → `oof_sector_pool_share_pct` **99.72% → 32.21%**; shipped cones
  defer to the pooled block far more. Disclosed in
  `provenance.corpus.sector_blocks` + ADR-011 decision 15; **do not tune
  `MIN_SECTOR_ATOMS_PER_CLASS` back down to chase containment.**

## Watch out for

- Regenerating reports requires the re-recorded `out_4_ml.json` bytes first;
  a failed `run_evals` pass leaves sidecar `failed` entries that make a later
  comparison look like drift (it isn't — rebuild, don't chase).
- Windows Python resolves `/tmp/...` to `D:\tmp`; write scratch files to the
  repo root or use bash paths and copy in.
- The suite is **unittest** (`python -m unittest discover -s tests`), not
  pytest — 657 tests green, 1 skipped. Full suite ~4.5 min (trainer tests
  train temp boosters).
- B/A remain the documented fallbacks if D's red ever needs resolving
  (B only as a recorded redefinition, "tracking limits", with a BRD
  amendment).

Seams: `tests/test_4_ml_direction.py` (race classification),
`tests/test_train_direction_model.py` (labels, OOF race Brier, pools),
`tests/test_run_evals.py` (gate rename + graded-set slices),
`tests/test_9_decision_engine.py` (absent-spread 0.0),
`tests/test_seed_context_records.py` (re-record/promote claims).
