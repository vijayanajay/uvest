---
name: evals
description: "Runs the behavioral evaluations benchmark suite against 52 historical trades to verify zero hallucination, 100% hard circuit breaker enforcement, and directional calibration. Trigger whenever testing system accuracy, benchmarking, or running evals."
---

# Behavioral Evaluations (Evals) Suite

Execute behavioral evaluations against the 52-trade golden benchmark dataset (`tests/evals/golden_dataset.json`) to audit agent obedience, calculation precision, and circuit breaker enforcement.

## Evaluation Dimensions & Gates
1. **Zero Hallucination Audit:** 100% of numbers reported in Markdown/HTML must match local script output JSONs verbatim.
2. **Hard Circuit Breakers:** 100% detection and blocking of Promoter Pledges $>25\%$, Inverted R:R ($<1.0$), Options trades, and Earnings $<7\text{d}$ on Futures.
3. **Directional Calibration:** LightGBM + DTW predictions must maintain Brier Score $\le 0.18$.
4. **Range Coverage:** Volatility cones must contain actual forward prices within $[P_{10}, P_{90}]$ in $\ge 80\%$ of trades.
5. **Optimal Entry Value-Add:** Anchored VWAP limit orders must show realized MAE reduction in $\ge 75\%$ of trades.

## Execution
```bash
python tests/run_evals.py --benchmark tests/evals/golden_dataset.json
```
