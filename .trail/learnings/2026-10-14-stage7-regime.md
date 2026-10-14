# 2026-10-14 — Stage 7 (TICK-007) sector/macro regime: spec conflicts frozen before code

## What shipped
- `scripts/7_sector_macro_regime.py` + seam `tests/test_7_sector_macro_regime.py` (27 tests):
  Mansfield RS + 5-session momentum, RRG quadrant, frozen `nifty_trend` tokens, VIX bands,
  breadth regime, FII/DII flow bias, `panic_regime_breaker` (strict > 24.0 and < 30.0).
- Plumbing: `_recorder.FROZEN_ARTIFACTS[7]`, `PENDING_ARTIFACTS` dropped 7, `FIXTURE_FLAG[7] =
  --regime-fixture`, `RECORD_STAGES += 7`, `CONTRACT_STAGES += 7`, chain-test skip lists updated
  (4, 8, 10 only now).
- Evidence: `uvest-market-context/1` schema (ADR-010 in `docs/agents/domain.md`), built by
  `tests/support/market_context.py`, committed fixture `tests/fixtures/market_context_20261009.json`.

## Decided (ADR-010, owner command)
1. **Spec outranks shipped code**: stage 9 scored `nifty_trend` as BULLISH/NEUTRAL/BEARISH —
   tokens no producer emits, so every run silently took the 0.2 default. Kept TICK-007/BRD §7.2
   tokens; fixed `score_regime` + its test instead.
2. RRG needs two axes: `sector_mrs_mom` added to contract (BRD §7.1/§9 inventory row 7 updated).
3. **One fixture, one waiver**: all-or-nothing payload waiver with `missing_inputs`; BRD §10.4's
   waiver is payload-granular (stage 9 scores one artifact), so partial waiver would need a
   stage-9 scoring seam that doesn't exist.
4. Sector map = module constant `SECTOR_INDEX_MAP` (stage-0 OPTIONS_TYPES precedent); unmapped
   symbol ⇒ waiver, never a guessed index.

## Ruled out
- Per-component waiver / renormalized sub-scores in `score_regime` (inflation risk, spec churn).
- Embedding the sector map in the fixture (hides domain data in evidence).
- Emitting BRD §7.1 "stock vs sector RS" (no stock series in the regime bundle; deferred to
  stages 2/4, disclosed in TICK-007 §2).

## Watch out for
- **Run the suite on `.venv/Scripts/python.exe`** — bare `python` (3.13) lacks `fastdtw`; chain
  tests fail at stage 3 import, not from real regressions.
- Follow-on open: breadth/flow consumed by stage 9 (TICK-009 §3), panic breaker graded in gate 2
  (EVAL-001 §3), live NIFTY-Indices/yfinance connector (TICK-007 last box).
- The `n=210` weekday fixture is authored/disclosed, not market evidence; no graded gate reads it.
