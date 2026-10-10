# TICK-005: NSE Bhavcopy Delivery % × Futures OI Matrix

**Status:** FROZEN  
**Priority:** P0  
**Test Seam:** `tests/test_5_institutional_flow.py`  
**Target Script:** `scripts/5_institutional_flow.py`  
**Output Artifact:** `out_5_flow.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 5)

### 1. Goal & Context

Stage 5 is the **Institutional Order Flow & Footprint Engine** (BRD §5.3, §9.1 row 5). In the Indian market, retail traders churn intraday volume while institutional desks accumulate via high delivery percentages and sustained Futures Open Interest (OI) buildup.

This stage evaluates the **Holy Trinity Matrix** (BRD §5.3):
1. **Aggressive Long Buildup (Institutional Accumulation):** Price UP + Delivery % High (>50% or +1.5σ) + Futures OI Rising (>+5%) $\implies$ Strong Buy Confirmation.
2. **Retail Short-Covering Trap (Exhaustion Rally):** Price UP + Delivery % Low (<30% or -1.0σ) + Futures OI Falling (<-5%) $\implies$ Reject Buy (Bull trap).
3. **Short Buildup (Institutional Distribution):** Price DOWN + Delivery % High + Futures OI Rising $\implies$ Reject Buy.
4. **Long Unwinding (Capitulation):** Price DOWN + Delivery % Low + Futures OI Falling $\implies$ Wait for Absorption.

For Futures recommendations, it also computes **Futures Basis** ($P_{\text{FUT}} - P_{\text{Spot}}$) and near-expiry rollover percentage (BRD §5.4).

### 2. Frozen Specification Reference

- `BRD.md` §5.3 (The Holy Trinity Matrix).
- `BRD.md` §5.4 (Futures Basis, Rollover & Carrying Cost).
- `BRD.md` §9 Script Inventory: `delivery_pct`, `delivery_zscore`, `oi_change_pct`, `matrix_verdict`, `basis_annualized`.
- `BRD.md` §10.1 Scoring: Institutional Order Flow contributes 20 points.
- `docs/agents/evals.md` — Golden dataset class *"10 Institutional Distribution Traps"*.
- **Input:** `current_trade.json`. **Output:** `out_5_flow.json`.

**Canonical output contract** (mirrors `BRD.md` §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `delivery_pct` | float \| null | Delivery quantity as % of total traded volume |
| `delivery_zscore` | float \| null | Delivery % Z-score relative to 20-day rolling average |
| `oi_change_pct` | float \| null | Daily % change in Futures Open Interest |
| `matrix_verdict` | str \| null | `LONG_BUILDUP` \| `SHORT_COVERING_TRAP` \| `SHORT_BUILDUP` \| `LONG_UNWINDING` |
| `basis_annualized` | float \| null | Annualized Spot-Futures Basis % |
| `rollover_pct` | float \| null | Expiry week rollover % vs 3-month average (null if not expiry week) |
| `waived` | bool | `true` if Bhavcopy data is missing |

### 3. TDD Acceptance Criteria

- [ ] Consumes canonical `current_trade.json` and evaluates Delivery % and Futures OI.
- [ ] Correctly classifies each combination of Price Change, Delivery %, and OI Change into the 4 Holy Trinity Matrix regimes.
- [ ] Detects Retail Short-Covering Traps: rising price with falling OI and below-average delivery %.
- [ ] Calculates annualized basis for Futures contracts; flags discount/inversion during uptrend as hedge/distribution warning.
- [ ] Replays recorded Bhavcopy fixture offline with zero network dependency.
- [ ] Waiver path: Missing Bhavcopy yields `waived: true` with null metrics and exit code 0.
