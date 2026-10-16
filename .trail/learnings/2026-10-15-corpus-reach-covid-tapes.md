# 2026-10-15 — Extending the corpus back over the March–April 2020 crash

**Owner command:** make the two excluded Covid tapes (SCEN-001, SCEN-002)
*gradeable* instead of permanently ungraded under gate 5's trained-window scope
(ADR-011 decision 9).

## Root cause (the thing to remember)

- The stock vintages were never the problem: `RELIANCE_202003.json` and
  `SBIN_202003.json` run 2019-04-30 → 2020-04-30. The **index panel**
  (`tests/fixtures/history/indices_daily.json`) started **2020-03-09**, and a
  training row needs `CROSS_ASSET_WINDOW + 1` (21) index sessions at or before
  its own date (`4_ml_direction.common_return_windows`) — so the panel's first
  session, not the stocks', held the first buildable row to 2020-04-09 and
  opened `row_window` weeks after SBIN's own 200-session floor (2020-02-18).
- Ruled out by probe: Yahoo is *not* the limit. `fetch_yahoo_closes` with a
  larger session count served back to 2014-08 immediately; the 2020-03-09 start
  was the fetch window itself (a fixed `SESSIONS = 1400` whose comment had gone
  stale at "2021-05 onward" while the vintages reached 2018-03).
- Also ruled out: any change to the gate. Decision 9's scope logic stayed
  untouched; the *corpus* moved (contract: evals.md §3 #5 "the corpus owns any
  reach gap" + ADR-011 decision 10).

## What was done

1. `tests/evals/build_index_corpus.py`: `stock_history_span()` +
   `fetch_sessions(as_of)` — the fetch count is **derived from the committed
   stock corpus** (now 1889 sessions → start 2017-11-30, 90-day margin),
   `SESSIONS` demoted to a fallback floor.
2. Re-fetched the panel (one networked step, same tier-1 source/fetcher):
   window `2017-11-30 – 2026-10-15`, **same 5 served / 7 disclosed absent** —
   trained universe unchanged (bank/IT/pharma only; the 6 one-session
   tickers + `^CNXBANK` stay absent).
3. Retrained: `row_window = ['2020-02-18', '2025-10-20']`, **6,294** rows
   (+33, all SBIN 2020-02-18..2020-04-08), symbols/class counts unchanged in
   kind, OOF Brier **0.266553** vs base **0.244843**, cone containment
   75.87 bar / 81.78 close / 88.56 terminal.
4. Re-recorded stage-4 evidence (`record_stage4_evidence.py --apply`) — a
   retrain *always* forces this: `test_proof_manifest.RERUNNABLE[4]`
   byte-compares fresh stage-4 stdout against the recorded artifacts, and the
   tool re-promotes `cone_p10/p90/direction/probability` into the golden
   dataset and re-freezes hashes + baseline (V12).
5. Re-ran the runner: **6 of 6 cone-labeled trades graded, no exclusion on the
   scope line**; gate 4 **0.636 → 0.562**; gate 5 still **0.0% (0/6 miss)**;
   exit 1 unchanged.

## Measured per-row (new cones)

| row | floor (P10) | ceiling (P90) | realized | miss |
|---|---|---|---|---|
| SCEN-001 | −6.50% | +14.34% | −2.23% .. **+63.17%** | HIGH (V-rebound) |
| SCEN-002 | −6.54% | +14.56% | **−8.35%** .. +10.97% | LOW |
| SCEN-003 | −6.92% | +14.02% | **−17.05%** .. −3.68% | LOW |
| SCEN-004 | −6.20% | +14.53% | **−17.57%** .. −3.43% | LOW |
| SCEN-006 | −7.29% | +11.84% | **−8.69%** .. +1.46% | LOW |
| SCEN-008 | −7.15% | +14.61% | **−43.48%** .. −6.07% | LOW |

Close path also 0/6 — the miss is neither scope nor realization.

## Watch-outs

- **Vintage-boundary gap labels (pre-existing):** SBIN's vintages tile with one
  calendar gap (2020-05 → 2021-05), so rows within 22 sessions of a vintage's
  last session take forward sessions across the gap. This predates this change
  (the old window already trained 2020-04-09+ SBIN rows); the reach added ~33
  rows, ~8 of them near the boundary. A future honesty pass could restrict
  labels to within-vintage forward windows — owner decision, not done here.
- **Yahoo absences are source-mood, not policy:** 6 sector tickers serve
  exactly "1 session" and `^CNXBANK` none. A future re-fetch could change the
  served set → trained universe grows → `TestShippedArtifact.
  test_the_artifact_still_matches_the_corpus_it_names` goes red and a retrain
  is required (it compares provenance symbols against the buildable set).
- **Derived windows need margin:** `fetch_sessions` adds +90 days before the
  corpus's earliest session because `fetch_yahoo_closes` truncates with
  `int(sessions * 1.7) + 30`; the committed-file seam (panel span ⊇ stock
  span) is the outcome check if that formula ever drifts.
- **Gates publish, never tune:** the reach changed only the denominator
  honesty; coverage stayed 0.0% because the pools gained just 33 rows (only
  SBIN trades 2020 among the trained universe). Gate 4's improvement
  (0.562) is a retrain side effect, published as measured.

## Seams added

- `tests/test_build_index_corpus.py::TestThePanelCoversTheStockCorpus`
  (fetch window derived from stock corpus + committed panel spans it)
- `tests/test_train_direction_model.py::TestTheCorpusReachesTheGradedTapes`
  (rows build inside every graded tape, from committed files alone)
- `TestConeCoverageScope.test_the_covid_tapes_are_inside_the_scope_and_all_six_grade`
  (end-to-end: 6/6 graded, no `ungraded` on the scope line; the synthetic
  disclosure seams keep the exclusion logic covered via `SYNTHETIC_WINDOW`)
