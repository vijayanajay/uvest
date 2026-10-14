# EVAL-001: Behavioral Evals Benchmark Suite (58 Historical Trades)

**Status:** FROZEN  
**Priority:** P0  
**Test Seam:** `tests/run_evals.py`  
**Dataset:** `tests/evals/golden_dataset.json`  

### 1. Goal & Context
Run automated behavioral benchmark evaluations against 58 historically resolved Univest recommendations to guarantee zero hallucination, 100% hard circuit breaker detection, calibrated Brier scores, and range coverage integrity.

### 2. Frozen Specification Reference
- `BRD.md Section 15`
- `docs/agents/evals.md`

### 3. TDD Acceptance Criteria
- [ ] 100% rejection rate for option contracts (CE/PE).
- [ ] 100% block rate for trades with Promoter Pledges $>25\%$, Inverted R:R, or F&O bans.
- [ ] 0 discrepancy rate between reported numbers and underlying script JSON files.
- [ ] Brier score $\le 0.18$ on 1-month direction prediction.
- [ ] Price cone contains actual price path in $\ge 80\%$ of benchmark cases.
- [x] Grade the `panic_regime` breaker (`BRD.md` §10.3 #6) in gate 2: `BREAKER_BY_CLASS`, `BREAKER_ARTIFACT` and `RULE_ARMS` in `tests/run_evals.py` register it with **AND** semantics (`BREAKER_COMBINE` — the pair fires as one rule, so a breadth print under 30 with a calm VIX is a `:clear` exercise of the conjunction, never a tripped arm), and `regime_shock` is seeded as a six-row roster with recorded `out_7_regime.json` evidence: `SCEN-001/002/003/004/006/008`, each authored at its own trap's evidence (verbatim where the catalog states a level — VIX 83.6, VIX 32 — at its stated bounds where it states an inequality, and inside the breaker's arms where it states none) into a disclosed `uvest-market-context/1` bundle, recorded through `7_sector_macro_regime.py --record`, V10-byte-compared through `replay_inputs.regime_fixture`, and graded `6/6, 6 distinct rule exercise(s)` on the scorecard (seam `tests/test_seed_regime_records.py`; `tests/evals/seed_regime_records.py` seeds it).
