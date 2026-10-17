# TICK-008: Dhan API Precision Intraday & Depth Client

**Status:** FROZEN (amended 2026-10-16 by owner command — Q1–Q3 signed as recommendation B in [docs/agents/proposal-limit-band-policy.md](file:///d:/Code/uvest/docs/agents/proposal-limit-band-policy.md): the **discount clamp** — `optimal_limit_buy` is a limit entry only when it sits strictly below `entry_price`, disclosed by the new `zone_above_market` key — and gate 6's fill-rate floor ≥ 60% with the median-net line)  
**Priority:** P1  
**Test Seam:** `tests/test_8_dhan_precision.py`  
**Target Script:** `scripts/8_dhan_precision.py`  
**Output Artifact:** `out_8_dhan.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 8)

### 1. Goal & Context

Stage 8 is the **Tier-2 Precision Intraday and Execution Layer** (BRD §8, §9.1 row 8). While earlier stages rely entirely on free public daily data, Stage 8 reserves the Dhan Broker API strictly for intraday resolution and real-time order depth:
1. **5m/10m Intraday Candles:** Precision pivot anchoring for Anchored VWAP (AVWAP).
2. **Optimal Limit Price Execution Zone:** Identifies order blocks, Fair Value Gaps (FVG), and support retests (Core Prediction 3) to realign retail R:R from sub-par market chasing to pristine limit execution.
3. **Real-Time Depth & Impact Cost:** Evaluates Top 5 Bids/Asks and spread impact.
4. **Local SQLite Caching:** Caches API queries in `dhan_cache.db` with a 15-minute TTL to prevent rate limit churn.
5. **Graceful Offline Degradation:** If Dhan credentials are unset or the API returns HTTP 429/500, marks `waived: true` (0 pts) and completes validation cleanly with exit code 0.

### 2. Frozen Specification Reference

- `BRD.md` §4 (Core Prediction 3: Optimal Price Range).
- `BRD.md` §8 (Tiered Data Hierarchy & Dhan API Integration).
- `BRD.md` §8.1 (Client Specifications, Caching, and Degradation).
- `BRD.md` §9 Script Inventory: `anchored_vwap`, `optimal_limit_buy`, `spread_impact_cost_pct`, `orderblock_support`.
- `docs/agents/domain.md` — ADR-003 (Tiered Data Hierarchy).
- **Input:** `current_trade.json`. **Output:** `out_8_dhan.json`.

**Canonical output contract** (mirrors `BRD.md` §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `anchored_vwap` | float \| null | Intraday Anchored VWAP from swing low pivot |
| `optimal_limit_buy` | float \| null | Recommended limit buy entry price — the band midpoint, and **null when it is not a strict discount** below `entry_price` (the 2026-10-16 clamp: a limit at/above the entry is a market order, not a limit entry) |
| `optimal_limit_range` | list[float] \| null | Optimal execution band `[min, max]` (the levels ship even when the midpoint is clamped) |
| `zone_above_market` | bool \| null | `true` when the midpoint was clamped (≥ `entry_price`), `false` when the limit shipped, `null` when no zone was computed (waiver) |
| `spread_impact_cost_pct`| float \| null | Real-time bid-ask spread and impact cost % |
| `orderblock_support` | float \| null | Nearest 5m demand orderblock support level |
| `cache_hit` | bool | `true` if served from `dhan_cache.db` |
| `waived` | bool | `true` if Dhan credentials absent or API offline |

### 3. TDD Acceptance Criteria

- [x] Consumes canonical `current_trade.json` and computes Anchored VWAP from recent session pivot.
- [x] Derives optimal limit buy price that reduces MAE compared to Univest recommended market entry — the *derivation* is graded per record by gate 6 (`tests/run_evals.py`): its first measurement over the benchmark is **71.4%** of filled limits beating market MAE (16 of 58 limits never trade to the zone and are excluded, never free wins), below the ≥ 75% criterion — a banked red under the baseline-aware pre-commit policy, published rather than papered over.
- [x] Caches responses in `dhan_cache.db` with 15-minute expiry; avoids redundant API calls.
- [x] Degrades gracefully when `DHAN_ACCESS_TOKEN` is unset: returns `waived: true` with null metrics and exit code 0 (the Dhan half only — the daily Tier-1 zone still ships, exactly §8.1's "completes the validation using daily OHLCV from Tier 1").
- [x] Fully tested offline using recorded mock API payloads with zero live network calls (the Dhan half is operator-gated behind `--live`, which the validation chain never passes, so every recorded artifact is byte-deterministic and byte-compared to a fresh run via `test_proof_manifest.RERUNNABLE`).
- [x] Discount clamp (amended 2026-10-16, proposal B signed): the midpoint is emitted only when it sits strictly below `entry_price`; at or above it `optimal_limit_buy` is null with `zone_above_market: true`, and gate 6's criterion is now *both* ≥ 75% of filled limits beating market MAE *and* a ≥ 60% fill rate over records carrying a limit claim (the lemma: a filled limit beats market MAE iff `limit < entry`, so the fill rate is the falsifiable half).
- [x] **Phase 1 of the Tier-2 collection campaign (offline, landed 2026-10-16; §7 Q1–Q5 signed).** The client now speaks Dhan's **documented** v2 shapes — `api.dhan.co` host, `/v2/charts/intraday`, the documented request body, parallel OHLCV arrays with epoch `timestamp` (read in IST), the `access-token` + `{"NSE_EQ": [securityId]}` depth request, and the 5-level book at `data.<seg>.<id>.depth.buy[0].price` / `sell[0].price` — behind the operator-gated `--live` gate, with the waiver tri-state and the `dhan_cache.db` 15-minute TTL contract unchanged. It also runs offline over recorded evidence via the new `--intraday-fixture` / `--depth-fixture` flags (a fixture replaces the network for its half, so the validation chain stays deterministic and V10 keeps byte-comparing), and `.env.example` carries the two credential keys (`.env` gitignored; the token never enters a fixture, provenance block or log). Seams: `TestTheLiveClientSpeaksTheDocumentedApi` and `TestOfflineTier2Fixtures` in `tests/test_8_dhan_precision.py`.
- [ ] **Open — the Tier-2 half has never carried *real* evidence.** Every **recorded** artifact is still `precision_source: "daily_tier1"` with the Dhan half waived offline, so neither the 5m intraday refinement nor `spread_impact_cost_pct` has ever been graded against a real tape. Phase 1 closed the offline-verifiable half; what remains is Phase 2–3 of [docs/agents/plan-tier2-dhan-collection.md](file:///d:/Code/uvest/docs/agents/plan-tier2-dhan-collection.md) — a preflight probe, then backfilling ≥ 1 reachable row with a recorded `intraday_prefix.json` and ≥ 1 depth snapshot, plus the out-of-retention disclosure on the 5 pre-2021 rows. That needs the operator's `--live` run (`DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN`), which Phase 1 deliberately ran without.
  **Phase 0's probe now exists** (`tests/evals/dhan_preflight.py`, seam `tests/test_dhan_preflight.py`, 9 tests) and it **fails closed**: run without `DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN` it exits 2 with `ERR_NO_CREDENTIALS` and writes nothing, so no retention/depth-shape disclosure is committed until the operator runs it against the broker.
- [x] **`score_precision` honesty fix (landed 2026-10-16, TDD red-first).** Stage 9 no longer rewards an unmeasured spread: `spread_impact_cost_pct` absent, null, or non-numeric now scores **0.0**, and a measured zero-cost spread still scores 1.0 under the frozen `1 − clip(spread / 0.5)` formula (the old `float(doc.get(...) or 0.0)` paid 1.0 for evidence nobody collected, and raised `ValueError` on a non-numeric value). Seam: `TestScorePrecisionNeverRewardsAbsentSpread` in `tests/test_9_decision_engine.py`, which also pins §10.4's other half — a *waived* `out_8` deducts its 5.0 weight from `max_possible_score` rather than contributing a score of any kind.
