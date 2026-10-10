# TICK-012: Continuous Learning & Failure Autopsy Engine

**Status:** FROZEN  
**Priority:** P2  
**Test Seam:** `tests/test_11_postmortem_learning.py`  
**Target Script:** `scripts/11_postmortem_learning.py`  
**Output Artifact:** `out_11_postmortem.json`, `thresholds.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 11)

### 1. Goal & Context

Stage 11 is the **Continuous Learning and Post-Mortem Autopsy Engine** (BRD §12, §9.1 row 11).
Whenever a tracked recommendation resolves (hits Target, hits Stop Loss, or reaches time expiry), Stage 11 executes an automated autopsy.

Key operations:
1. **The 5-Category Failure Decomposition (BRD §12.1):**
   - `VOLATILITY_NOISE`: Stop hit intraday but stock closed above stop (ATR buffer was too tight).
   - `REGIME_SHOCK`: NIFTY fell $> 1.5\%$ or VIX spiked $> 15\%$ on stopout day (macro beta overrode alpha).
   - `INSTITUTIONAL_DISTRIBUTION`: Low delivery % or falling OI during initial rally (retail bull trap).
   - `EARNINGS_SURPRISE`: Unscheduled filing, downgrade, or binary news event.
   - `ENTRY_SLIPPAGE`: Entered outside recommended band; waiting for optimal limit zone would have won.
2. **Bayesian Self-Audit & Calibration:**
   - Evaluates Brier score calibration on completed predictions.
   - Updates Bayesian feature scoring weights in `thresholds.json` (Stage 11 is the sole writer of `thresholds.json`).

### 2. Frozen Specification Reference

- `BRD.md` §12.1 (Post-Mortem Failure Autopsy Matrix).
- `BRD.md` §9.1 (Conditional stage: runs only on resolved trades; sole writer of `thresholds.json`).
- `BRD.md` §9 Script Inventory: `resolved_trades`, `root_cause_breakdown`, `brier_calibration`, `weight_adjustments`.
- **Database:** Local SQLite `uvest_history.db`.
- **Outputs:** `out_11_postmortem.json` to stdout, `thresholds.json` to disk.

**Canonical output contract** (mirrors `BRD.md` §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `resolved_trades` | list[dict] | Summary of processed resolved trades |
| `root_cause_breakdown` | dict | Count and % of losses across the 5 failure taxonomy buckets |
| `brier_calibration` | float | Realized Brier calibration score across resolved trades |
| `weight_adjustments` | dict | Bayesian adjustments applied to scoring weights |

### 3. TDD Acceptance Criteria

- [ ] Invoked with `--resolve <TRADE_ID> --outcome LOSS/WIN` to process trade resolution.
- [ ] Correctly decomposes loss into one of the 5 mutually exclusive root causes.
- [ ] Sole writer of `thresholds.json`; preserves valid JSON formatting and bounds weights.
- [ ] Leaves open positions untouched in `uvest_history.db`.
- [ ] Tested offline with temporary SQLite database and recorded market data.
