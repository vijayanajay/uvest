# 2026-10-14 — Stage 4 on the shared bundle, and the daily triage mode

## What was asked

1. Wire live market context into **stage 4** so the LightGBM engine consumes the
   *same fetched bundle* stage 7 classifies instead of waiting on a second fetcher.
2. Add a **daily triage mode** that reruns stage 7 against live context for all
   tracked positions.

Both targeted stages that did not exist (`4_ml_direction.py`, `10_daily_triage.py`),
so the work was "implement the stage, with the wiring already in".

## Decisions (owner-confirmed before coding)

- **Stage-4 weights: embedded, authored, disclosed booster.** TICK-004 §1 allows a
  pretrained artifact *or* embedded fallback weights. No training corpus exists and
  BRD §5.2/understanding.md warn against fitting on a few dozen trades, so the shipped
  model is a small deterministic stand-in built by `tests/evals/build_direction_model.py`
  from two committed fixtures (10-year RELIANCE closes + the authored market bundle),
  156 point-in-time rows, `origin: authored` written into the calibration file. Rejected:
  a waiver-only skeleton (the ask would be hollow) and fitting on the 58-trade benchmark
  (the overfitting the BRD names).
- **Tracked positions: SQLite `uvest_history.db`** (BRD §11), so the triage mode needed
  the minimal stage-10 slice — `--action track` to freeze the day-1 thesis, a
  `triage_history` audit trail, and `--action triage` for the loop. Rejected: a
  positions JSON register (diverges from the frozen storage decision) and reusing
  proof bundles (conflates proof scenarios with live positions).

## Design points worth not re-deriving

- **One bundle, two consumers.** The runner fetches live context *once*, in front of the
  first consumer (stage 4 in the frozen order) and assigns it to both fixtures;
  `--regime-fixture` also feeds stage 4's `--context-fixture`. `FIXTURE_FLAG` became
  stage → tuple of `(flag, slot)` so a stage can draw on more than one artifact and two
  stages can share one. No second fetcher, no second cache.
- **Feature/trainer identity.** One `feature_vector` is imported by the stage and the
  builder, so the closed feature set cannot drift. It is close-based on purpose: the
  10-year training fixture carries closes only, so Parkinson/GK volatility, ADX,
  overnight gaps and delivery features are *not* shipped (disclosed in TICK-004 §4 and
  the actionplan) rather than trained on data that never existed.
- **Beta variance floor.** `_beta` returns 0.0 below `MIN_MARKET_VARIANCE = 1e-6`: the
  authored NIFTY proxy is a near-flat ramp whose daily-return variance is ~1e-8, which
  made the OLS slope report ~10.7 — a number that reads as conviction. Real NIFTY
  windows sit near 1 (σ ≈ 0.6–1.0%).
- **Exact mixture quantiles.** The cone inverts the *pooled* class-weighted distribution
  (weight p_c / n_c over the observed samples), so `p10 ≤ … ≤ p90` holds by
  construction; no vincentized approximation. MAE/MFE are that mixture's medians in ₹.
- **Macro premise → action (stage 10).** broken (panic breaker, `PANIC` VIX or `LAGGING`
  sector) ⇒ `LIQUIDATE`; weakened (`RISK_OFF` breadth, `NET_SELLING` flow, or a
  `WEAKENING`/`IMPROVING` sector) ⇒ `TIGHTEN_STOP`; else `HOLD`. The day-1 premise is
  "sector MRS leading", so the RRG quadrant is premise evidence and all four frozen
  tokens are mapped — a first cut treated `WEAKENING` as neutral, which quietly ignored
  the very premise the stage audits. `TRAIL_STOP` is never emitted — it is profit
  harvesting, i.e. the unimplemented price half. `adverse_level`/`trailing_stop_update`
  stay `null` and every row names what it is missing in `pending_inputs`.
- **Stage 10 reruns the real stage**: `7_sector_macro_regime.py` is spawned through its
  frozen CLI per position and its payload is transcribed verbatim.

## Traps hit (and what they cost)

- **LightGBM model files are line-sensitive.** `Path.write_text` on Windows rewrote the
  booster to CRLF and every load failed with `Model format error, expect a tree here`.
  Write bytes, and pin `models/*.txt -text` in `.gitattributes` — otherwise
  `core.autocrlf=true` re-corrupts it on a fresh clone.
- **The in-process fetcher prints to stdout.** Stage 10 fetches the live bundle inside a
  stage whose stdout *is* the artifact, so the fetcher's `[ok] …` progress lines prefixed
  the JSON. Fixed with `contextlib.redirect_stdout(sys.stderr)`; the seam now asserts the
  artifact stays parseable JSON. The runner never hit this because its stdout is a log.
- **Stage stderr must be ASCII.** A `·` in a stage's stderr line crashed the test
  harness's UTF-8 reader thread on Windows (cp1252 byte 0xb7).
- **Relative fixture paths vs the repo-root spawn.** Stage 10 spawns stage 7 from the repo
  root, so a caller-relative `--context-fixture` silently waived. Resolve to absolute.
- **Fixture thinness is not a code bug.** Yahoo currently serves `^CNXENERGY` **1**
  session (vs 247 for `^NSEBANK`/`^CNXIT`). The fetcher's `align_axes` inner-joins the
  index series, so one thin sector series collapses the whole bundle and stage 7 waives
  naming `series:^CNXENERGY:short` — correct behaviour, but it means live audits of
  energy-sector symbols waive until that source recovers.

## Contract/test rosters an implementer must update

`_recorder.FROZEN_ARTIFACTS` (+4, +10), runner `PENDING_ARTIFACTS`/`RECORD_STAGES`/
`FIXTURE_FLAG`/payload stages, `test_chain_contract.CONTRACT_STAGES` (+4, +10),
`test_run_pipeline`'s exact `runs` list, `test_recorder`'s forward-looking unregistered
stage (now 8), `test_run_evals`' stage-ran disclosure line, and
`test_contract_consistency.NON_STAGE_SCRIPTS` (+`scripts/_sector_map.py`). The evals
cone-coverage skip reason was reworded: stage 4 exists, but grading an authored
stand-in's envelope would be the unbacked claim evals.md §2 forbids.

## Open, disclosed

- BRD §5.2's real NSE-F&O-universe booster (with purged group time-series CV) — the
  shipped artifact is a stand-in, so eval dimensions 4–5 stay ungraded.
- The price half of TICK-011: the four-zone classification, `still_chance_pct`, the
  three-stage adverse protocol and the action levels.
- Yahoo's thin `^CNXENERGY` coverage; and whether `align_axes` should stop collapsing
  series a consumer could still use on its own axis (stage 4 needs 21 common sessions,
  not the full 200).
