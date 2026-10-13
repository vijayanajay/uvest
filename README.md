# 🛡️ UVEST: Trade Validation & Daily Stewardship Engine

Welcome to **UVEST**, your institutional-grade, agentic trade validation and stewardship engine.

Retail trading recommendations often suffer from poor entry points, hidden corporate governance red flags, or market timing issues. UVEST acts as your **edge magnifier** and **capital protector**. It evaluates trade recommendations against strict quantitative, fundamental, and market flow metrics before you risk any capital.

---

## 🎯 How It Works: The 3 Core Predictions

Whenever you provide a trade recommendation, UVEST computes three actionable predictions:

1. **Direction (1-Month Swing Trajectory):** Predicts the probability of the stock moving up, down, or sideways using historical pattern matching (DTW) and momentum metrics.
2. **Expected Range (1-Month Volatility Cone):** Projects the expected high, low, and median price over the next 22 trading days to ensure your stop-loss is safely placed outside normal market noise.
3. **Optimal Price Range (Precision Execution):** Identifies the best exact limit price to enter the trade based on volume profiles, ensuring you get the best Risk-to-Reward (R:R) ratio instead of chasing market prices.

---

## 🚀 How to Use UVEST

UVEST is designed to run seamlessly inside your **Agentic IDE** (e.g., Cursor, Windsurf, VS Code Agent).

### Step 1: Input the Recommendation
Simply copy and paste the raw text or upload a screenshot of your trade recommendation into your IDE's agent chat.
Provide it alongside the `prompt.md` instructions if starting a new session.

### Step 2: Automated Analysis
The Agent will automatically parse the input, execute a suite of local Python scripts, query free public data sources (and Dhan API for precision), and synthesize a final decision score.

### Step 3: Understanding the Verdict
UVEST will issue one of four distinct, pre-entry verdicts:

*   🟢 **GO:** The trade has a positive statistical expectancy. Execute full size using the recommended limit order.
*   🟡 **GO_WEAK:** The trade is viable but has minor warnings (e.g., pending earnings or missing data). Cap your position size to 50%.
*   ⏳ **WAIT:** The setup is good, but the current price has slipped. Do not enter now; wait for the price to return to the optimal limit zone.
*   🔴 **AVOID:** The trade triggered a hard circuit breaker (e.g., severe promoter pledging $>25\%$, F&O ban, or options contract). **Do not take this trade.**

> **The Flip Condition:** For any `WAIT` or `AVOID` verdict, UVEST will explicitly tell you *what needs to happen* (e.g., "Wait for a dip to ₹X") for the trade to become a `GO`.

---

## 🛡️ Daily Stewardship: Post-Entry Management

Once you enter a trade, UVEST doesn't stop. It provides daily actionable advice for your active positions:

*   🔵 **HOLD:** The trade is behaving normally within expected volatility. Do nothing.
*   🟠 **PRESSURE:** The trade is degrading or facing abnormal adverse movement. UVEST will recommend a **Soft Exit** or to tighten your stop-loss.
*   ⛔ **EXIT:** The original thesis is broken, or a hard stop is hit. Liquidate immediately.

### ⚠️ The Golden Rule of Capital Preservation
**NEVER AVERAGE DOWN A LOSING POSITION.** If a trade goes against you, UVEST will never recommend buying more. Follow the recommended stops and protect your capital.

---

## 📊 Reading Your Reports (User Verification)

For every recommendation analyzed, UVEST provides two forms of output for your verification:

1.  **Terminal Markdown Report:** Delivered directly in your IDE chat. It includes a quick summary of the score (0-100), the verdict, and actionable execution steps (Exact Limit Price, SL, Target, Position Sizing).
2.  **Interactive HTML Visualizer:** A rich, zero-dependency visual dashboard is generated locally at `reports/report_{SYMBOL}_{DATE}.html`. Open this file in any web browser to view speedometer gauges, price cones, and fundamental health barometers in a sleek, simple interface.
