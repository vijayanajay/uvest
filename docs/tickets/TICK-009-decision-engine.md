# TICK-009: Consolidated Multi-Factor Decision Engine

**Status:** FROZEN  
**Priority:** P0  
**Test Seam:** `tests/test_9_decision_engine.py`  
**Target Script:** `scripts/9_decision_engine.py`  
**Output Artifact:** `out_9_decision.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 9)

### 1. Goal & Context

Stage 9 is the **Master Decision Engine and Synthesis Layer** (BRD §10, §9.1 row 9). It aggregates the JSON artifacts from all upstream stages (`0` through `8`) to produce the unified trade verdict.

Key responsibilities:
1. **Multi-Dimensional 100-Point Model (BRD §10.2):**
   - Math & R:R Geometry: 20 pts
   - Quant & ML Edge (DTW + LightGBM): 25 pts
   - Institutional Order Flow: 20 pts
   - Fundamental Health & Governance: 15 pts
   - Sector RS & Macro VIX Regime: 15 pts
   - Intraday Precision Execution: 5 pts
2. **Hard Circuit Breaker Override (BRD §10.3):** If ANY of the 6 retained hard circuit breakers trip (Promoter Pledge $>25\%$, Inverted R:R, F&O Ban, Futures Earnings $<7$d, Volatility Noise Trap, Panic Regime), the verdict is forced to **🔴 AVOID** regardless of score. The single exception is breaker #2: a live $R:R < 1.0$ becomes **⏳ WAIT** when an optimal limit order restores $R:R \ge 1.5$.
3. **Waiver Rule (BRD §10.4):** Waived stages deduct from available points; effective score is computed as $\text{score} / \text{max\_available} \times 100$, and $\ge 1$ waived stage caps the verdict at **🟡 GO_WEAK (50% size)**. Missing data never inflates, never penalizes, and never fabricates a false rejection.
4. **Verdicts (BRD §10.1, §10.4):** The composite score is a *calibration input* to the calibrated $P(\text{win})$, never a pass/fail cutoff:
   - Calibrated $P(\text{win}) \ge 55\%$ **and** $R:R \ge 1.5$ **and** no breaker tripped $\implies$ 🟢 **GO (EXECUTE FULL SIZE)**
   - Positive expectancy but $\ge 1$ stage waived (or a BRD §6.4 size cap) $\implies$ 🟡 **GO_WEAK (REDUCE SIZE 50%)**
   - Entry/timing slipped but an optimal limit order restores $R:R \ge 1.5$ $\implies$ ⏳ **WAIT**; composite negative expectancy $\implies$ 🔴 **AVOID (DO NOT ENTER)**
5. **The Three Core Predictions & Decision Flip Table (BRD §4, §16):** Assembles Direction, Expected Volatility Cone, and Optimal Execution Zone, along with the market vs limit order Flip Table. The flip table is also the source of the mandatory flip condition: every non-`GO` verdict must carry `what_would_flip_me` (BRD §10.4 Flip Condition Invariant).

### 2. Frozen Specification Reference

- `BRD.md` §4, §10.1, §10.2, §10.3, §10.4, §15.3 (gate 9), §16.
- `BRD.md` §9 Script Inventory: `final_score`, `decision`, `circuit_breakers_tripped`, `three_predictions`, `flip_table`, `p_win`, `expectancy`, `verdict`, `what_would_flip_me`.
- `BRD.md` §15.3 gate 9 (100% circuit-breaker enforcement) and `docs/agents/evals.md` §3 dimension 2.
- **Inputs:** `current_trade.json`, `out_1_math.json` through `out_8_dhan.json`. **Output:** `out_9_decision.json`.

**Canonical output contract** (mirrors `BRD.md` §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `final_score` | float | Composite score (0–100) — a calibration input, never a cutoff |
| `max_possible_score` | float | 100 minus waived points |
| `effective_score_pct`| float | $(\text{final\_score} / \text{max\_possible\_score}) \times 100$ |
| `decision` | str | Canonical 7-state verdict (BRD §10.4): `GO` \| `GO_WEAK` \| `WAIT` \| `AVOID` \| `HOLD` \| `PRESSURE` \| `EXIT` |
| `verdict` | str | Same token as `decision`, retained because the BRD §9 inventory froze both names |
| `p_win` | float | Calibrated probability of win at the trade's own geometry |
| `expectancy` | float | Expected value of the setup in R |
| `what_would_flip_me` | list \| dict \| null | Mandatory for every non-`GO` verdict (BRD §10.4 Flip Condition Invariant) |
| `avoid_reason` / `confidence_deficit` | str \| null | Required by §10.4 for `AVOID` / `GO_WEAK` respectively |
| `circuit_breakers_tripped` | list[str] | Triggered hard circuit breakers (BRD §10.3) |
| `three_predictions` | dict | Consolidated Direction, Range Cone, and Optimal Limit Zone |
| `flip_table` | dict | Score & R:R comparison: Market Order vs Optimal Limit Order |

### 3. TDD Acceptance Criteria

- [ ] Ingests upstream JSON artifacts and calculates weighted composite score.
- [ ] Hard Breaker Override: any tripped hard breaker forces `decision: "AVOID"` and records it; breaker #2 alone yields `decision: "WAIT"` once a limit order at the optimal zone restores $R:R \ge 1.5$.
- [ ] Correctly applies waiver rule: adjusts `max_possible_score` when any stage is marked `waived: true`.
- [ ] Maps calibrated $P(\text{win})$, $R:R$ geometry, and the breaker set onto the canonical 7-state vocabulary (BRD §10.4) — the composite score is never a cutoff: $\ge 55\%$ with $R:R \ge 1.5$ and no breaker $\implies$ `GO`; $\ge 1$ waived stage $\implies$ `GO_WEAK`; slipped entry recoverable by limit $\implies$ `WAIT`; composite negative expectancy $\implies$ `AVOID`.
- [ ] Enforces the Flip Condition Invariant: every non-`GO` verdict carries `what_would_flip_me`; a verdict without one is rejected as un-calibrated noise.
- [ ] Emits only canonical verdict tokens — a retired `CONDITIONAL_GO`/`NO_GO` string can never be produced (guarded by `tests/test_contract_consistency.py`).
- [ ] Synthesizes the Three Core Predictions and Decision Flip Table into structured JSON.
- [ ] Tested with zero network dependencies using recorded stage fixtures.
- [x] Scoring vocabulary pinned to stage 7's frozen tokens: `score_regime` reads `nifty_trend` in `ABOVE_50_200_DMA` / `BELOW_50_ABOVE_200` / `BELOW_200_DMA` (BRD §7.2), never the retired `BULLISH`/`NEUTRAL`/`BEARISH` keys no producer emits — seam `test_frozen_nifty_trend_tokens_are_the_scored_vocabulary` in `tests/test_9_decision_engine.py` (ADR-010 decision 1).
- [x] Consume stage 7's macro sizing signals through the §6.4 size-cap lever (ADR-010 decision 6): `breadth_regime: RISK_OFF` (BRD §7.2: all long setups gated or reduced) or `fii_dii_flow_bias: NET_SELLING` below $-\text{₹}5,000\ \text{Cr}$ (BRD §7.4: cautious sizing) caps a would-be `GO` at `GO_WEAK` (50% size) with `confidence_deficit` naming each source; both together cap at the same 50% and never compound; a waived/absent `out_7_regime.json` caps nothing, and neither signal is a veto — the fatal breaker remains exactly BRD §10.3 #6.
