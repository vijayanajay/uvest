# TICK-010: Standalone Interactive HTML Visualizer

**Status:** FROZEN  
**Priority:** P1  
**Test Seam:** `tests/test_12_generate_html_report.py`  
**Target Script:** `scripts/12_generate_html_report.py`  
**Output Artifact:** `reports/report_{SYMBOL}_{DATE}.html`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 12)  

### 1. Goal & Context
Generate a self-contained, standalone interactive HTML dashboard (`reports/report_{SYMBOL}_{DATE}.html`) for every analyzed recommendation. Can be opened directly in any browser (`file:///...`) without running a web server.

### 2. Frozen Specification Reference
- `BRD.md Section 13`
- Prototype: `reports/sample_report.html`
- Inputs: `current_trade.json`, `out_1_math.json` through `out_9_decision.json`
- Output: `reports/report_{SYMBOL}_{DATE}.html`

### 3. TDD Acceptance Criteria
- [ ] Generates valid HTML5 with 0 external resource errors (self-contained CSS and SVG charts).
- [ ] Displays the verdict badge in the canonical 7-state vocabulary (pre-entry 🟢 GO / 🟡 GO_WEAK / ⏳ WAIT / 🔴 AVOID; post-entry 🔵 HOLD / 🟠 PRESSURE / ⛔ EXIT — BRD §10.4) alongside the composite score, the calibrated $P(\text{win})$, and expectancy.
- [ ] Visualizes the 3 Core Predictions: Direction gauge, 22-day Volatility Price Cone chart, and Optimal Limit Entry vs Univest Entry Range bar.
- [ ] Displays Institutional Flow Matrix (Bhavcopy Delivery % + Futures OI buildup).
- [ ] Displays Fundamental Health card (Piotroski, Altman Z, 0% Pledge check).
- [ ] Renders Decision Flip Table and Actionable Order Execution Ticket.
