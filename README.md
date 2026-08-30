# 🛡️ Antigravity Trade Validation & Daily Screener Pipeline

An end-to-end quantitative framework implementing `BRD.md`: a local, script-driven trade validation and daily screening workflow that turns raw trade recommendations and market data into **Confidence Scores (0–100)**, **statistical edge classifications**, and **actionable trade decisions** — backed by real mathematical rigor, live market data, and an **accumulating historical learning loop**.

---

## 📑 Table of Contents
1. [System Architecture](#-system-architecture)
2. [Quick Start & Setup](#-quick-start--setup)
3. [Script Directory & Technical Deep Dive](#-script-directory--technical-deep-dive)
   - [Trade Validation Suite (BRD Core)](#trade-validation-suite-brd-core)
   - [Daily Screener & Nadh-Style Microstructure Suite](#daily-screener--nadh-style-microstructure-suite)
4. [Mathematical & Statistical Models](#-mathematical--statistical-models)
   - [Wilson 95% Score Confidence Interval & Gating](#1-wilson-95-confidence-interval--statistical-gating)
   - [Volume Profile (POC, VAH, VAL)](#2-volume-profile-microstructure)
   - [Conditional Probability & Lift](#3-conditional-probability--lift-metric)
   - [Delivery Volume & Institutional Flow Proxies](#4-delivery-volume--institutional-activity-proxies)
   - [Market Quality & Conditions Momentum Overlay](#5-market-quality--conditions-momentum-overlay)
   - [Capital Layer & Risk-First Sizing](#6-capital-layer--risk-first-sizing-500000-base)
5. [The Learning Loop & Memory Engine](#-the-learning-loop--memory-engine)
6. [Database Schemas](#-database-schemas)
7. [CLI Reference & Workflows](#-cli-reference--workflows)

---

## 🏗️ System Architecture

The repository operates two complementary, non-intrusive pipelines:

### 1. Workflow A: Trade Validation Pipeline (Single Recommendation)
```
current_trade.json ──► 1_math_and_rr.py ──► script1_out.json
                     ──► 2_market_data.py ──► script2_out.json
                     ──► 3_historical_audit.py ──► script3_out.json
                     ──► 6_backtest_setup.py ──► script6_out.json (similar-setup backtest)
                     ──► screen_day_quality.py ──► screen_day_quality_out.json
                     ──► screen_signals.py ──► screen_signals_out.json
                     ──► screen_entry_readiness.py ──► screen_entry_readiness_out.json
                     ──► screen_scalp_viability.py ──► screen_scalp_out.json
                     ──► screen_gap_analysis.py ──► screen_gap_out.json
                     ──► screen_volume_profile.py ──► screen_vp_out.json
                     ──► screen_mean_reversion.py ──► screen_mr_out.json
                     ──► screen_day_statistics.py ──► screen_day_stats_out.json
                     ──► screen_conditional_probability.py ──► screen_cp_out.json
                     ──► screen_delivery_volume.py ──► screen_dv_out.json
                     ──► 4_score_calculator.py ──► FINAL SCORE + DECISION + Flip Table
                     ──► 5_track_and_resolve.py ──► univest_history.db (Learning Loop)
                     ──► decision_report.md (Markdown output)
                     ──► watch.py (Standing-order live price monitor)
```

### 2. Workflow B: Daily Screener Pipeline (Market-Wide Scalping)
```
Universe (NSE 150)
   │
   ▼
screen_regime.py ────────► NIFTY trend vs 200-DMA, India VIX, Market Breadth
   │
   ▼
screen_stats.py ─────────► Multi-horizon backtest (2D/3D/5D/1W/2W × 1%/2%/3%/4%)
   │
   ▼
screen_day_quality.py ───► 0-100 Day Quality Score + Conditions Momentum (AVOID Gate)
   │
   ▼
screen_regime_classify.py► TRENDING / MEAN_REVERSION / BREAKOUT / CHOPPY
   │
   ▼
screen_rank.py ──────────► Filter & Rank candidates by P(hit), Expectancy, Momentum
   │
   ▼
Per-Stock Deep Dives ────► Signals, Entry Readiness, Scalp Viability, Gap Analysis,
(Top 10 Candidates)        Volume Profile, Mean Reversion, Calendar Stats,
                           Conditional Probability Lift, Delivery Volume
   │
   ▼
screen_picks.json ───────► Selected 1–3 highest conviction trade candidates
   │
   ▼
screen_record.py ────────► screen_history.db (Record top list, resolve past picks,
                           generate screen_learning.md, update stock memory)
```

---

## 🚀 Quick Start & Setup

### Prerequisites
- Python 3.11+
- Virtual environment recommended

### Installation
```bash
# 1. Clone the repository and navigate into it
cd d:/Code/uvest

# 2. Install dependencies
python -m pip install -r requirements.txt

# 3. Bootstrap track-record DB (optional — auto-initialized on first run)
python scripts/seed_history.py --fresh
```

### Quick Runs
```bash
# Single Trade Validation:
python scripts/run_analysis.py

# Daily Market Screen:
python scripts/run_screen.py --top 20
```

---

## 📦 Script Directory & Technical Deep Dive

### Trade Validation Suite (BRD Core)

| Script | Purpose | Output Contract & Core Logic |
| :--- | :--- | :--- |
| **`scripts/1_math_and_rr.py`** | Risk:Reward & Price Geometry | Computes raw R:R (at recommended entry) and live R:R (at current market price). Calculates per-lot INR risk and reward. Flags entry-range slippage and inverted risk-reward ratios ($R:R < 1.0$). Emits `script1_out.json`. |
| **`scripts/2_market_data.py`** | Live Market Context | Fetches live daily bars via Yahoo Finance (`yfinance`). Computes 14-day ATR, 50-day EMA, and price trend direction. Evaluates stoploss distance as an ATR multiple to detect volatility traps (SL $< 1.0\times$ ATR). Emits `script2_out.json`. |
| **`scripts/3_historical_audit.py`** | Historical Track Record Audit | Queries `univest_history.db` for the symbol and instrument type. Calculates sample count ($N$), win rate, average win, average loss, and net expectancy ($\text{INR/trade}$). Prioritizes real self-tracked data, falling back to bootstrap seed rows. Emits `script3_out.json`. |
| **`scripts/4_score_calculator.py`** | Consolidated Decision Engine | Combines checks into a 0–100 Confidence Score and GO / CONDITIONAL GO / NO-GO verdict. Evaluates Day Quality gates, empirical backtest gating, and the waiver model. Generates the **Flip Table** (exact price thresholds required to change decision) and the **Optimal Activation Zone**. Emits `script4_out.json`. |
| **`scripts/5_track_and_resolve.py`** | Learning Loop & Track-Record Resolver | Resolves open recommendations against historical daily bars (WIN/LOSS net of costs). Records the current recommendation (deduplicated by reco identity). Emits decision accuracy, missed profit on NO-GOs, avoided losses, and Wilson score calibration. Updates `univest_history.db`. |
| **`scripts/6_backtest_setup.py`** | Similar-Setup Historical Backtest | Replays the setup geometry (direction, target, and stop as ATR multiples) across 3 years of daily bars. Resolves setups conservatively (same-bar ambiguity = stop-loss). Computes target-first hit rate and win rate with **Wilson 95% Confidence Intervals** vs. break-even win rate $p^*$. Emits `script6_out.json`. |
| **`scripts/watch.py`** | Standing-Order Trigger Monitor | Computes the optimal activation zone from `current_trade.json` and polls live market price (`--loop`) until the price triggers the limit zone. |
| **`scripts/seed_history.py`** | Database Bootstrapper | Seeds `univest_history.db` with realistic synthetic history for initial calibration until real tracked trades accumulate. |
| **`scripts/run_analysis.py`** | End-to-End Trade Orchestrator | Executes scripts 1→6 in sequence, records the trade, resolves open recommendations, and renders `decision_report.md`. |

---

### Daily Screener & Nadh-Style Microstructure Suite

| Script | Purpose | Technical Deep Dive |
| :--- | :--- | :--- |
| **`scripts/screen_universe.py`** | Universe Fetcher | Retrieves the ~150 most liquid NSE equities from official NSE archives or vendored fallback `universe_nse150.csv`. Emits `screen_universe_out.json`. |
| **`scripts/screen_regime.py`** | Macro Regime Filter | Fetches NIFTY 50 and India VIX. Computes NIFTY trend vs 200-DMA, VIX volatility regime (Low/Normal/High), and universe-wide market breadth (% stocks $> 50$ EMA). Classifies macro environment into **RISK-ON**, **CAUTIOUS**, or **RISK-OFF**. |
| **`scripts/screen_stats.py`** | Multi-Horizon Backtester | Scans 2 years of daily bars for all universe stocks. Replays combinations of horizons (2D, 3D, 5D, 1W, 2W) and targets (1%, 2%, 3%, 4%) with $1.5\times$ ATR stops. Computes forward-return distributions (median, IQR, P10, P90) and regime splits (Bull vs Bear tape). Emits `screen_stats_out.json`. |
| **`scripts/screen_day_quality.py`** | Day Quality Gate & Conditions Momentum | Evaluates day quality (0–100) using VIX percentile, FII/DII net flows, market breadth, and calendar seasonality. Incorporates a **Conditions Momentum Overlay** (VIX compression rate, breadth thrust, FII flow acceleration). Gate verdict: **STRONG (≥70)**, **TRADING (50–69)**, **WASH (30–49)**, **AVOID (<30)**. Emits `screen_day_quality_out.json`. |
| **`scripts/screen_regime_classify.py`** | Microstructure Regime Classifier | Diagnoses the trading environment: **TRENDING** (ADX > 20, momentum works), **MEAN_REVERSION** (range-bound, buy support), **BREAKOUT** (Bollinger squeeze release), or **CHOPPY** (erratic, capital preservation required). Provides specific scalping guidance (hold time, stop style, target size). |
| **`scripts/screen_rank.py`** | Watchlist Ranker | Filters and ranks backtested stocks by target hit probability, Wilson CI lower bounds, expectancy, and 20-day Sector Relative Strength (RS20). Emits `screen_rank_out.json`. |
| **`scripts/screen_signals.py`** | Momentum & Technical Signals | Computes 14-day RSI, MACD histogram, Bollinger Band width & squeeze status, volume ratio vs 20-day average, daily VWAP, and floor pivot points (S1, S2, R1, R2). Identifies optimal confluence entry zones. |
| **`scripts/screen_entry_readiness.py`** | Timing & Entry Readiness Gate | Answers *"Should I enter TODAY?"* Scores (0–100) opening position relative to prior range, close strength $((C - L)/(H - L))$, volume confirmation, 3-day momentum acceleration, and multi-timeframe trend alignment. Verdict: **READY (≥70)**, **CONDITIONAL (40–69)**, **NOT_READY (<40)**. |
| **`scripts/screen_scalp_viability.py`** | Scalp Viability Scanner | Evaluates if a stock's daily range and volatility produce reliable 0.5%–1.0% scalps after costs. Computes forward-return probabilities and net hit rates at 1-day (0.5%), 2-day (1.0%), and 3-day (1.5%) horizons. Scores **SCALPABLE (≥65)**, **MARGINAL (40–64)**, or **NOT_SCALPABLE (<40)**. |
| **`scripts/screen_gap_analysis.py`** | Overnight Gap Analyzer | Classifies opening gaps (Up/Down, Small/Medium/Large). Evaluates historical gap continuation vs gap fill rates. Generates signal scores: **STRONG_FOLLOW_THROUGH**, **FOLLOW_THROUGH_LIKELY**, or **REVERSAL_LIKELY**. |
| **`scripts/screen_volume_profile.py`** | Volume Profile & Institutional Levels | Constructs 20-day Volume-at-Price distributions. Identifies the **Point of Control (POC)** (highest volume price level) and **Value Area (VAH/VAL)** (70% volume zone). Classifies profile shapes ($P$-shaped accumulation, $b$-shaped distribution, $D$-shaped balanced). Buying below POC in an uptrend represents prime institutional support. |
| **`scripts/screen_mean_reversion.py`** | Oversold Dip & Mean Reversion Scanner | Identifies oversold bounce setups: 3-day drawdown magnitude, RSI $< 40$, proximity to support (VWAP, S1, EMA20), and reversal candles (Hammer, Bullish Engulfing). Enforces **Trend Safety Net**: dips in uptrends are gifts; dips in downtrends or drawdowns $> 5\%$ are **CRASH traps** (`NOT_A_DIP`). |
| **`scripts/screen_day_statistics.py`** | Calendar Seasonality Statistics | Analyzes 2 years of NIFTY return distributions by day of week, week of month, and calendar month. Quantifies the monthly F&O expiry week drag. Scores day statistical advantage (0–100). |
| **`scripts/screen_conditional_probability.py`** | Environment-Aware Conditional Probability | Calculates forward-return hit rates conditioned on TODAY'S market state (VIX regime, NIFTY 200-DMA trend, breadth). Computes **Lift Metric** $(\text{Lift} > 1.0$ indicates today's tape favors this stock). Identifies edge durability. |
| **`scripts/screen_delivery_volume.py`** | Institutional Activity & Delivery Proxy | Distinguishes institutional accumulation from retail churn using NSE delivery percentages ($>50\%$ delivery on volume surge = accumulation) or Yahoo proxies (OBV, Accumulation/Distribution line, close position $\times$ volume). Scores **INSTITUTIONAL_ACCUMULATION (≥65)** or **RETAIL_CHURN (<45)**. |
| **`scripts/screen_record.py`** | Screener Recorder & Sizing Engine | Manages the daily screener learning loop. Resolves prior picks against live bars, records today's watchlist and picks, calculates risk-first capital sizing on ₹5,00,000, and updates `screen_learning.md`, `screen_recos_ledger.json`, and `screen_stock_memory.json`. |
| **`scripts/run_screen.py`** | Screener Orchestrator | Executes universe -> regime -> stats -> rank -> deep dives -> report rendering in a single command. |

---

## 🧮 Mathematical & Statistical Models

### 1. Wilson 95% Confidence Interval & Statistical Gating
Point estimates of win rate can be noisy on small samples. We compute the Wilson Score Interval with continuity correction for $n$ historical setups and $w$ wins:

$$\hat{p} = \frac{w + \frac{z^2}{2}}{n + z^2} \pm \frac{z}{n + z^2} \sqrt{\frac{w(n - w)}{n} + \frac{z^2}{4}}$$

where $z = 1.96$ for a 95% confidence interval.

**Statistical Break-Even Gating ($p^*$):**
Given a live net Reward-to-Risk ratio $\text{net\_rr}$ (after modeled transaction costs), the break-even win rate is:

$$p^* = \frac{1}{1 + \text{net\_rr}}$$

- **Edge Confirmed (Full Score)**: Wilson Lower Bound $\text{CI}_{\text{lower}} > p^*$
- **Positive but Unconfirmed**: Observed Win Rate $\hat{p} > p^*$ but $\text{CI}_{\text{lower}} \le p^*$
- **No Edge (0 Points)**: Observed Win Rate $\hat{p} \le p^*$

---

### 2. Volume Profile Microstructure
For a 20-day historical window of daily high, low, close, and volume, volume is distributed across price bins:
- **Point of Control (POC)**: Price level bin containing the maximum traded volume:
  $$\text{POC} = \arg\max_{P} V(P)$$
- **Value Area (VA)**: The smallest range of prices around POC containing 70% of total volume:
  $$\sum_{P = \text{VAL}}^{\text{VAH}} V(P) \ge 0.70 \times V_{\text{total}}$$
- **Profile Shapes**:
  - **$P$-Shape**: Volume concentrated at upper price range (short covering / aggressive institutional accumulation).
  - **$b$-Shape**: Volume concentrated at lower price range (long liquidation / institutional distribution).
  - **$D$-Shape**: Symmetrical normal distribution (balanced market).

---

### 3. Conditional Probability & Lift Metric
To prevent trading setups whose edges collapse in adverse market regimes, we condition stock forward returns on macro states $S$ (VIX level, NIFTY trend vs 200-DMA, breadth):

$$\text{Lift} = \frac{P(\text{Hit Target} \mid \text{Market State } S)}{P(\text{Hit Target})}$$

- **$\text{Lift} > 1.0$**: The current market environment enhances the stock's statistical edge.
- **$\text{Lift} < 0.85$**: The stock's historical performance degrades significantly under today's conditions; size is reduced or position is skipped.

---

### 4. Delivery Volume & Institutional Activity Proxies
1. **Actual NSE Delivery Ratio**:
   $$\text{Delivery Ratio} = \frac{\text{Delivery Quantity}}{\text{Traded Quantity}}$$
   - $\text{Ratio} \ge 50\%$ on rising volume: **Institutional Accumulation**.
   - $\text{Ratio} \le 30\%$ on high volume: **Retail Churn / Day Trading Speculation**.

2. **Yahoo Finance Proxies (Fallback)**:
   - **On-Balance Volume (OBV)** 20-day trend.
   - **Accumulation/Distribution (A/D)** divergence.
   - **Close Location Value (CLV)**:
     $$\text{CLV} = \frac{(C - L) - (H - C)}{H - L}$$

---

### 5. Market Quality & Conditions Momentum Overlay
Day Quality combines static indicators (VIX percentile, FII net cash flow, breadth) with a 3-day **Conditions Momentum Overlay**:

$$\Delta_{\text{Momentum}} = \Delta_{\text{VIX Compression}} + \Delta_{\text{Breadth Thrust}} + \Delta_{\text{FII Acceleration}}$$

- **Momentum Collapse ($\le -20$)**: Downgrades a static STRONG day to WASH.
- **Momentum Building ($\ge +15$)**: Upgrades a static TRADING day to STRONG.

---

### 6. Capital Layer & Risk-First Sizing (₹5,00,000 Base)
Picks are mechanically sized to enforce capital discipline:
1. **Risk-First Quantity**:
   $$\text{Qty}_{\text{risk}} = \left\lfloor \frac{0.01 \times \text{Capital}}{\text{Stoploss Distance}} \right\rfloor$$
2. **Floor Quantity (30% Deployment Floor)**:
   $$\text{Qty}_{\text{floor}} = \left\lfloor \frac{0.30 \times \text{Capital}}{\text{Entry Price}} \right\rfloor$$
3. **Execution Quantity**:
   $$\text{Qty} = \max(\text{Qty}_{\text{risk}}, \text{Qty}_{\text{floor}})$$
4. **Portfolio Rules**: Maximum 3 concurrent picks, total deployment $\le 100\%$ of capital, combined stop exposure $\le 4.5\%$ of capital.

---

## 🔁 The Learning Loop & Memory Engine

The learning loop ensures the system continuously audits its own recommendations and calibrates its confidence scoring:

```
                  ┌───────────────────────────────┐
                  │ 1. Record New Recommendations │
                  │    - OPEN rows in SQLite DB   │
                  │    - Score, Decision, Geometry│
                  └──────────────┬────────────────┘
                                 │
                                 ▼
                  ┌───────────────────────────────┐
                  │ 2. Live Bar Resolution        │
                  │    - Target hit first → WIN   │
                  │    - Stop hit first → LOSS    │
                  │    - Expiry close settlement  │
                  └──────────────┬────────────────┘
                                 │
                                 ▼
                  ┌───────────────────────────────┐
                  │ 3. Score Calibration & Audit  │
                  │    - Real vs predicted win rate│
                  │    - Missed vs avoided profits │
                  │    - Update stock memory      │
                  └──────────────┬────────────────┘
                                 │
                                 ▼
                  ┌───────────────────────────────┐
                  │ 4. Feedback to Future Scoring │
                  │    - Script 3 uses real data  │
                  │    - Screen weights adjust    │
                  └───────────────────────────────┘
```

### Transaction Cost Deduction
All P&L is calculated net of modeled friction:
- **Futures (FUT)**: $\approx 0.09\%$ of notional round-trip (brokerage, STT, exchange fees, GST, stamp duty).
- **Options (OPT)**: $\approx 0.25\%$ of premium round-trip.

---

## 🗄️ Database Schemas

### `univest_history.db` (`trade_recommendations` table)
```sql
CREATE TABLE trade_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    instrument_type TEXT NOT NULL,      -- FUT / OPT
    action TEXT,                        -- BUY / SELL
    outcome TEXT NOT NULL,              -- WIN / LOSS / OPEN
    pnl_inr REAL,
    rr_ratio REAL,
    entry_price REAL,
    target REAL,
    stoploss REAL,
    entry_range_min REAL,
    entry_range_max REAL,
    live_price REAL,
    lot_size REAL,
    margin REAL,
    confidence_score INTEGER,
    decision TEXT,
    recommended_on TEXT,
    resolved_on TEXT,
    final_price REAL,
    expiry TEXT,                        -- e.g. "25 AUG"
    expiry_date TEXT,                   -- parsed ISO date
    costs_inr REAL,                     -- modeled transaction costs
    is_seed INTEGER NOT NULL DEFAULT 0, -- 1 = bootstrap seed row
    source TEXT,                        -- univest / other
    analysis_count INTEGER NOT NULL DEFAULT 1,
    last_analyzed_on TEXT,
    UNIQUE (symbol, instrument_type, action, entry_price, target, stoploss)
);
```

### `screen_history.db` (`screen_recos` table)
Tracks all ranked candidates, selected picks, sized capital deployment, predicted return bands (P10–P90), and realized outcomes.

---

## 💻 CLI Reference & Workflows

### 1. Single Trade Validation Commands
```bash
# Automated run (recommended):
python scripts/run_analysis.py [path/to/current_trade.json]

# Step-by-step BRD manual blueprint:
python scripts/screen_day_quality.py > screen_day_quality_out.json
python scripts/1_math_and_rr.py > script1_out.json
python scripts/2_market_data.py --symbol BHARTIARTL --expiry 25AUG > script2_out.json
python scripts/3_historical_audit.py --symbol BHARTIARTL --type FUT > script3_out.json
python scripts/6_backtest_setup.py > script6_out.json
python scripts/screen_signals.py --symbol BHARTIARTL > screen_signals_out.json
python scripts/4_score_calculator.py
python scripts/5_track_and_resolve.py --score 35 --decision "NO-GO (REJECT)"
```

### 2. Daily Screener Commands
```bash
# Automated daily screen:
python scripts/run_screen.py --top 20

# Record, size on ₹5L, resolve past picks, and update learning journal:
python scripts/screen_record.py --record

# Standing-order live price monitor:
python scripts/watch.py --loop
```
