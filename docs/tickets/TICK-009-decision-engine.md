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
1. **Multi-Dimensional 100-Point Model (BRD §10.1):**
   - Math & R:R Geometry: 20 pts
   - Quant & ML Edge (DTW + LightGBM): 25 pts
   - Institutional Order Flow: 20 pts
   - Fundamental Health & Governance: 15 pts
   - Sector RS & Macro VIX Regime: 15 pts
   - Intraday Precision Execution: 5 pts
2. **Hard Disqualifiers / Circuit Breaker Override (BRD §10.2):** If ANY of the 6 hard gates trip (Inverted R:R, Pledge $>25\%$, F&O Ban, Earnings $<7$d on FUT, SL $<0.75\times\text{ATR}$, Panic Regime), overrides verdict to **🔴 NO-GO (REJECT)** regardless of score.
3. **Waiver Rule (BRD §10.3):** Waived stages deduct from available points; effective score is computed as $\text{score} / \text{max\_available} \times 100$. Missing data never inflates or unfairly penalizes.
4. **Verdicts (BRD §10.3):**
   - $\ge 80\%$ Effective Score $\implies$ 🟢 **GO (EXECUTE FULL SIZE)**
   - $65\% – 79\%$ Effective Score $\implies$ 🟡 **CONDITIONAL GO (REDUCE SIZE 50%)**
   - $< 65\%$ Effective Score $\implies$ 🔴 **NO-GO (REJECT)**
5. **The Three Core Predictions & Decision Flip Table (BRD §4, §16):** Assembles Direction, Expected Volatility Cone, and Optimal Execution Zone, along with the market vs limit order Flip Table.

### 2. Frozen Specification Reference

- `BRD.md` §4, §10.1, §10.2, §10.3, §16.
- `BRD.md` §9 Script Inventory: `final_score`, `decision`, `circuit_breakers_tripped`, `three_predictions`, `flip_table`.
- `docs/agents/evals.md` §3 (Dimension 2: 100% hard breaker enforcement).
- **Inputs:** `current_trade.json`, `out_1_math.json` through `out_8_dhan.json`. **Output:** `out_9_decision.json`.

**Canonical output contract** (mirrors `BRD.md` §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `final_score` | float | Composite score (0–100) |
| `max_possible_score` | float | 100 minus waived points |
| `effective_score_pct`| float | $(\text{final\_score} / \text{max\_possible\_score}) \times 100$ |
| `decision` | str | `GO` \| `CONDITIONAL_GO` \| `NO_GO` |
| `circuit_breakers_tripped` | list[str] | List of triggered disqualifiers |
| `three_predictions` | dict | Consolidated Direction, Range Cone, and Optimal Limit Zone |
| `flip_table` | dict | Score & R:R comparison: Market Order vs Optimal Limit Order |

### 3. TDD Acceptance Criteria

- [ ] Ingests upstream JSON artifacts and calculates weighted composite score.
- [ ] Hard Breaker Override: If any circuit breaker is active, forces `decision: "NO_GO"` and records tripped breakers.
- [ ] Correctly applies waiver rule: adjusts `max_possible_score` when any stage is marked `waived: true`.
- [ ] Strictly applies decision cutoffs: $\ge 80\% \implies \text{GO}$, $65\%-79\% \implies \text{CONDITIONAL\_GO}$, $< 65\% \implies \text{NO\_GO}$.
- [ ] Synthesizes the Three Core Predictions and Decision Flip Table into structured JSON.
- [ ] Tested with zero network dependencies using recorded stage fixtures.
