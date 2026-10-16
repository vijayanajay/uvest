# 2026-10-16 — Baseline-aware pre-commit: banked reds pass, regressions block

## Decided

The hook (`githooks/pre-commit`) demanded an absolute `SUITE PASSED` from
`tests/run_evals.py`, but gates 4/5 are red **by policy** (published, not
tuned — evals.md §2) since the engine-gate arming. Result: every commit needed
`--no-verify`, so the hook caught nothing — a guard that always screams is
worse than no guard. The scorecard half is now baseline-aware:

- `run_evals.py --regression-exit` — exit 1 only on a **regression against the
  frozen baseline's new `gates` block** (verdict + value per gate): PASS that
  stops passing, any verdict → SKIP (grading lost), a banked red that gets
  worse, SKIP → FAIL (a newly graded red must be banked), or a red never
  banked. Improvements print as re-bank hints, never block.
- `run_evals.py --re-freeze-gates` — the deliberate publication: banks this
  run's 9 gate verdicts/values into `baseline_scorecard.json` (read-modify-write,
  other keys preserved, CRLF proof-root convention byte-matched to
  `scaffold_catalog.write_json`). Banking then exiting through baseline mode is
  acceptance: exit 0 unless a non-gate half failed.
- Non-gate failures (dataset integrity, probe rosters, evidence coverage, rule
  census) keep blocking absolutely in every mode — they are correctness or
  already floor-compared, not published reds.
- Plain `run_evals.py` unchanged: `0 if verdict_ok else 1` (suite-verified).

Measured: bank run exit 0 (9 gates), `--regression-exit` exit **0** with
"Baseline regression check: OK (0 regression(s))" while the FINAL verdict line
still prints SUITE NOT PASSED — the scorecard never lies, only the exit code
changed meaning. Full suite 599/599 (9 new seams:
`TestBaselineRegressionExit`).

## Ruled out

- Parsing the scorecard in shell against the baseline — the Gate objects and
  `load_baseline` already live in the runner; shell JSON-parsing would fork a
  second truth.
- A separate checker script — same code, one more import surface.
- Tolerating unbanked reds — that would let any new red ship silently; the
  bank is what makes a red an act (stage 8's arrival will be exactly this).
- FAIL → SKIP tolerance — losing grading hides behind the same percentage.

## Watch

- A re-record (e.g. `record_stage4_evidence.py --apply`, which calls
  `scaffold_catalog.re_freeze_baseline`) changes gate inputs without refreshing
  the `gates` block: the next `--regression-exit` run may report a worse red or
  an improvement. Improvement → re-bank; worse → investigate, then bank.
  `re_freeze_baseline` intentionally does not bank gates (it cannot compute
  them without a full run).
- The bank is stale-prone by design — the ▲ hint exists to keep it honest.
- CI/workflows are untouched: default exit behavior did not change.
