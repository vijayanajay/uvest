# 2026-10-17 — next item selection: close the offline fixture trap

## Decided
Pick **stage 2's live Tier-1 daily-OHLCV fetcher** (with `data_cache/` caching)
next, over stage 10's price half, stage 11, and the remaining stage-12 visuals.
It also carries stage 7's NSE index-archive fallback for sector series.

## Why — measured, not argued
`scripts/run_pipeline.py --input tests/fixtures/_input_reliance_20261009.txt
--workdir <tmp> --regime-live` (no fixture flags):

- `out_2` … `out_8` all `waived: true` — **7 of 8 upstream stages**.
- Verdict from stage 1 alone: `7.11/20.0` (35.5%), P(win) 49.2%, `GO_WEAK`.
- Stage 4 names the cause: `ohlcv:absent, series:^CNXENERGY:absent`.
- Stage 7 waived on `series:^CNXENERGY:absent` — its live sector series is
  Yahoo-only, and stage 7 is all-or-nothing (ADR-010 #3), so one absent sector
  series costs the whole 15-point macro block. The NSE daily index archive
  parser already written in `tests/evals/build_index_corpus.py` is the reuse.

One cached stock series feeds stages 2, 3, 4 and 8, so this is the smallest
change that turns four waivers into four measured blocks. Stage 10's price half
and stage 11's autopsy both consume ATR/price evidence, so they are cheaper
after it, not before.

## Second finding (not an actionplan item, filed here)
Stage 9's `p_win` is `0.55 + 0.004·(effective_score − 50)` clipped — algebraically
the score rescaled, so "P(win) ≥ 55%" is exactly "effective score ≥ 50" and
`expectancy` is monotone in the score. Brier of the recorded `p_win` against the
58 derived resolutions is **0.2485 vs 0.2310 for a constant at the corpus win
rate (36.2%)** — the engine's probability is worse than a base rate, the same
verdict stage 4 earned (0.410 vs 0.248). Gate 8's 65.5% alignment is measuring
that one fact. Any future "improve the verdict" work should grade `p_win` itself
before touching weights.

## Watch out
- `KNOWN_LOT_SIZES` in `scripts/0_ingest_reco.py` is declared and never read:
  the lot-size validation item is genuinely open, not merely unchecked.
- Verification of the live path is operator-gated: no Dhan credentials here, so
  `tests/evals/dhan_preflight.py` fails closed with `ERR_NO_CREDENTIALS`.
