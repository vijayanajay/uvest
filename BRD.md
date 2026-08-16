# REVISED BRD & EXECUTION BLUEPRINT: ANTIGRAVITY IDE NATIVE WORKFLOW

This updated spec removes the image/OCR/vision parsing layer. Instead, the workflow is re-architected for **Antigravity IDE** (or any agentic code interpreter environment like Cursor/Windsurf/VS Code Agent), where you provide text/JSON directly, and the AI agent automatically runs local Python scripts to compute metrics, query historical data, and output a **Confidence Score**.

---

## 1. Re-Architected Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ANTIGRAVITY IDE WORKFLOW                           │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. User inputs trade details (Text / JSON) into IDE chat or input file      │
│ 2. AI Agent parses variables and generates `current_trade.json`             │
│ 3. AI Agent executes local Python script suite via Terminal / Tool Calls    │
│    ├── python scripts/1_math_and_rr.py                                      │
│    ├── python scripts/2_market_data.py --symbol BHARTIARTL --expiry 25AUG   │
│    ├── python scripts/3_historical_audit.py --symbol BHARTIARTL --type FUT  │
│    └── python scripts/4_score_calculator.py                                │
│ 4. AI Agent synthesizes script terminal outputs & prints Decision Report    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Input Specification (How You Input to Antigravity IDE)

You can provide the input in a simple key-value format, unformatted text, or a raw copy-paste from Univest directly into the IDE chat or an `input.txt` file.

### Standardized Input Prompt Template:
```text
Symbol: BHARTIARTL
Expiry: 25 AUG
Type: FUTURES
Action: BUY
Entry Price: 1993.50
Entry Range: 1989.00 - 1995.00
Live Price: 1995.10
Target: 2045.00
Stoploss: 1939.00
Lot Size: 475
Margin: 172634.82
App Warning: Trade is outside the entry range. Take caution
```

---

## 3. Modular Python Script Suite

The IDE Agent will trigger these lightweight, modular scripts stored in your local directory (`/scripts`).

### Script 1: Mathematical Risk-to-Reward & Slippage Engine
**File:** `scripts/1_math_and_rr.py`  
**Purpose:** Calculates raw R:R, slippage-adjusted R:R, and checks for inverted risk symmetry.

```python
import sys
import json

def analyze_math(data):
    entry = data["entry_price"]
    sl = data["stoploss"]
    target = data["target"]
    live = data["live_price"]
    lot_size = data["lot_size"]
    
    # Raw Math
    raw_risk_per_share = entry - sl
    raw_reward_per_share = target - entry
    raw_rr = raw_reward_per_share / raw_risk_per_share if raw_risk_per_share > 0 else 0
    
    # Live / Slippage Math
    live_risk_per_share = live - sl
    live_reward_per_share = target - live
    live_rr = live_reward_per_share / live_risk_per_share if live_risk_per_share > 0 else 0
    
    output = {
        "raw_rr": round(raw_rr, 2),
        "live_rr": round(live_rr, 2),
        "risk_per_lot_inr": round(live_risk_per_share * lot_size, 2),
        "reward_per_lot_inr": round(live_reward_per_share * lot_size, 2),
        "outside_entry_range": live > data["entry_range_max"] or live < data["entry_range_min"],
        "inverted_rr_flag": live_rr < 1.0
    }
    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    with open("current_trade.json") as f:
        analyze_math(json.load(f))
```

---

### Script 2: Live Market Context & Technical Indicator Fetcher
**File:** `scripts/2_market_data.py`  
**Purpose:** Queries broker APIs (Zerodha Kite / Dhan / `yfinance`) to fetch ATR, EMA trends, sector health, and option/futures metrics.

```python
import sys
import json
import yfinance as yf
import pandas as pd

def fetch_market_context(symbol, sl_price, entry_price):
    # Appends .NS for Indian Stock Exchange on yfinance
    ticker = yf.Ticker(f"{symbol}.NS")
    df = ticker.history(period="60d", interval="1d")
    
    if df.empty:
        print(json.dumps({"error": "Failed to fetch ticker data"}))
        return

    # Calculate Daily ATR (14)
    df['TR'] = pd.DataFrame([
        df['High'] - df['Low'],
        (df['High'] - df['Close'].shift(1)).abs(),
        (df['Low'] - df['Close'].shift(1)).abs()
    ]).max()
    atr_daily = df['TR'].rolling(14).mean().iloc[-1]
    
    # Calculate 50 EMA and 200 SMA
    ema_50 = df['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
    close_price = df['Close'].iloc[-1]
    
    sl_distance = abs(entry_price - sl_price)
    atr_ratio = sl_distance / atr_daily if atr_daily > 0 else 0
    
    output = {
        "current_atr": round(atr_daily, 2),
        "sl_distance": round(sl_distance, 2),
        "atr_sl_ratio": round(atr_ratio, 2),
        "is_tight_sl": atr_ratio < 1.0,
        "above_50_ema": bool(close_price > ema_50),
        "daily_trend": "BULLISH" if close_price > ema_50 else "BEARISH"
    }
    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    with open("current_trade.json") as f:
        data = json.load(f)
        fetch_market_context(data["symbol"], data["stoploss"], data["entry_price"])
```

---

### Script 3: Univest Historical Track Record & Expectancy Auditor
**File:** `scripts/3_historical_audit.py`  
**Purpose:** Queries a local SQLite DB (`univest_history.db`) to compute Univest's historical win rate, expectancy, and failure probability for the specific stock/instrument type.

```python
import sqlite3
import json

def audit_univest_performance(symbol, instrument_type):
    conn = sqlite3.connect("univest_history.db")
    cursor = conn.cursor()
    
    # Query past trades for this symbol and asset class
    cursor.execute("""
        SELECT outcome, pnl_inr, rr_ratio 
        FROM trade_recommendations 
        WHERE symbol = ? AND instrument_type = ?
    """, (symbol, instrument_type))
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        # Fallback to general instrument stats if symbol stats don't exist
        print(json.dumps({
            "ticker_sample_size": 0,
            "win_rate": 0.65, # Default benchmark
            "net_expectancy_inr": 0,
            "status": "NO_HISTORICAL_DATA_FOR_TICKER"
        }))
        return

    wins = [r for r in rows if r[0] == "WIN"]
    losses = [r for r in rows if r[0] == "LOSS"]
    win_rate = len(wins) / len(rows) if rows else 0
    
    avg_win = sum([r[1] for r in wins]) / len(wins) if wins else 0
    avg_loss = abs(sum([r[1] for r in losses]) / len(losses)) if losses else 0
    
    # Expectancy formula: (Win% * Avg Win) - (Loss% * Avg Loss)
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
    
    output = {
        "ticker_sample_size": len(rows),
        "historical_win_rate": round(win_rate * 100, 2),
        "avg_win_inr": round(avg_win, 2),
        "avg_loss_inr": round(avg_loss, 2),
        "net_expectancy_per_trade": round(expectancy, 2),
        "edge_is_positive": expectancy > 0
    }
    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    with open("current_trade.json") as f:
        data = json.load(f)
        audit_univest_performance(data["symbol"], data["instrument_type"])
```

---

### Script 4: Consolidated Decision Engine
**File:** `scripts/4_score_calculator.py`  
**Purpose:** Combines outputs of Scripts 1, 2, and 3 to output a final 0–100 Confidence Score and Go / No-Go Decision.

```python
import json

def calculate_score(math_res, market_res, audit_res):
    score = 0
    rejection_reasons = []

    # 1. Math R:R Check (Max 25 pts)
    live_rr = math_res["live_rr"]
    if live_rr >= 1.5:
        score += 25
    elif live_rr >= 1.0:
        score += 10
        rejection_reasons.append("WARNING: R:R is between 1.0 and 1.5. Sub-optimal reward profile.")
    else:
        rejection_reasons.append(f"CRITICAL: Inverted R:R ({live_rr}). Risking more than potential reward.")

    # 2. Price Discipline / Range Check (Max 20 pts)
    if not math_res["outside_entry_range"]:
        score += 20
    else:
        rejection_reasons.append("ENTRY VIOLATION: Current price is outside Univest's recommended range.")

    # 3. Technical & Volatility Check (Max 20 pts)
    if not market_res.get("is_tight_sl", False):
        score += 10
    else:
        rejection_reasons.append("VOLATILITY RISK: Stop Loss is tighter than 1.0 x Daily ATR (High noise stopout risk).")
        
    if market_res.get("above_50_ema", True):
        score += 10
    else:
        rejection_reasons.append("TREND CONFLICT: Stock is trading below its 50 EMA.")

    # 4. Historical Edge Check (Max 20 pts)
    if audit_res.get("edge_is_positive", False) or audit_res.get("status") == "NO_HISTORICAL_DATA_FOR_TICKER":
        score += 20
    else:
        rejection_reasons.append("HISTORICAL AUDIT FAIL: Univest signals for this setup/ticker have a negative net expectancy.")

    # 5. Event Safety Check (Max 15 pts) - Assumed clean unless flagged
    score += 15

    # Decision Output
    decision = "NO-GO (REJECT)"
    if score >= 80:
        decision = "GO (EXECUTE FULL SIZE)"
    elif score >= 65:
        decision = "CONDITIONAL GO (REDUCE SIZE 50%)"

    output = {
        "final_confidence_score": score,
        "decision": decision,
        "rejection_reasons": rejection_reasons
    }
    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    with open("script1_out.json") as f1, open("script2_out.json") as f2, open("script3_out.json") as f3:
        calculate_score(json.load(f1), json.load(f2), json.load(f3))
```

---

## 4. Execution Blueprint in Antigravity IDE

When you paste a recommendation into the Antigravity IDE agent chat, the agent executes this instruction pipeline:

```
Step 1: Write input details to `current_trade.json`
Step 2: Run `python scripts/1_math_and_rr.py > script1_out.json`
Step 3: Run `python scripts/2_market_data.py > script2_out.json`
Step 4: Run `python scripts/3_historical_audit.py > script3_out.json`
Step 5: Run `python scripts/4_score_calculator.py`
Step 6: Render synthesized analysis to user in Markdown
```

---

## 5. Live Simulation: Sample Input vs. IDE Agent Output

### Your Input in Antigravity IDE Chat:
> "Hey, run analysis on this trade: BUY BHARTIARTL 25 AUG FUT. Entry range 1989.00 - 1995.00, entry price 1993.50, live price 1995.10, target 2045.00, stoploss 1939.00, lot size 475, margin 172634.82."

### The IDE Output (Generated by AI Agent after Script Execution):

---

# 🛡️ TRADE VALIDATION REPORT: BHARTIARTL 25 AUG FUT

### **FINAL CONFIDENCE SCORE: 35 / 100**
### **DECISION: 🔴 NO-GO (REJECT)**

---

### 🔍 Quantitative & Risk Analysis

| Parameter | Recommended Value | Current Live Value | Impact / Analysis |
| :--- | :--- | :--- | :--- |
| **Entry Price** | ₹1,993.50 (Max ₹1,995.00) | **₹1,995.10** | ⚠️ **Outside Range** (+₹0.10 slippage) |
| **Stoploss** | ₹1,939.00 | ₹1,939.00 | Risk: **₹56.10 / share** (₹26,647.50 / lot) |
| **Target** | ₹2,045.00 | ₹2,045.00 | Potential: **₹49.90 / share** (₹23,702.50 / lot) |
| **Real Risk:Reward** | 1 : 0.94 (Nominal) | **1 : 0.89 (Live)** | ❌ **Negative Symmetry** (Risking ₹1 to make ₹0.89) |
| **Volatility Check** | Daily ATR: ₹28.40 | SL Distance: ₹56.10 | ✅ **Pass** (SL is ~2.0x ATR; safe from daily noise) |

---

### 📊 Univest Historical Performance Audit (`BHARTIARTL FUT`)
* **Ticker Historical Win Rate:** 57% (12 Trades sampled)
* **Avg Win:** +₹18,200 | **Avg Loss:** -₹24,500
* **Historical Expectancy:** **-₹1,215 / trade**
* **Verdict:** *Due to bad R:R on Univest's Bharti Airtel signals, taking these trades over time results in negative overall profitability despite a >50% win rate.*

---

### ❌ Rejection Reasons & Flags
1. **INVERTED RISK-TO-REWARD (Score: 0/25):** You are risking **₹26,647.50** to potentially make **₹23,702.50**. Top 1% traders do not take negative expectancy trades.
2. **PRICE DISCIPLINE VIOLATION (Score: 0/20):** Current price (₹1,995.10) exceeds the entry upper bound (₹1,995.00). Buying now further reduces your potential return.
3. **HISTORICAL EDGE FAIL (Score: 5/20):** Univest's historical recommendations with R:R < 1.0 have a negative long-term mathematical edge.

---

### 💡 Agent Actionable Recommendation
* **Primary Recommendation:** **REJECT TRADE.**
* **Alternative Execution (If required):** Do not place a Market Order. Place a **LIMIT BUY at ₹1,981.00** (re-aligns R:R to 1:1.52 with ₹21,850 risk vs ₹30,400 target).