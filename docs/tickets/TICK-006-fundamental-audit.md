# TICK-006: Fundamental & Corporate Governance Auditor

**Status:** FROZEN  
**Priority:** P1  
**Test Seam:** `tests/test_6_fundamental_audit.py`  
**Target Script:** `scripts/6_fundamental_audit.py`  
**Output Artifact:** `out_6_fundamental.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 6)

### 1. Goal & Context

Stage 6 is the **Fundamental Quality & Governance Hazard Auditor** (BRD §6, §9.1 row 6). No quantitative setup survives accounting fraud, promoter debt liquidation, or surprise earnings collapse.

This stage evaluates three core risk dimensions:
1. **Financial Health & Solvency:** Piotroski F-Score (scale 0–9, penalty if $F \le 4$), Altman Z''-score (distress check).
2. **Cash Flow Quality:** CFO-to-PAT ratio ($\text{CFO}/\text{PAT} < 0.6$ flags accrual anomaly / paper profits).
3. **Indian Governance Kill Switches & Calendar Hazards:**
   - **Promoter Pledging (BRD §10.2 #2):** If pledged shares $> 15\%$, warning. If $> 25\%$, **INSTANT HARD REJECTION (NO-GO)**.
   - **Corporate Earnings Calendar Hazard (BRD §10.2 #4):** If quarterly results are scheduled within $< 7$ days on Futures $\implies$ **HARD NO-GO**; within $< 10$ days on Cash $\implies$ **CONDITIONAL GO (50% size)**.

### 2. Frozen Specification Reference

- `BRD.md` §6.1, §6.2, §6.3, §6.4.
- `BRD.md` §9 Script Inventory: `piotoski_score`, `promoter_pledge_pct`, `pledge_circuit_breaker`, `cfo_pat_ratio`, `days_to_earnings`.
- `BRD.md` §10.2 #2 (Promoter Pledging $> 25\%$ hard kill switch) & #4 (Earnings $< 7$d on Futures).
- `docs/agents/evals.md` — Golden dataset classes *"5 High Promoter Pledging Traps"* and *"5 Impending Earnings Event Traps"*.
- **Input:** `current_trade.json`. **Output:** `out_6_fundamental.json`.

**Canonical output contract** (mirrors `BRD.md` §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `piotoski_score` | int \| null | Piotroski F-score (0–9) |
| `altman_z_score` | float \| null | Altman Z''-score for emerging markets |
| `promoter_pledge_pct` | float \| null | Promoter pledged shares as % of promoter holding |
| `pledge_circuit_breaker` | bool \| null | `true` if promoter pledge $> 25.0\%$ (instant NO-GO) |
| `cfo_pat_ratio` | float \| null | Operating Cash Flow / Net Profit After Tax |
| `days_to_earnings` | int \| null | Trading days until next scheduled quarterly earnings announcement |
| `earnings_hazard_flag` | bool \| null | `true` if earnings within $< 7$ days on Futures trade |
| `waived` | bool | `true` if fundamental data is unavailable |

### 3. TDD Acceptance Criteria

- [ ] Consumes canonical `current_trade.json` and evaluates corporate fundamentals.
- [ ] Sets `pledge_circuit_breaker: true` if and only if `promoter_pledge_pct > 25.0%`.
- [ ] Flags `earnings_hazard_flag: true` when `days_to_earnings < 7` for `instrument_type == 'FUT'`.
- [ ] Computes Piotroski score (0–9) and CFO/PAT ratio correctly from financial statement data.
- [ ] Supports recorded offline test fixtures with zero network calls.
- [ ] Waiver path: Missing financial reports yields `waived: true` with null metrics and exit code 0.
