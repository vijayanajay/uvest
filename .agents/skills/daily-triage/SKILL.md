---
name: daily-triage
description: "Audits active open recommendations and manages trades moving in an adverse direction using the 3-stage protocol. Trigger whenever the user asks 'what should I do today?', 'check my active trades', or reports a trade moving against them."
---

# Daily Position Triage & Adverse Movement Protocol

Automated daily audit of active positions in `uvest_history.db` against live market prices and microstructure.

## Daily Triage Zones
1. **Profit Harvesting Zone (Within 10% of Target):**
   - Action: Trail Stop Loss to Breakeven + 50% of unrealized gains.
2. **Healthy Runway Zone (Above Entry):**
   - Action: HOLD. Maintain original stop loss. Let mathematical edge work.
3. **Adverse Movement Protocol (Price Moving Against Entry):**
   - **Level 1 (Drawdown $\le 1.0 \times \text{ATR}$):** Normal pullback. Verify institutional delivery is holding. **DO NOTHING. NEVER AVERAGE DOWN.**
   - **Level 2 (Drawdown $> 1.0 \times \text{ATR}$ or breaks 20 EMA):** Premise warning. Cancel re-entry orders, tighten stop to previous day high/low.
   - **Level 3 (Stop Loss Hit or Premise Invalidation):** Hard liquidation immediately. No exceptions.
4. **Stagnation Zone (>12 days without movement):**
   - Futures: Exit on Friday to eliminate theta/carry drag. Cash: Hold.

## Execution
```bash
python scripts/10_daily_triage.py --action status
```
