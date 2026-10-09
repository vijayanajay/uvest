---
name: postmortem
description: "Decomposes closed or failed trade recommendations into root causes (volatility noise, regime shock, institutional distribution, event shock, slippage) and updates calibration weights. Trigger whenever a trade hits stop loss, expires, or resolves."
---

# Post-Mortem Failure Autopsy & Learning Machine

Systematic root-cause decomposition of resolved recommendations in `uvest_history.db` to prevent repeating identical failures.

## Root Cause Classification Matrix
1. `VOLATILITY_NOISE`: Stop hit intraday, but stock closed above stop price by day end. (ATR buffer too tight).
2. `REGIME_SHOCK`: NIFTY crashed $>1.5\%$ or India VIX surged $>15\%$ on the stopout day. (Macro beta overrode alpha).
3. `INSTITUTIONAL_DISTRIBUTION`: Delivery % was low or Futures OI was unwinding during initial rally. (Retail bull trap).
4. `EVENT_SURPRISE`: Unscheduled corporate filing, credit downgrade, or sudden news. (Tighten corporate announcement filters).
5. `ENTRY_SLIPPAGE`: Entered outside recommended price band. Had user waited for optimal limit zone, trade would have been a WIN.

## Prior & Threshold Update
- Re-calibrates Brier scores and decision thresholds in `thresholds.json`.
- Logs ticker-specific error patterns to prevent recurring losses on the same stock.

## Execution
```bash
python scripts/11_postmortem_learning.py --resolve <TRADE_ID> --outcome LOSS
```
