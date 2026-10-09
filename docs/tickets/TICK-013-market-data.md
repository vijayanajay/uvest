# TICK-013: Daily OHLCV, ATR & Trend Regime Engine

**Status:** FROZEN  
**Priority:** P1  
**Test Seam:** `tests/test_2_market_data.py`  
**Target Script:** `scripts/2_market_data.py`  
**Output Artifact:** `out_2_market.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 2)

### 1. Goal & Context

Stage 2 is the **sole owner of price-derived volatility and trend state** for the whole pipeline. It consumes `current_trade.json`, fetches free daily OHLCV, and emits the volatility buffer that gates the recommendation.

It exists for one reason: BRD §10.2 #5 — a stop-loss tighter than $0.75 \times \text{Daily ATR}$ is a noise trap that will be stopped out by ordinary intraday wicks. Nothing else in the suite computes ATR, so this stage is a hard prerequisite for the **Volatility Noise Stop** circuit breaker (`prompt.md` §3), the **Volatility Buffer** row of the report template, and the §11 adverse-movement triage levels.

**Deliberately out of scope (do not build here):**
- Intraday 5m/10m candles, VWAP, order blocks → stage 8 (`8_dhan_precision.py`, Tier 2 Dhan fallback).
- GARCH cone, MAE/MFE, ML drift → stage 4 (`4_ml_direction.py`).
- NIFTY/India VIX/sector regime → stage 7 (`7_sector_macro_regime.py`).
- Bhavcopy delivery % / F&O OI → stage 5 (`5_institutional_flow.py`).

**Data hierarchy:** Tier 1 free only, per ADR-003. yfinance daily bars. No paid API call may be introduced at this stage.

### 2. Frozen Specification Reference

- `BRD.md` §9.1 stage 2 (contract row) and §2 (`2_market_data.py` — ATR, 50/200 EMA, Volatility Regime).
- `BRD.md` §10.2 #5 — **Severe Volatility Noise Trap:** SL tighter than $0.75 \times \text{Daily ATR}$ $\implies$ instant 🔴 NO-GO.
- `BRD.md` §10.3 — **Waiver Rule:** on data failure the check is marked `waived`, earns 0 points, and is subtracted from `max_available_score`. Missing data never inflates and never penalizes. This is why `is_tight_sl` is **tri-state**, never a defaulted `false`.
- `BRD.md` §11 — Level 1 ($\le 1.0 \times \text{ATR}$ drawdown) and Level 2 ($> 1.0 \times \text{ATR}$ **or breaks 20 EMA**): the 20 EMA level is consumed by stage 10 triage.
- `BRD.md` §5.1 — feature naming (`ATR/Price ratio`, `Bollinger Bandwidth`) consumed downstream by stage 4.
- `prompt.md` §3 — **Volatility Noise Stop**, source script `2_market_data.py`.
- `prompt.md` §5 — **Volatility Buffer** report row (`Daily ATR: ₹ATR`, `SL Distance: ₹Dist`, `Safe (N.Nx ATR)`).
- `docs/agents/evals.md` — dimension 2 (circuit-breaker enforcement) and the *"5 High Volatility / Tight Stop Traps"* golden-dataset class.
- **Input:** `current_trade.json` (canonical schema, BRD §3.2). **Output:** `out_2_market.json` (stdout).

**Canonical output contract** (keys mirror `BRD.md` §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `atr_14` | float \| null | 14-session Average True Range (₹) |
| `sl_atr_ratio` | float \| null | $\lvert \text{live\_price} - \text{stoploss} \rvert / \text{atr\_14}$ |
| `is_tight_sl` | bool \| null | `true` when `sl_atr_ratio` $< 0.75$; `null` when waived |
| `ema_20` | float \| null | 20-session EMA level (stage 10 triage input) |
| `above_50_ema` | bool \| null | last close vs 50-session EMA |
| `above_200_sma` | bool \| null | last close vs 200-session SMA |
| `trend_state` | enum \| null | `BULLISH` \| `BEARISH` \| `MIXED` |
| `bollinger_upper` / `bollinger_lower` | float \| null | 20-period, 2σ bands |
| `bollinger_bandwidth` | float \| null | $(\text{upper} - \text{lower}) / \text{mid} \times 100$ |
| `waived` | bool | `true` when OHLCV was unavailable; all metrics `null` |
| `as_of` | str \| null | Last completed session date (`YYYY-MM-DD`) |

**Indicator conventions (frozen here for determinism — tests depend on these):**
- $\text{TR}_t = \max(H_t - L_t,\ \lvert H_t - C_{t-1} \rvert,\ \lvert L_t - C_{t-1} \rvert)$; `atr_14` = simple mean of the 14 most recent true ranges (no Wilder smoothing).
- EMA: $\alpha = 2/(N+1)$, seeded with the SMA of the first $N$ closes.
- Bollinger σ: **population** standard deviation of the 20 closes (not sample).
- Requires ≥ 200 completed sessions; below that the stage is `waived`.

### 3. TDD Acceptance Criteria

- [ ] Reads the canonical `current_trade.json` and uses `live_price` as the reference for SL distance (matching the worked example in `BRD.md` §16: $\lvert 2988.20 - 2910.00 \rvert = 78.20$).
- [ ] Fetches **daily** OHLCV only; no intraday, depth, or order-block request is made (Tier-2 boundary).
- [ ] `atr_14` matches a hand-computed fixture array, using the frozen simple-mean TR convention.
- [ ] `sl_atr_ratio` is computed from `live_price`, and `is_tight_sl` is `true` iff the ratio is $< 0.75$.
- [ ] **Boundary:** a ratio of exactly 0.75 yields `is_tight_sl: false` (strict inequality — the gate must not fire on the boundary).
- [ ] `trend_state` is `BULLISH` when close > `ema_50` > `sma_200`, `BEARISH` when close < `ema_50` < `sma_200`, else `MIXED`.
- [ ] `ema_20` is present and equals the frozen EMA convention, so stage 10 can evaluate the §11 Level-2 "breaks 20 EMA" rule.
- [ ] **Waiver path:** throttled/short-history/unavailable OHLCV yields `waived: true` with every metric `null` — and exit code 0. It must **never** fabricate a value and never report `is_tight_sl: false` as a default (Zero Hallucination + BRD §10.3).
- [ ] A `current_trade.json` missing `live_price` exits with code 2 and `ERR_MISSING_LIVE_PRICE` (mirrors stage 0's `ERR_OPTIONS_PROHIBITED` convention).
- [ ] Runs and passes with **zero network calls**: the seam replays a recorded fixture, so the test is deterministic and offline.
- [ ] Emits `as_of` provenance so every reported ATR traces to a dated local payload (Zero Hallucination, evals dimension 1).
- [ ] Byte-identical payload for repeated runs over the same fixture (no time-of-day or ordering nondeterminism).

### 4. Implementation Checklist

- [ ] Write the failing seam in `tests/test_2_market_data.py` first (`/tdd`).
- [ ] Implement the minimum code in `scripts/2_market_data.py`.
- [ ] Verify the boundary case (exactly $0.75 \times \text{ATR}$) and the waiver path are both covered.
- [ ] Confirm `tests/run_all_tests.py` stays green, including `tests/test_contract_consistency.py`.
- [ ] Once the *"5 High Volatility / Tight Stop Traps"* records exist, verify this stage's flag yields the expected 🔴 NO-GO within `tests/run_evals.py` (evals dimension 2).
