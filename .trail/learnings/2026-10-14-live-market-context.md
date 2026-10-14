# 2026-10-14 — Live `uvest-market-context/1` fetcher + regime-shock roster

## Decided
- `scripts/fetch_market_context.py` is the one live fetcher for stage 7's
  evidence bundle (ADR-010 decision 4), wired via `--regime-live` on
  `run_pipeline.py` (mutually exclusive with `--regime-fixture`). It is a
  non-stage script: added to `NON_STAGE_SCRIPTS` in
  `tests/test_contract_consistency.py` so contract docs may cite it.
- Flow history: NSE's `fiidiiTradeReact` API returns only the latest day
  (range params silently ignored; `archives.nseindia.com/content/nscash/`
  is dead). The dated public cash table (5paisa `fii-dii`) supplies the
  daily history; NSE API wins for the latest date. Values cross-check to the
  decimal (both sources: 2026-10-14 net = 813.0 cr).
- Breadth = today's NSE500 constituents (NSE's own CSV) applied to history's
  closes — disclosed as such; Yahoo bulk history is fetched as one batched
  request (2026-10-14: 501 symbols, ~35 KB).

## Ruled out
- Committing live-fetched data (`CONTRACTS.txt`, caches): live values churn
  and would produce per-machine diffs. All live tests are fake-source,
  offline seams; real-network tests are `UVEST_LIVE=1`-gated skips.
- Relying on `yfinance` frames' column layout: single-ticker frames come back
  MultiIndex (`price, TICKER`) — always resolve columns positionally or via
  droplevel; this bit the live smoke once.

## Watch out
- `data_cache/` holds the FII/DII continuity cache (gitignored on purpose);
  deleting it costs the flow continuity back to the earliest date in the
  committed dated-table fixture (≥5 sessions on a clean checkout).
- `tests/support/market_context.py` names the breadth series
  `NSE500_BREADTH` from the real NSE500 constituents CSV (not
  NIFTY50-derived) — the seeded roster fixtures rest on that claim.
