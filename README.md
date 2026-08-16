# Antigravity IDE Native Trade Validation Pipeline

An implementation of `BRD.md`: a local, script-driven trade validation
workflow that turns a raw trade recommendation into a **0–100 Confidence
Score** and a **GO / No-Go decision**, backed by real math, live market
data, and a historical track-record audit.

No OCR, no vision layer — you paste text/JSON, the agent runs four modular
Python scripts, and the suite prints a synthesized Decision Report.

```
current_trade.json ──► 1_math_and_rr.py ──► 2_market_data.py ──► 3_historical_audit.py
        (input)              │                     │                     │
                            ▼                     ▼                     ▼
                     script1_out.json      script2_out.json      script3_out.json
                            └───────────► 4_score_calculator.py ◄──────────┘
                                                  │
                                                  ▼
                                        Confidence Score + Decision
                                                  │
                                                  ▼
                                       decision_report.md (Markdown)
```

## Quick Start

```bash
# 1. Install dependencies (Python 3.11+)
python -m pip install -r requirements.txt

# 2. Seed the historical audit database (one-time)
python scripts/seed_history.py --fresh

# 3. Edit current_trade.json with your trade (or copy input.txt format)

# 4. Run the full pipeline — one command
python scripts/run_analysis.py
```

The Decision Report is printed to stdout and saved to `decision_report.md`.

## Running Individual Scripts (BRD Blueprint)

```bash
python scripts/1_math_and_rr.py                    # > script1_out.json
python scripts/2_market_data.py --symbol BHARTIARTL --expiry 25AUG   # > script2_out.json
python scripts/3_historical_audit.py --symbol BHARTIARTL --type FUT  # > script3_out.json
python scripts/4_score_calculator.py
```

Each script emits one JSON object to stdout — the machine-readable
contract between stages. `scripts/run_analysis.py` automates all of the
above in the exact order the BRD specifies.

## Input

`current_trade.json` is the canonical input. A raw copy-paste from
Univest (see `input.txt`) is accepted by any agentic IDE: the agent
normalizes it into `current_trade.json`, then runs the pipeline.

```json
{
  "symbol": "BHARTIARTL",
  "expiry": "25 AUG",
  "instrument_type": "FUT",
  "action": "BUY",
  "entry_price": 1993.50,
  "entry_range_min": 1989.00,
  "entry_range_max": 1995.00,
  "live_price": 1995.10,
  "target": 2045.00,
  "stoploss": 1939.00,
  "lot_size": 475,
  "margin": 172634.82,
  "app_warning": "Trade is outside the entry range. Take caution"
}
```

## What Each Script Does

| Script | Purpose |
| :--- | :--- |
| `1_math_and_rr.py` | Raw & live (slippage-adjusted) R:R, per-lot risk/reward in INR, entry-range violation & inverted-R:R flags |
| `2_market_data.py` | Yahoo Finance (`yfinance`): 14-day ATR, 50 EMA, trend direction, stop-loss distance vs ATR |
| `3_historical_audit.py` | SQLite (`univest_history.db`): historical win rate, avg win/loss, net expectancy per trade for symbol + instrument type |
| `4_score_calculator.py` | Weighted decision engine: 25 pts R:R, 20 price discipline, 20 technical, 20 historical edge, 15 event safety |
| `seed_history.py` | Creates & seeds `univest_history.db` with sample track records (see below) |
| `run_analysis.py` | Orchestrates the full blueprint and renders `decision_report.md` |

## Scoring & Decisions

| Score | Decision |
| :--- | :--- |
| 80 – 100 | 🟢 **GO (EXECUTE FULL SIZE)** |
| 65 – 79 | 🟡 **CONDITIONAL GO (REDUCE SIZE 50%)** |
| 0 – 64 | 🔴 **NO-GO (REJECT)** |

## Seeded Historical Data

`scripts/seed_history.py` loads 30 sample trades demonstrating every
branch of the audit:

* **BHARTIARTL FUT** (12 trades, 58% win rate) — **negative expectancy**
  (`-₹1,216.67/trade`): the classic "wins often, loses big" trap → REJECT
* **RELIANCE FUT** (10 trades) — **positive expectancy** (`+₹13,000/trade`)
  → historically approve
* **NIFTY OPT** (8 trades) — positive expectancy on options
* **HDFCBANK FUT** — intentionally no rows, exercising the
  `NO_HISTORICAL_DATA_FOR_TICKER` fallback

Replace the sample rows with real recommendation history (from Univest,
broker APIs, or CSV exports) for production use. Schema:

```sql
CREATE TABLE trade_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    instrument_type TEXT NOT NULL,   -- FUT / OPT
    outcome TEXT NOT NULL,           -- WIN / LOSS
    pnl_inr REAL NOT NULL,
    rr_ratio REAL,
    entry_price REAL, target REAL, stoploss REAL,
    recommended_on TEXT
);
```

## Notes

* Script 2 needs internet access (Yahoo Finance). If it fails, the suite
  degrades gracefully: volatility/trend points are waived and the report
  says so explicitly — it never invents numbers.
* `prompt.md` is the ready-to-paste master prompt that drives the entire
  workflow in any agentic IDE (Antigravity / Cursor / Windsurf / VS Code).
