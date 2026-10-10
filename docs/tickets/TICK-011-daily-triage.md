# TICK-011: Daily Position Triage & Adverse Movement Tracker

**Status:** FROZEN  
**Priority:** P1  
**Test Seam:** `tests/test_10_daily_triage.py`  
**Target Script:** `scripts/10_daily_triage.py`  
**Output Artifact:** `out_10_triage.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 10)

### 1. Goal & Context

Stage 10 is the **Daily Position Management and Adverse Movement Protocol Engine** (BRD §11, §9.1 row 10).
Once a trade recommendation is logged to `uvest_history.db`, Stage 10 runs daily prior to market open or post-close to answer: *"What should the user do today?"*

Key operations:
1. **Four Operational Zones (BRD §11.1):**
   - *Profit Harvesting:* Price within 10% of target $\implies$ Trail stop to breakeven + 50% unrealized gains.
   - *Healthy Runway:* Price consolidating above entry $\implies$ Hold, maintain stop loss.
   - *Adverse Movement:* Price drifting towards stop $\implies$ Enforce 3-Stage Adverse Protocol.
   - *Time Stop / Stagnation:* Active $> 12$ days without progress $\implies$ Close futures before weekend carry cost.
2. **Three-Stage Adverse Movement Protocol (BRD §11.2):**
   - **Level 1 (Drawdown $\le 1.0 \times \text{ATR}$):** Normal pullback $\implies$ **DO NOTHING. DO NOT AVERAGE DOWN.**
   - **Level 2 (Drawdown $> 1.0 \times \text{ATR}$ or breaks 20 EMA):** Warning $\implies$ **CANCEL RE-ENTRY. TIGHTEN STOP TO PREV HIGH/LOW.**
   - **Level 3 (Stop Hit or Invalidation):** Hard liquidation $\implies$ **NEVER AVERAGE DOWN. EXIT IMMEDIATELY.**

### 2. Frozen Specification Reference

- `BRD.md` §11.1 (The Four Triage Zones).
- `BRD.md` §11.2 (The Three-Stage Adverse Movement Protocol).
- `BRD.md` §9 Script Inventory: `open_trades_status`, `action_required`, `trailing_stop_update`, `premise_invalidated`.
- **Database:** Local SQLite `uvest_history.db`.
- **Output:** `out_10_triage.json`.

**Canonical output contract** (mirrors `BRD.md` §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `open_trades_status` | list[dict] | Status of all open tracked recommendations |
| `action_required` | str | `HOLD` \| `TRAIL_STOP` \| `TIGHTEN_STOP` \| `LIQUIDATE` |
| `trailing_stop_update` | float \| null | New calculated trailing stop level |
| `premise_invalidated` | bool | `true` if fundamental or structural support broke |
| `adverse_level` | int \| null | 1, 2, or 3 if trade is in drawdown |

### 3. TDD Acceptance Criteria

- [ ] Logs new trades into SQLite `uvest_history.db` and queries active positions.
- [ ] Classifies position into the 4 operational zones based on price progress.
- [ ] Level 1 drawdown ($\le 1.0\times\text{ATR}$) strictly prescribes `HOLD` with explicit prohibition against averaging down.
- [ ] Level 2 drawdown ($> 1.0\times\text{ATR}$ or close $< 20\text{ EMA}$) tightens stop and raises warning.
- [ ] Level 3 triggers instant liquidation when stop loss is breached.
- [ ] Tested with local in-memory/temp SQLite database and recorded price fixtures.
