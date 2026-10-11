# TICK-007: Sector Relative Strength & Macro VIX Regime

**Status:** FROZEN  
**Priority:** P1  
**Test Seam:** `tests/test_7_sector_macro_regime.py`  
**Target Script:** `scripts/7_sector_macro_regime.py`  
**Output Artifact:** `out_7_regime.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 7)

### 1. Goal & Context

Stage 7 is the **Sector Relative Strength and Macro Regime Classifier** (BRD §7, §9.1 row 7). A stock rarely swims against a collapsing sectoral or macroeconomic market tide.

This stage evaluates four macro/sector components:
1. **Sectoral Mansfield Relative Strength (MRS):** Rolling 50-day sector-to-NIFTY performance and RRG quadrant (`LEADING`, `WEAKENING`, `LAGGING`, `IMPROVING`).
2. **NIFTY 50 Macro Tape & Breadth:** NIFTY 50 relative to 50/200 DMA; NSE 500 market breadth (% trading above 200 DMA).
3. **India VIX Volatility Regime:** Categorized into Low (<12), Normal (12–18), Elevated (18–24), and Panic (>24).
4. **Panic Volatility Regime Breaker (BRD §10.3 #6):** If India VIX $> 24$ and Market Breadth $< 30\%$ bullish $\implies$ **🔴 AVOID** for all long setups.
5. **Institutional Macro Flows:** Rolling 5-day combined FII + DII net cash flow bias.

### 2. Frozen Specification Reference

- `BRD.md` §7.1, §7.2, §7.3, §7.4.
- `BRD.md` §9 Script Inventory: `sector_mrs`, `sector_rrg_state`, `nifty_trend`, `vix_regime`, `fii_dii_flow_bias`.
- `BRD.md` §10.3 #6 (Panic Volatility Regime: VIX $> 24$ and Breadth $< 30\%$), and §10.4 for the verdict it produces.
- `docs/agents/domain.md` — Mansfield Relative Strength formula.
- **Input:** `current_trade.json`. **Output:** `out_7_regime.json`.

**Canonical output contract** (mirrors `BRD.md` §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `sector_mrs` | float \| null | Mansfield Relative Strength of parent sector vs NIFTY 50 |
| `sector_rrg_state` | str \| null | `LEADING` \| `WEAKENING` \| `LAGGING` \| `IMPROVING` |
| `nifty_trend` | str \| null | `ABOVE_50_200_DMA` \| `BELOW_50_ABOVE_200` \| `BELOW_200_DMA` |
| `market_breadth_pct`| float \| null | % of NSE 500 stocks trading above 200 DMA |
| `vix_value` | float \| null | India VIX current value |
| `vix_regime` | str \| null | `LOW` \| `NORMAL` \| `ELEVATED` \| `PANIC` |
| `fii_dii_flow_bias` | str \| null | `NET_BUYING` \| `NEUTRAL` \| `NET_SELLING` |
| `panic_regime_breaker`| bool \| null | `true` if VIX $> 24.0$ and Breadth $< 30.0\%$ (forces 🔴 AVOID, BRD §10.3 #6) |
| `waived` | bool | `true` if index data is unavailable |

### 3. TDD Acceptance Criteria

- [ ] Consumes canonical `current_trade.json` and isolates the target sector index.
- [ ] Computes Mansfield RS over 50-period SMA and maps to RRG quadrant.
- [ ] Flags `panic_regime_breaker: true` when VIX $> 24$ and breadth $< 30\%$.
- [ ] Correctly classifies VIX into LOW (<12), NORMAL (12-18), ELEVATED (18-24), PANIC (>24).
- [ ] Operates offline with zero network calls against recorded index fixtures.
- [ ] Waiver path: Missing index data yields `waived: true` with null metrics and exit code 0.
