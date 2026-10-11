# TICK-004: LightGBM 1-Month Direction & Volatility Cone

**Status:** FROZEN  
**Priority:** P1  
**Test Seam:** `tests/test_4_ml_direction.py`  
**Target Script:** `scripts/4_ml_direction.py`  
**Output Artifact:** `out_4_ml.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 4)

### 1. Goal & Context

Stage 4 is the **Machine Learning Directional and Volatility Range Engine** (BRD §5.2, §9.1 row 4). It consumes `current_trade.json`, daily OHLCV from stage 2, and historical features to deliver:
1. **Directional Probability:** LightGBM classifier prediction ($P(\text{Bullish})$, $P(\text{Neutral})$, $P(\text{Bearish})$) calibrated to achieve Brier score $\le 0.18$.
2. **22-Day Volatility Price Cone:** Statistical expected price envelope ($[P_{10}, P_{25}, P_{50}, P_{75}, P_{90}]$), projected Max Adverse Excursion (MAE), and Max Favorable Excursion (MFE).
3. **Feature Attribution:** Top SHAP drivers explaining the model prediction.

**Deliberately out of scope (do not build here):**
- FastDTW geometric pattern matching → stage 3 (`scripts/3_dtw_pattern.py`).
- Institutional order flow matrix → stage 5 (`scripts/5_institutional_flow.py`).
- Composite scoring & verdict → stage 9 (`scripts/9_decision_engine.py`).

### 2. Frozen Specification Reference

- `BRD.md` §4 (The Three Core Predictions: Direction & Expected Range).
- `BRD.md` §5.2 (LightGBM 1-Month Direction Engine).
- `BRD.md` §9 Script Inventory: `prob_target_hit_22d`, `price_cone_p10_p50_p90`, `mae_projected`, `shap_drivers`.
- `BRD.md` §10.4 Waiver Rule: If model weights or features are missing, returns `waived: true` with all metrics `null` and exit code 0. A waived stage caps the eventual verdict at 🟡 `GO_WEAK` (BRD §10.4).
- `BRD.md` §15.3 — **S2 (Calibration:** $|\text{predicted } P_{\text{win}} - \text{realized win frequency}| \le 10\text{pp}$ per verdict bucket) and **S7 (Marginal Band Honesty)**. These are the authoritative gates; `docs/agents/evals.md` §3 dimensions 4–5 (Brier $\le 0.18$, cone coverage $\ge 80\%$) remain this stage's own thresholds.

**Canonical output contract** (mirrors `BRD.md` §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `prob_target_hit_22d` | float \| null | Calibrated probability of reaching target before stop |
| `price_cone_p10_p50_p90` | dict \| null | Percentile price envelope: `{"p10", "p25", "p50", "p75", "p90"}` |
| `mae_projected` | float \| null | Projected Maximum Adverse Excursion in ₹ |
| `mfe_projected` | float \| null | Projected Maximum Favorable Excursion in ₹ |
| `shap_drivers` | list[dict] \| null | Top 5 predictive feature contributions |
| `direction_label` | str \| null | `STRONG_BULLISH` \| `BULLISH` \| `NEUTRAL` \| `BEARISH` |
| `waived` | bool | `true` if model/features unavailable |

### 3. TDD Acceptance Criteria

- [ ] Consumes canonical `current_trade.json` and evaluates 22-session forward trajectory.
- [ ] Price cone envelope contains $P_{10} \le P_{25} \le P_{50} \le P_{75} \le P_{90}$ monotonically.
- [ ] Validates whether recommended stoploss is safely positioned below projected MAE.
- [ ] Supports offline deterministic execution via recorded fixtures.
- [ ] On missing model artifacts, executes waiver path: `waived: true`, null fields, exit 0.
