# TICK-010: Standalone Interactive HTML Visualizer

**Status:** FROZEN  
**Priority:** P1  
**Test Seam:** `tests/test_12_html.py`  
**Target Script:** `scripts/12_generate_html_report.py`  

### 1. Goal & Context
Generate a self-contained, standalone interactive HTML dashboard (`reports/report_{SYMBOL}_{DATE}.html`) for every analyzed recommendation. Can be opened directly in any browser (`file:///...`) without running a web server.

### 2. Frozen Specification Reference
- `BRD.md Section 13`
- Prototype: `reports/sample_report.html`
- Inputs: `current_trade.json`, `out_1_math.json` through `out_9_decision.json`
- Output: `reports/report_{SYMBOL}_{DATE}.html`

### 3. TDD Acceptance Criteria
- [ ] Generates valid HTML5 with 0 external resource errors (self-contained CSS and SVG charts).
- [ ] Displays Decision Badge (🟢 GO / 🟡 CONDITIONAL / 🔴 NO-GO) and Score (0–100).
- [ ] Visualizes the 3 Core Predictions: Direction gauge, 22-day Volatility Price Cone chart, and Optimal Limit Entry vs Univest Entry Range bar.
- [ ] Displays Institutional Flow Matrix (Bhavcopy Delivery % + Futures OI buildup).
- [ ] Displays Fundamental Health card (Piotroski, Altman Z, 0% Pledge check).
- [ ] Renders Decision Flip Table and Actionable Order Execution Ticket.
