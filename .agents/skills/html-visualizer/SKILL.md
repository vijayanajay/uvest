---
name: html-visualizer
description: "Generates a standalone, zero-dependency, rich interactive HTML visualization report card (reports/report_{SYMBOL}_{DATE}.html) for a trade recommendation. Trigger whenever the user asks for visualization, an HTML report, visual charts, or after completing trade validation."
---

# HTML Visualizer Engine

Generate a standalone, zero-dependency, dark-mode interactive HTML report card for each analyzed recommendation.

## Input & Output Contracts
- **Inputs:** `current_trade.json` and tool output JSONs (`out_1_math.json` through `out_9_decision.json`).
- **Output:** `reports/report_{SYMBOL}_{DATE}.html`

## Core Visual Components
1. **Decision Header & Badge:**
   - Canonical 7-state verdict badge (pre-entry 🟢 GO / 🟡 GO_WEAK / ⏳ WAIT / 🔴 AVOID; post-entry 🔵 HOLD / 🟠 PRESSURE / ⛔ EXIT) with final score (0–100), calibrated P(Win), and expectancy.
   - Ticker, instrument type (EQUITY / FUTURES), lot size, and margin.
2. **The Three Core Predictions:**
   - **Direction Speedometer:** Visual probability gauge for $P(\text{Bullish})$, $P(\text{Neutral})$, $P(\text{Bearish})$.
   - **22-Day Volatility Price Cone Chart:** SVG/Canvas fan chart showing $[P_{10}, P_{25}, P_{50}, P_{75}, P_{90}]$ vs Target and Stop Loss, with MAE/MFE markers.
   - **Optimal Execution Zone Bar:** Visual range bar comparing slipped market price against institutional limit entry zone (Anchored VWAP + order block support).
3. **Institutional Microstructure Matrix:**
   - Delivery % gauge (+σ deviation), Futures OI buildup bar, and Spot-Futures basis carry.
4. **Fundamental Health Radar:**
   - Piotroski F-score (0–9), Altman Z''-score, Promoter Pledging gauge (kill switch $>25\%$), and earnings calendar countdown.
5. **Sector & Macro Barometer:**
   - Mansfield RS vs NIFTY 50, India VIX volatility needle, and FII/DII net flows.
6. **Decision Flip Table & Actionable Execution Ticket:**
   - Exact limit price that turns a 🔴 AVOID or ⏳ WAIT into a 🟢 GO, plus order placement parameters.

## Execution
```bash
python scripts/12_generate_html_report.py   # writes reports/report_{SYMBOL}_{DATE}.html
```
