# TICK-002: Mathematical R:R, Slippage & Margin Engine

**Status:** FROZEN  
**Priority:** P0  
**Test Seam:** `tests/test_1_math_and_rr.py`  
**Target Script:** `scripts/1_math_and_rr.py`  
**Output Artifact:** `out_1_math.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 1)

### 1. Goal & Context

Stage 1 is the **sole owner of mathematical risk-to-reward symmetry, execution slippage, and capital allocation** (BRD §9.1 row 1). It consumes `current_trade.json` (stage 0) and computes the foundational mathematical layer that later stages score against:

1. **Raw vs. Live R:R:** Evaluates whether market price has drifted away from the advisory's recommended entry midpoint.
2. **Execution Slippage:** Measures drift outside the recommended entry band as a percentage of band width.
3. **₹ Risk & Reward per Position:** Computes total currency risk and reward based on instrument type (`lot_size` for Futures, 1 share for Cash/Equity).
4. **Inverted R:R Circuit Breaker:** Implements BRD §10.3 #2 — if live $R:R < 1.0$ (risking ₹1 to make less than ₹1), flags `inverted_rr_flag: true`. The flag is a hazard *input*, not a verdict: stage 9 converts it into ⏳ `WAIT` when an optimal limit order restores $R:R \ge 1.5$, and 🔴 `AVOID` otherwise (BRD §10.4).

**Deliberately out of scope (do not build here):**
- Technical indicators / ATR / Moving averages → stage 2 (`scripts/2_market_data.py`).
- Historical analog matching → stage 3 (`scripts/3_dtw_pattern.py`).
- 100-point composite score calculation → stage 9 (`scripts/9_decision_engine.py`).

### 2. Frozen Specification Reference

- `BRD.md` §9.1 stage 1 (contract row) and §2 (`scripts/1_math_and_rr.py` — R:R Symmetry, Lot Sizing, Slippage).
- `BRD.md` §9 Script Inventory payload row for `1_math_and_rr.py`: `raw_rr`, `live_rr`, `risk_inr`, `reward_inr`, `outside_range_pct`, `inverted_rr_flag`.
- `BRD.md` §10.3 #2 — **Inverted Risk-Reward ($R:R < 1.0$):** Retained hard circuit breaker (BRD §10.3). Overridden to ⏳ `WAIT` if an optimal limit order restores $R:R \ge 1.5$; 🔴 `AVOID` otherwise. Boundary case: live $R:R = 1.0$ does **not** trip the breaker (strict inequality `< 1.0`).
- `BRD.md` §16 — Worked example parameters (`RELIANCE` FUT: Entry Band 2975–2985, Live 2988.20, SL 2910, Target 3120, Lot 250).
- `docs/agents/domain.md` — Definitions of Lot Size, Futures vs. Cash equity sizing.
- `docs/agents/evals.md` — Golden dataset class *"10 Inverted Risk-Reward / Slippage Traps"*.
- **Input:** `current_trade.json` via `--input <path>`. **Output:** `out_1_math.json` to stdout.

**Canonical output contract** (mirrors `BRD.md` §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `symbol` | str | Ticker symbol |
| `instrument_type` | str | `FUT` or `CASH`/`EQUITY` |
| `raw_rr` | float | Nominal R:R at recommended entry midpoint: $(\text{Target} - \text{Mid}) / (\text{Mid} - \text{SL})$ |
| `live_rr` | float | Realized R:R at current live market price: $(\text{Target} - \text{Live}) / (\text{Live} - \text{SL})$ |
| `outside_range_pct` | float | Slippage beyond band as % of band width (0.0 if inside band) |
| `risk_inr` | float | Position risk in ₹: $(\text{Live} - \text{SL}) \times \text{Qty}$ |
| `reward_inr` | float | Position reward in ₹: $(\text{Target} - \text{Live}) \times \text{Qty}$ |
| `inverted_rr_flag` | bool | `true` iff `live_rr < 1.0` (strict inequality) |
| `user_capital_inr` | float | User's total available trading capital |
| `capital_allocation_pct`| float | $(\text{risk\_inr} / \text{user\_capital\_inr}) \times 100$ |

### 3. Frozen Mathematical Conventions

1. **Midpoint & Raw R:R:**
   $$\text{Midpoint} = \frac{\text{entry\_range\_min} + \text{entry\_range\_max}}{2}$$
   $$\text{Raw R:R} = \frac{\text{target} - \text{Midpoint}}{\text{Midpoint} - \text{stoploss}}$$
2. **Live R:R:**
   $$\text{Live R:R} = \frac{\text{target} - \text{live\_price}}{\text{live\_price} - \text{stoploss}}$$
3. **Execution Slippage (`outside_range_pct`):**
   - If $\text{live\_price} > \text{entry\_range\_max}$:
     $$\text{outside\_range\_pct} = \frac{\text{live\_price} - \text{entry\_range\_max}}{\text{entry\_range\_max} - \text{entry\_range\_min}} \times 100$$
   - If $\text{live\_price} < \text{entry\_range\_min}$:
     $$\text{outside\_range\_pct} = \frac{\text{entry\_range\_min} - \text{live\_price}}{\text{entry\_range\_max} - \text{entry\_range\_min}} \times 100$$
   - If inside band: $\text{outside\_range\_pct} = 0.0$ (never negative).
4. **Position Sizing:**
   - For `FUT`: $\text{Quantity} = \text{lot\_size}$
   - For `CASH` / `EQUITY`: $\text{Quantity} = 1$ (per share evaluation)
5. **Inverted R:R Circuit Breaker:**
   - Tripped iff $\text{live\_rr} < 1.0$. If $\text{live\_rr} == 1.0$, `inverted_rr_flag: false`.

### 4. TDD Acceptance Criteria

- [x] Reads canonical `current_trade.json` via `--input` CLI flag and writes JSON to stdout.
- [x] Calculates `raw_rr` from band midpoint and `live_rr` from current `live_price`.
- [x] Computes `outside_range_pct` correctly when price slips above or below band, and returns `0.0` when inside band.
- [x] Multiplies ₹ risk and reward by `lot_size` for Futures, and uses `1` for Cash/Equity.
- [x] Sets `inverted_rr_flag: true` when `live_rr < 1.0`, and `false` when `live_rr >= 1.0` (including exact boundary 1.0).
- [x] Exits with error code 1 and `ERR_PARSE` message on missing required fields or invalid ranges.
- [x] Integrates with `--record <dir>` bundle helper for deterministic replay.
- [x] Tested with zero network dependencies in `tests/test_1_math_and_rr.py`.
