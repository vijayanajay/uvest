# EVAL-001: Behavioral Evals Benchmark Suite (52 Historical Trades)

**Status:** FROZEN  
**Priority:** P0  
**Test Seam:** `tests/run_evals.py`  
**Dataset:** `tests/evals/golden_dataset.json`  

### 1. Goal & Context
Run automated behavioral benchmark evaluations against 52 historically resolved Univest recommendations to guarantee zero hallucination, 100% hard circuit breaker detection, calibrated Brier scores, and range coverage integrity.

### 2. Frozen Specification Reference
- `BRD.md Section 15`
- `docs/agents/evals.md`

### 3. TDD Acceptance Criteria
- [ ] 100% rejection rate for option contracts (CE/PE).
- [ ] 100% block rate for trades with Promoter Pledges $>25\%$, Inverted R:R, or F&O bans.
- [ ] 0 discrepancy rate between reported numbers and underlying script JSON files.
- [ ] Brier score $\le 0.18$ on 1-month direction prediction.
- [ ] Price cone contains actual price path in $\ge 80\%$ of benchmark cases.
