# 2026-10-16 — Stage 8 Tier-2 Phase 1: the client matches Dhan's documented API, offline

## What was decided

- §7 Q1–Q5 of [plan-tier2-dhan-collection.md](docs/agents/plan-tier2-dhan-collection.md)
  signed by the owner the same day. Only the **offline-verifiable** Phase 1 was
  executed; Phases 0/2/3 (preflight probe, backfill, prospective depth) are
  operator runs gated on `DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN`, which Phase 1
  deliberately ran without.

## What landed (offline, TDD red-first)

- `scripts/8_dhan_precision.py` now speaks Dhan's **documented** v2 shapes:
  `api.dhan.co` host, `POST /v2/charts/intraday` (was the *daily* route to
  `api.dhantrade.com`), the documented request body (`securityId`,
  `exchangeSegment`, `instrument`, `interval`, `fromDate`, `toDate`), parallel
  OHLCV arrays with epoch `timestamp` (read in **IST**, so a fixture's bytes
  never depend on the machine's timezone), the `access-token` +
  `{"NSE_EQ": [securityId]}` depth request, and the 5-level book at
  `data.<seg>.<id>.depth.buy[0].price` / `sell[0].price` (flat scalars kept as
  a fallback).
- New `--intraday-fixture` / `--depth-fixture` flags: stage 8 runs over
  recorded evidence offline, byte-deterministically, with **zero network** — a
  fixture replaces the network for its half, the daily tier stays the fallback
  when a fixture is absent, and every half that did not run is named in
  `waived_reason` (the frozen tri-state: absence disclosed, never fatal, never
  invented).
- `.env.example` carries the two credential keys (`.env` is gitignored;
  `.env.example` is not, so it is committable and the token never enters a
  fixture, provenance block or log).
- Seams: `TestTheLiveClientSpeaksTheDocumentedApi` and
  `TestOfflineTier2Fixtures` in `tests/test_8_dhan_precision.py`.

## What Phase 1 cannot close, and why it is still open

A mock can pin a *shape*; only a recorded **real** response proves §2's gap
table's host/path/body rows — "never by editing a doc to match the code." That
closes in Phase 2 once a real `--live` run records the first fixtures. The
campaign's honest limits stay named: intraday history is **5 years** (5 of 62
rows — SCEN-001/002/009/010/011 — are outside retention and stay on the daily
tier with `dhan:intraday:outside-retention`), and **no historical depth
endpoint exists**, so `spread_impact_cost_pct` can only ever be graded from
prospectively captured snapshots (Phase 3).

## Watch out for

- **The daily-tier `out_8_dhan.json` bytes did not change.** The fixture flags
  default to `None` and the validation chain never passes `--live`, so V10's
  byte-compare (`test_proof_manifest.RERUNNABLE`) still passes and gate 6's
  number (71.4% of filled limits beating market MAE, a banked red) is
  unchanged. Re-banking becomes relevant only when Phase 2's *recorded*
  fixtures change what the artifact claims.
- **`load_*_fixture(None)` must return `None`, not a waiver.** `main()` passes
  the loader result unconditionally, so a loader that returns `[]`/`{}` on a
  missing path would waive the half the validation chain never asked for and
  silently break every recorded artifact.
- A fixture-mode zone is the *refined* one: `swing_low_pivot` runs over the 5m
  bars and `anchored_vwap` over `bars[pivot:]`. A test fixture whose lowest low
  is on the **first** bar does **not** move the anchor (the trailing window is
  then the whole series) — pin an intraday fixture whose pivot bar's typical
  price actually drags the VWAP below the daily tier's, or the assertion
  "refined" is vacuous.
- Suite is **unittest** (`python -m unittest discover -s tests`), not pytest:
  665 green, 1 skipped. Full suite ~4 min (trainer tests train temp boosters).
  `--regression-exit` reports 0/0 and exits 0 (the pre-commit shape).

Seams: `tests/test_8_dhan_precision.py` (documented request/response shapes +
offline fixture mode), `tests/test_9_decision_engine.py` (absent-spread 0.0),
`tests/test_proof_manifest.py` (V10 byte-compare of the unchanged daily
bytes), `tests/test_run_pipeline.py` / `tests/test_chain_contract.py` (stage-8
contract keys).
