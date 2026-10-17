# 2026-10-17 — Stage-3 DTW evidence lands; the GO ceiling is now per-record, and divergence is a gate

## What was decided

1. **`out_3_dtw.json` is recorded evidence on all 58 golden rows.** The
   history bundle each row replays is *derived*, never fetched:
   `tests/evals/record_stage3_evidence.py` truncates the row's own committed
   symbol-month tape (`tests/fixtures/history/<SYMBOL>_<YYYYMM>.json`) to
   `date <= T_rec` into `fixtures/<id>/history.json`, declares it as
   `replay_inputs.history_fixture`, and runs the real stage `--record`.
   Truncation is the harness's job (the same frozen rule the OHLCV prefix
   obeys) — passing the raw tape would put the DTW "current window" *after*
   T_rec, i.e. look-ahead; the validator's V7 scan now covers the bundle
   because `_replay_paths` gained the key.
2. **Stage 3 joined `test_proof_manifest.RERUNNABLE`** (V10 byte-compares
   every recorded artifact; stage-9 replay composes the row's recorded
   payloads in stage order, so re-recording stage 3 *requires* re-recording
   stage 9 before V10 can pass).
3. **Gate 8, "Verdict Alignment vs Expectation", grades the divergence**
   (`run_evals.gate_verdict_alignment`, evals.md §3 #8): proof-row
   `expected_verdict` vs recorded `out_9_decision.json` `decision`, two
   independent sources, transition slice + every divergent record printed on
   the scorecard. Threshold 100% by policy — a **published red** banked via
   `--re-freeze-gates`, exactly like gates 4/5; `--regression-exit` turns any
   *further* divergence into a named regression.

## What was ruled out / learned

- **"DTW is what keeps every verdict below GO" was only half true.** After
  recording, the distribution is *unchanged* (38 AVOID / 5 WAIT / 15
  GO_WEAK / 0 GO) — on measured numbers now. The remaining ceiling is
  per record and named in the payload: stages 4/5/6/7 are recorded on only
  27/10/12/6 rows (so `max_possible_score` stays low on the unbundled
  campaign rows), and several GO-expected rows sit at calibrated P(win)
  just under the 55% floor (e.g. `GB-2025-0032`: 32.5 max, P(win) 54.9%).
  Neither side was tuned; the badges and the gate both re-derive from the
  recorded bytes.
- **`report_regen` never overwrites a graded report by design.** After
  re-recording payloads, stale `reports/report_*.html` make every job a
  sidecar-compare "differs" failure *and* a diff failure against the new
  record. The workflow is: `rm -f reports/report_*.html reports/run_record.json`,
  then `report_regen.py` (58, differ-clean) and `report_regen.py --prototype`
  (the one tracked report) — never edit reports by hand.
- **`.venv/Scripts/python.exe` is the interpreter with fastdtw/lightgbm;**
  system `python` lacks them, and `Path.read_text(newline=...)` is
  3.13-only (use `open(..., newline="")` for CRLF-preserving edits —
  `tests/test_run_evals.py` is CRLF).
- Windows console is cp1252: avoid printing non-ASCII (→, ₹, …) from
  one-off diagnostic scripts; redirect through bash or print `ascii`.

## What to watch

- The alignment bank: 38/58 (65.5%). An improvement (records flip to
  aligned) prints as a re-bank hint; a *new* divergence blocks pre-commit
  until deliberately banked. The 15 `GO→GO_WEAK` rows are the campaign's
  geometry advisories — the moment stages 4–7 get recorded evidence on
  those rows, expect `max_possible_score` and P(win) to move and some of
  those verdicts to flip; that is the adjudication the red is waiting for.
- `replay_inputs.history_fixture` is now part of the frozen schema; any new
  seeder that authors rows must derive the bundle from the row's own tape
  (≥ 72 sessions at T_rec) or leave stage 3 honestly waived.
