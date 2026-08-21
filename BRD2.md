# Business Requirements Document (BRD)

**System Title:** Short-Term Directional Prediction Engine for NSE Stock & Index Futures  
**Target Exchange:** National Stock Exchange of India (NSE)  
**Core Technologies:** LightGBM, Triple Barrier Method, Isotonic Calibration, Purged Walk-Forward CV  
**Trading Application:** NSE F&O (Futures & Options) Directional Swing & Scalp Trading  
**Version:** 2.2 (Phased Implementation — Nadh Methodology)
**Design Philosophy:** Kailash Nadh / Zerodha Varsity — ship simple, measure honestly, never auto-optimize  
**Target Horizon:** 1 to 2 Weeks (5–10 NSE Trading Days) with Daily Recalibration  

---

## 1. Executive Summary & Objectives

### 1.1 Purpose
This document specifies the complete technical, data, and algorithmic requirements for an automated machine learning prediction engine built specifically for **NSE-listed equities and indices** (e.g., Nifty 50, Bank Nifty, Reliance, Infosys, ICICI Bank futures). 

The system uses **daily charts exclusively** (derived from post-market NSE Bhavcopy / daily session feeds) to forecast directional moves over a 5–10 trading day horizon. It identifies high-probability trading setups to trade NSE stock futures, incorporating NSE-specific market dynamics such as institutional flows (FII/DII), India VIX, Market Wide Position Limits (MWPL), and monthly F&O expiry cycles.

### 1.2 Core Objectives
* **Target Universe:** High-liquidity F&O stocks on the NSE (Nifty 50 constituents and top liquid stock futures).
* **Directional Focus:** Binary classification (Positive expected return vs. Negative/Neutral) over a 5–10 NSE trading day window.
* **Daily Recalibration:** Re-evaluate and re-score open/prospective positions every trading day post 4:00 PM IST (following NSE EOD data release).
* **High Confidence Filtering:** Execute trades **only** when the calibrated probability of success meets a strict confidence threshold ($P(\text{Success}) \ge 0.70$).
* **Data Constraint:** Exclusively operate on **Daily Candles (OHLCV)**. Intraday (minute/hourly) data is strictly out of scope.
* **Self-Learning:** Every recommendation logged with full feature snapshot. On failure, structured autopsy generated. Human reviews and proposes guardrails. Guardrails tested in walk-forward before activation. **No auto-adding of rules.**
* **1-2 trades/day maximum** — selectivity is the edge, not frequency.

### 1.3 What This System Is NOT
* It is NOT a high-frequency trading system.
* It is NOT a fully automated execution system (human approves each trade).
* It does NOT use paid data sources or APIs.
* It does NOT auto-tune guardrails (human-in-the-loop only).

---

## 1A. Implementation Phases (Nadh Methodology)

The full spec is ambitious. Ship in three phases. Each phase is independently runnable and produces honest, measurable results before the next phase begins.

### STAGE 1 — Core Pipeline (Implement Now)
The minimal system that proves directional edge exists:
* **Data:** yfinance daily OHLCV for liquid NSE stocks (brd2/data/liquid_nse100.csv)
* **Features:** 12 core technical features — Log Returns (1D/5D/10D), ATR, Parkinson Vol, RSI, MACD, Bollinger %B, Volume Z-Score, Relative Strength vs Nifty, EMA trend
* **Labels:** Triple Barrier Method (k_u=1.5, k_l=1.0, H=10 days) with ATR-quality flag
* **Validation:** Purged Walk-Forward CV (purge=9, embargo=5, 5-fold expanding)
* **Model:** LightGBM classifier (constrained hyperparams) + Isotonic Calibration
* **Output:** Walk-forward backtest report per stock: win rate, Sharpe, max drawdown, feature importances
* **Scope:** RELIANCE + HDFCBANK minimum (2 stocks)
* **Deferred features:** FracDiff, FII/DII flows, India VIX, Sector indices, MWPL — these are data-acquisition-heavy and not load-bearing for edge detection
* **Deferred operations:** Broker API, real-time MWPL monitoring, Fractional Kelly sizing

### STAGE 2 — Scale & Enrich (After Stage 1 proves edge)
* **Universe expansion:** Scale pipeline to 50+ stocks from liquid_nse100.csv
* **Additional features:** Fractional Differentiation, FII/DII institutional flows, India VIX 5D change, Sector relative strength
* **Self-learning v0.1:** Minimal autopsy (p_calibrated, final_price, barrier_sequence, human_note) on every resolved recommendation
* **Daily inference:** Automated post-market scoring of universe, ranked watchlist output
* **Position sizing:** Fixed fractional (1% risk per trade) with portfolio-level deployment caps
* **Regime conditioning:** Split model performance by NIFTY above/below 200-DMA

### STAGE 3 — Operations & Learning (After Stage 2 validates at scale)
* **MWPL monitoring:** Real-time F&O ban tracking, dynamic exclusion filter
* **Expiry management:** Automated position squaring before physical delivery risk
* **Fractional Kelly sizing:** Edge-magnitude-aware position sizing from accumulated track record
* **Guardrail registry:** JSON-based guardrails, human approval workflow, walk-forward testing before activation
* **Full autopsy:** Feature-delta analysis, regime-shift detection, AI-proposed guardrails
* **Broker integration:** AMO / Market-on-Open order staging via broker API
* **Sector index features:** Full sector momentum and cross-sector correlation matrix

---

## 2. NSE-Specific System Architecture Overview

```
                        [ NSE MARKET CLOSE - 3:30 PM IST ]
                                       │
                                       ▼
             [ NSE Daily Bhavcopy + India VIX + FII/DII Data (4:00 PM IST) ]
                                       │
                                       ▼
                       ┌───────────────────────────────┐
                       │  Feature Engineering Pipeline │
                       │  (FracDiff, NSE Sector Ratios,│
                       │   FII/DII Flows, Technicals)  │
                       └───────────────┬───────────────┘
                                       │
                                       ▼
                       ┌───────────────────────────────┐
                       │    LightGBM Classifier        │
                       └───────────────┬───────────────┘
                                       │
                                       ▼
                       ┌───────────────────────────────┐
                       │    Isotonic Calibration       │
                       │     (Probability Map)         │
                       └───────────────┬───────────────┘
                                       │
                                       ▼
                       ┌───────────────────────────────┐
                       │ NSE Filters & Decision Engine │
                       │ (MWPL Ban & Expiry Checks)    │
                       └───────────────┬───────────────┘
                                       │
                                       ▼
             [ Orders Staged / Executed at Next Day NSE Open (9:15 AM IST) ]
```

---

## 3. Data & Feature Engineering Specifications (NSE Context)

All inputs are derived strictly from daily End-of-Day (EOD) data published by the National Stock Exchange of India and SEBI reports.

### 3.1 Data Inputs
1. `[STAGE 1]` **NSE Stock/Index Daily OHLCV:** yfinance daily bars (Open, High, Low, Close, Volume) — cached locally.
2. `[STAGE 2]` **Benchmark Index Daily OHLCV:** Nifty 50 Index (`^NSEI`) and Nifty Bank Index (`^NSEBANK`).
3. `[STAGE 3]` **Sector Indices:** Nifty IT, Nifty Financial Services, Nifty Auto, Nifty FMCG, Nifty Metal, Nifty Pharma.
4. `[STAGE 2]` **Volatility Indicator:** India VIX (`^INDIAVIX`) daily close and percentage change.
5. `[STAGE 2]` **Institutional Activity Data:** Daily net FII and DII cash market investment from NSE.
6. `[STAGE 3]` **Market Wide Position Limit (MWPL):** Daily MWPL utilization percentage.

### 3.2 Complete Feature Inventory (Daily Basis)

| Feature Category | Feature Name | Stage | Description / Formula | Stationarity Method / Range |
| :--- | :--- | :--- | :--- | :--- |
| **Price / Memory** | `FracDiff_Close` | STAGE 2 | Fractional Differentiation ($d \approx 0.35 - 0.45$) on Close | Stationary (Preserving Memory) |
| **Return Momentum** | `Log_Ret_1D`, `5D`, `10D` | STAGE 1 | Logarithmic returns over 1, 5, and 10 NSE trading days | Log Difference |
| **Delivery Strength** | `Delivery_Pct_ZScore` | STAGE 3 | Z-score of $(\text{Delivery Volume} / \text{Total Traded Volume})$ over 20 days | Standardized Z-Score |
| **Vol-Normalized Range**| `Normalized_ATR_14` | STAGE 1 | $\text{ATR}(14) / \text{Close}_t$ | Ratio Scaling |
| **Normalized Volatility**| `Parkinson_Vol_10` | STAGE 1 | Daily High-Low Parkinson Volatility estimator | Bounded Ratio |
| **Institutional Flow** | `FII_Net_Flow_5D` | STAGE 2 | 5-day rolling sum of net FII cash market investment ($\text{₹ Crores}$) | Z-Score Standardized |
| **Institutional Flow** | `DII_Net_Flow_5D` | STAGE 2 | 5-day rolling sum of net DII cash market investment ($\text{₹ Crores}$) | Z-Score Standardized |
| **Relative Strength** | `Rel_Strength_NIFTY` | STAGE 1 | Stock 10-day log return minus Nifty 50 10-day log return | Differential Return |
| **Sector Momentum** | `Rel_Strength_Sector` | STAGE 3 | Stock 10-day log return minus its corresponding Nifty Sector Index return | Differential Return |
| **Macro Volatility** | `India_VIX_5D_Change` | STAGE 2 | 5-day absolute difference in India VIX | Difference |
| **Oscillator** | `Normalized_RSI_14` | STAGE 1 | $(\text{RSI}(14) - 50) / 100$ | Range-bound $[-0.5, 0.5]$ |
| **Trend Convergence** | `MACD_Hist_Ratio` | STAGE 1 | $(\text{MACD Line} - \text{Signal Line}) / \text{Close}_t$ | Normalized by Price |
| **Band Squeeze** | `Bollinger_PctB` | STAGE 1 | $(\text{Close} - \text{LowerBand}) / (\text{UpperBand} - \text{LowerBand})$ | Normalized Position $[0, 1]$ |
| **Volume Z-Score** | `Volume_ZScore_20` | STAGE 1 | $(\text{Volume}_t - \mu_{\text{Vol}, 20}) / \sigma_{\text{Vol}, 20}$ | Z-Score Standardized |
| **F&O Ban Risk Proxy** | `MWPL_Pct` | STAGE 3 | Current Market-Wide Position Limit utilization percentage | Bounded $[0, 100\%]$ |

---

## 4. Labeling Engine: Triple Barrier Method (NSE Stock Adaptation)

The **Triple Barrier Method** adapts to the higher volatility of Indian equities by scaling barriers dynamically using daily normalized ATR.

```
 Price (₹)
   ▲
   │        ============================== Upper Barrier (Take Profit: +1.5 * ATR)
   │               /   \
   │   /\         /     \   <- Hit Upper Barrier First -> Label = 1
   │  /  \  /\   /       \
---┼─/────\/──\-/\────────---------------------------------- Initial Price (t_0)
   │ /         \
   │/           ========================== Lower Barrier (Stop Loss: -1.0 * ATR)
   │
   └──────────────────────────────────────► Time (NSE Trading Days)
     t_0                                  t_0 + 10 Days (Vertical Barrier)
```

### 4.1 Barrier Formulation
At market close of NSE trading day $t_0$:
* **Dynamic Volatility Proxy ($\sigma_{t_0}$):** 14-day Daily ATR normalized by Close price ($\text{ATR}_{14} / \text{Close}_{t_0}$).
* **Upper Barrier (Take Profit):** $U_{t_0} = \text{Close}_{t_0} \times (1 + k_u \cdot \sigma_{t_0})$, where $k_u = 1.5$.
* **Lower Barrier (Stop Loss):** $L_{t_0} = \text{Close}_{t_0} \times (1 - k_l \cdot \sigma_{t_0})$, where $k_l = 1.0$.
* **Vertical Barrier (Expiration):** $V_{t_0} = t_0 + 10 \text{ NSE trading days}$ (excluding NSE trading holidays).

### 4.2 Labeling Logic (Daily Bar Resolution)
Evaluating forward daily bars $t \in [t_0 + 1, t_0 + 10]$ using daily High, Low, and Close:
1. **Upper Barrier Hit:** If $\text{High}_t \ge U_{t_0}$ *before* $\text{Low}_t \le L_{t_0}$ and before day $t_0 + 10$, **Label = 1**.
2. **Lower Barrier Hit:** If $\text{Low}_t \le L_{t_0}$ *before* $\text{High}_t \ge U_{t_0}$ and before day $t_0 + 10$, **Label = 0**.
3. **Vertical Barrier Reached:** If neither barrier is triggered within 10 trading days:
   * **Label = 1** if $\text{Close}_{t_0 + 10} > \text{Close}_{t_0}$ (Positive return at horizon).
   * **Label = 0** if $\text{Close}_{t_0 + 10} \le \text{Close}_{t_0}$ (Negative/Flat return at horizon).

---

## 5. Validation Protocol & Purged Cross-Validation

### 5.1 Purged Walk-Forward Cross-Validation (CPCV)
To handle time-series overlap and serial correlation in daily Indian stock data, the model must enforce strict **Purging** and **Embargoing**.

```
[ Train Set ] ------ [ Purge (10 days) ] ------ [ Test Set (Fold) ] ------ [ Embargo (5 days) ] ------ [ Train Set ]
```

* **Label Window ($H$):** 10 NSE trading days.
* **Purge Window:** 9 trading days immediately preceding the test fold boundary removed from training set to prevent lookahead leakage.
* **Embargo Window:** 5 trading days immediately following the test fold boundary removed to eliminate autocorrelation bias.
* **Cross-Validation Setup:** 5-Fold Expanding Walk-Forward CV.

### 5.2 Daily Recalibration Protocol
* **Daily Inference:** Runs post-market close at 4:30 PM IST every day using the latest NSE Bhavcopy data.
* **Weekly Retraining:** LightGBM model weights are retrained every weekend using an expanding historical dataset to adapt to evolving Indian market regimes (e.g., earnings seasons, RBI policy shifts).

---

## 6. Model Training & Isotonic Probability Calibration

### 6.1 LightGBM Hyperparameters for Indian Equities

```python
lightgbm_params_nse = {
    'objective': 'binary',
    'metric': 'binary_logloss',
    'boosting_type': 'gbdt',
    'n_estimators': 350,
    'learning_rate': 0.012,
    'num_leaves': 16,          # Constrained for noisy tabular equity data
    'max_depth': 4,            # Prevents overfitting on individual stock anomalies
    'min_child_samples': 25,
    'subsample': 0.75,         # Row subsampling
    'colsample_bytree': 0.70,  # Feature subsampling
    'reg_alpha': 0.15,         # L1 Regularization
    'reg_lambda': 1.2,         # L2 Regularization
    'random_state': 42
}
```

### 6.2 Isotonic Probability Calibration Engine
Raw GBDT probability outputs do not reflect true win rates. An out-of-sample **Isotonic Calibration** layer maps raw model scores into empirical win probabilities.

#### Calibration Process:
1. Collect raw predicted probabilities $P_{\text{raw}}$ from out-of-sample Purged Cross-Validation folds.
2. Train `IsotonicRegression(out_of_bounds='clip')` mapping $P_{\text{raw}} \to \text{Actual Label (0 or 1)}$.
3. Store calibration curve $f_{\text{iso}}$ to transform raw daily inference outputs:
   $$P_{\text{calibrated}} = f_{\text{iso}}(P_{\text{raw}})$$

```
 Raw LightGBM Output (P_raw) ───► [ Isotonic Calibration ] ───► True Win Probability (P_calib)
```

---

## 7. NSE Futures Execution & Decision Logic

This module processes daily EOD signals, applies strict NSE operational constraints, and formats execution orders for the next day's market open (9:15 AM IST).

```
                       ┌──────────────────────────────┐
                       │ NSE Daily Bhavcopy Vectors   │
                       └──────────────┬───────────────┘
                                      │
                                      ▼
                       ┌──────────────────────────────┐
                       │ Raw LightGBM Prediction      │
                       │          P_raw               │
                       └──────────────┬───────────────┘
                                      │
                                      ▼
                       ┌──────────────────────────────┐
                       │ Isotonic Calibration Mapping │
                       │    P_calib = f_iso(P_raw)    │
                       └──────────────┬───────────────┘
                                      │
                                      ▼
                       /──────────────────────────────\
                      /   Is P_calib >= 0.70 AND       \
                     /  Stock NOT in F&O Ban (MWPL)?    \
                     \                                  /
                      \────────────────────────────────/
                               /              \
                        YES   /                \   NO
                             ▼                  ▼
             ┌───────────────────────┐  ┌───────────────────────┐
             │ BUY NSE STOCK FUTURES │  │  NO TRADE / HOLD CASH │
             │ (Set Bracket Targets) │  └───────────────────────┘
             └───────────────────────┘
```

### 7.1 Signal Matrix & Trade Management `[STAGE 1: P_calib threshold only]` `[STAGE 3: full matrix + MWPL + expiry]`

| Current Position | Calibrated Probability ($P_{\text{calib}}$) | MWPL / Expiry Condition | Action | Order Execution |
| :--- | :--- | :--- | :--- | :--- |
| **Flat** | $P_{\text{calib}} \ge 0.70$ | MWPL $< 85\%$ & Days to Expiry $> 4$ | **OPEN LONG FUTURES** | Limit/Market Order at 9:15 AM IST Open |
| **Flat** | $P_{\text{calib}} < 0.70$ | Any | **NO TRADE** | None |
| **In Long Position** | $P_{\text{calib}} \ge 0.55$ | Days to Expiry $> 4$ | **HOLD POSITION** | Keep existing stop/target |
| **In Long Position** | $P_{\text{calib}} < 0.55$ | Any | **EARLY EXIT** | Exit at 9:15 AM IST Open |
| **In Long Position** | Any | Days to Expiry $\le 3$ | **MANDATORY EXIT** | Square off to avoid physical delivery risk |
| **In Long Position** | Any | Stock enters F&O Ban ($\text{MWPL} \ge 95\%$) | **HOLD / REDUCE** | No new buying allowed by NSE |

### 7.2 NSE Special Execution Rules `[STAGE 3]`
1. **Physical Delivery Prevention (Expiry Rule):** All NSE stock futures are physically settled upon expiry. The system MUST close all active stock futures positions no later than **Monday of the expiry week** (Monthly expiry is the last Thursday of the month).
2. **F&O Ban Filter:** If a stock's MWPL exceeds 85%, block new entry signals. If MWPL exceeds 95% (F&O Ban), no additional buying is permitted by exchange rules.
3. **Lot Sizing & Margin Management:** `[STAGE 2: fixed fractional]` `[STAGE 3: Fractional Kelly]`
   * Futures orders are placed in standard **NSE Lot Sizes** (e.g., Reliance lot size = 250, Nifty lot size = 25/75 depending on contract specifications).
   * Position size is determined via **Fractional Kelly Criterion** scaled by required SEBI SPAN + Exposure margins.

---

## 8. Regulatory, Tax & Operational Requirements

### 8.1 Transaction Cost Modeling (India/NSE)
Backtesting and confidence thresholds must explicitly incorporate Indian statutory friction costs:
* **Securities Transaction Tax (STT):** $0.02\%$ on sell side for Futures.
* **NSE Exchange Transaction Charges:** $\approx 0.002\%$
* **SEBI Turnover Fees:** $\approx 0.0001\%$
* **GST:** $18\%$ applied on brokerage + exchange charges.
* **Stamp Duty:** $0.002\%$ on buy side.
* **Integrated Slip Page Assumption:** Minimum 0.05% slippage modeled per trade execution.

### 8.2 Operational Schedule (IST Timeline)

```
 [ 3:30 PM IST ] Market Close
       │
 [ 4:00 PM IST ] Download Official NSE Bhavcopy, India VIX, FII/DII Reports
       │
 [ 4:30 PM IST ] Run Feature Engineering, Model Inference & Isotonic Calibration
       │
 [ 5:00 PM IST ] Generate High-Confidence Signals (P_calib >= 0.70) & Format Broker Orders
       │
 [ 9:15 AM IST ] Execute Orders at NSE Open via Broker API (AMO or Market-on-Open)
```

---

## 9. The Self-Learning Loop (Human-in-the-Loop Guardrails) `[STAGE 2: v0.1 minimal]` `[STAGE 3: full]`

This is the core innovation. Structured accounting + human review + tested guardrails. **Not auto-optimization.**

### 9.1 Recommendation Snapshot `[STAGE 2]`
Every recommendation captures full context: features, model output (P_raw, P_cal, top-5 features), backtest stats, regime state, and guardrails applied.

### 9.2 Failure Autopsy (Only on LOSS) `[STAGE 2: 3-field minimal]` `[STAGE 3: full feature-delta]`
Compares what we expected vs what actually happened: feature deltas, regime shifts, what the model missed.

### 9.3 Human + AI Guardrail Generation `[STAGE 3]`
Autopsy JSON fed to AI tool -> proposes guardrails -> human approves/rejects -> tested in walk-forward -> activated.

### 9.4 Guardrail Registry (guardrails.json) `[STAGE 3]`
Guardrails live in JSON, not code. Auditable, reversible.

### 9.5 Anti-Patterns
1. Never auto-add guardrails
2. Never optimize thresholds on training data
3. Never trust a guardrail that only helps 1-2 folds
4. Never add more than 1-2 guardrails per week
5. Never ignore a guardrail that hurts performance

---

## 10. Walk-Forward Test with Guardrail Feedback

Each fold produces: model performance, guardrail effectiveness, failure autopsies for losses, and guardrail impact scores.

---

## 11. Database Schema

See `brd2/recs.db` for recommendations, resolutions, autopsies, guardrails, and walk-forward fold results.

---

## 12. Verification & Acceptance Criteria

| Metric | Target / Acceptance Criteria | Validation Method |
| :--- | :--- | :--- |
| **Data Integrity** | Zero intraday data dependency; strictly daily Bhavcopy data | Data Audit & Pipeline Code Review |
| **CV Purging Integrity** | Zero target overlap between train and test sets | CPCV Verification Harness |
| **Isotonic Accuracy** | Brier Score reduction of $\ge 15\%$ over raw LightGBM output | Out-of-Sample Calibration Audit |
| **Directional Win Rate** | High-Confidence signals ($P_{\text{calib}} \ge 0.70$) achieve $\ge 62\%$ win rate | 5-Year Walk-Forward Backtest on Nifty 50 stocks |
| **Net Profitability** | Positive Profit Factor ($\ge 1.6$) after deducting STT, GST, brokerage, and slippage | Post-Friction Performance Simulation |
| **Max Drawdown Control**| Portfolio Max Drawdown $\le 12\%$ using fractional Kelly position sizing | Historical Stress Test & Monte Carlo Runs |
| **Self-Learning** | Every LOSS generates structured autopsy; guardrails tested before activation | Walk-Forward Fold Reports |
| **Guardrail Impact** | Each active guardrail improves win rate by >= 1% on held-out folds | Guardrail Impact Tracking |
| **Explainability** | Every recommendation includes top-5 feature importances | Snapshot Audit |