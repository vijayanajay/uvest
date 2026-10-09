# Evals Specification & Benchmark Suite
*Configured via /setup-matt-pocock-skills for UVEST*

## 1. Core Purpose of Evals in UVEST
In AI-assisted quantitative development, **Evals (Evaluations)** are the behavioral benchmarks that prevent regressions, hallucinations, and heuristic drift. 

Before any production Python script is implemented, the evaluation criteria and golden test cases are frozen. The system must achieve a 100% pass rate on hard safety gates and meet defined statistical thresholds on empirical performance.

---

## 2. The 50-Trade Golden Benchmark Dataset (`tests/evals/golden_dataset.json`)

The Evals suite evaluates the entire pipeline against a curated benchmark of 50 historical Univest recommendations across Indian equities and futures spanning 2022–2026:
- **15 Clean Bullish Setups:** High delivery %, positive F&O buildup, clean fundamentals, R:R $\ge 1.8$, resulting in forward targets being achieved.
- **10 Inverted Risk-Reward / Slippage Traps:** Reached user after price had moved $>1.5\%$ above entry; live R:R $< 1.0$.
- **10 Institutional Distribution Traps:** Price rising on falling Futures OI and low Delivery % ($<25\%$), resulting in subsequent sharp sell-offs.
- **5 High Promoter Pledging Traps:** Companies with promoter pledge $> 25\%$ (e.g., historical Zee / Adani stress / Vedanta pledge events).
- **5 Impending Earnings Event Traps:** Recommended 2–4 days before quarterly board meeting; resulted in binary earnings gap-down.
- **5 High Volatility / Tight Stop Traps:** Stop Loss placed $< 0.75 \times \text{Daily ATR}$, resulting in intraday noise stopouts.

---

## 3. Evaluation Dimensions & Pass Criteria

| Dimension | Evaluation Seam | Automated Metric / Assertion | Pass Threshold |
| :--- | :--- | :--- | :--- |
| **1. Zero Hallucination Guarantee** | Agent Master Prompt Output vs. Script Output JSONs | Every number, percentage, and ratio cited in the generated Markdown and HTML reports must have an exact match in the corresponding script output JSON. | **100% Exact Match** (0 hallucinations allowed) |
| **2. Hard Circuit Breaker Enforcement** | Scoring Engine (`scripts/9_decision_engine.py`) | Any trade in the benchmark with Promoter Pledge $>25\%$, Inverted R:R ($<1.0$), Options instrument, or Earnings $<7\text{d}$ on Futures must receive a **🔴 NO-GO**. | **100.0% Detection Rate** (Zero false negatives) |
| **3. Option Rejection Invariance** | Ingestion Parser (`scripts/0_ingest_reco.py`) | Any recommendation containing Call (CE), Put (PE), or Option contracts must be immediately rejected with exit code 2. | **100.0% Rejection Rate** |
| **4. Directional Calibration** | LightGBM & DTW Engine (`3_dtw_pattern.py`, `4_ml_direction.py`) | Calibrated Brier score on 1-month forward direction prediction across the benchmark. | **Brier Score $\le 0.18$** |
| **5. Range Coverage Integrity** | Volatility Cone Engine (`4_ml_direction.py`) | Percentage of actual forward 22-day maximum and minimum price bars that remained within the calculated $[P_{10}, P_{90}]$ price envelope. | **Coverage $\ge 80.0\%$** |
| **6. Optimal Limit Entry Value-Add** | Precision Engine (`8_dhan_precision.py`) | Limit orders placed at Anchored VWAP / Order Block support must show lower realized Max Adverse Excursion (MAE) compared to entering at raw Univest market price. | **MAE Reduction in $\ge 75\%$ of trades** |
| **7. HTML Report Validity** | HTML Visualizer (`12_generate_html_report.py`) | Generated HTML must be self-contained (zero broken images or missing styles), render under 300ms, and contain all required charts and gauges. | **100% Valid HTML5, 0 network dependencies** |

---

## 4. Evals Execution Runner (`tests/run_evals.py`)

The evaluation runner runs headlessly and outputs an evaluation scorecard:

```bash
python tests/run_evals.py --benchmark tests/evals/golden_dataset.json
```

Output scorecard template:
```text
======================================================================
              UVEST AGENT & QUANT EVALUATION SCORECARD
======================================================================
  [PASS] Option Rejection Guardrail:       100.0% (5/5 rejected)
  [PASS] Hard Circuit Breaker Accuracy:   100.0% (25/25 correctly blocked)
  [PASS] Zero Hallucination Audit:        100.0% (0 discrepancies in 50 runs)
  [PASS] Directional Brier Score:          0.142  (Threshold <= 0.180)
  [PASS] Price Cone Range Coverage:        86.4%  (Threshold >= 80.0%)
  [PASS] Optimal Limit Slippage Reduction: +1.42% net expectancy improvement
  [PASS] HTML Visualizer Validation:       50/50 standalone files valid
======================================================================
  FINAL EVALUATION VERDICT: SUITE PASSED (Ready for Live Operation)
======================================================================
```
