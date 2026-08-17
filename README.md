# Antigravity IDE Native Trade Validation Pipeline

An implementation of `BRD.md`: a local, script-driven trade validation
workflow that turns a raw trade recommendation into a **0–100 Confidence
Score** and a **GO / No-Go decision** — backed by real math, live market
data, and a **self-building historical track record**.

No OCR, no vision layer — you paste text/JSON, the agent runs modular
Python scripts, and the suite prints a synthesized Decision Report.

```
current_trade.json ──► 1_math_and_rr.py ──► 2_market_data.py ──► 3_historical_audit.py
        (input)              │                     │                     │
                            ▼                     ▼                     ▼
                     script1_out.json      script2_out.json      script3_out.json
                            │                     │                     │
                            └───────────┬─────────┘                     │
                                        │                               ▼
                                        │                         6_backtest_setup.py
                                        │                    (similar-setup backtest)
                                        │                               │
                                        │                               ▼
                                        │                          script6_out.json
                                        │                               │
                                        └──────────────┬────────────────┘
                                                       ▼
                                            4_score_calculator.py
                                        (score + flip table + activation)
                                                       │
                                                       ▼
                                            Confidence Score + Decision
                                                       │
                                                       ▼
                                      5_track_and_resolve.py  ──►  univest_history.db
                                           (learning loop)        (real track record)
                                                       │
                                                       ▼
                                            decision_report.md (Markdown)
```

The report also tells you **what would change the decision** (per-check flip
thresholds, conflicts, and the best-case entry zone) and — via
`scripts/watch.py` — lets you monitor the live price against that
activation trigger as a standing order.

## Quick Start

```bash
# 1. Install dependencies (Python 3.11+)
python -m pip install -r requirements.txt

# 2. Bootstrap the track-record DB (optional — pipeline auto-creates it)
python scripts/seed_history.py --fresh

# 3. Edit current_trade.json with your trade (or copy input.txt format)

# 4. Run the full pipeline — one command
python scripts/run_analysis.py
```

The Decision Report is printed to stdout and saved to `decision_report.md`.

## The Learning Loop (why the DB exists)

Every time you run the pipeline, **the recommendation you analyzed is
recorded in `univest_history.db`** as an `OPEN` row — along with its
confidence score and GO/NO-GO decision. On your *next* run, Script 5
re-checks every open recommendation against live prices and resolves it:

* price hit the **target** → `WIN`
* price hit the **stoploss** → `LOSS`
* neither yet → stays `OPEN`

Resolution scans daily bars from the recommendation date; the first
trigger wins (same-bar ambiguity is resolved conservatively as a LOSS).
Nothing resolves on the recommendation day itself. Triggers are only
honoured up to the recommendation's **expiry**: if neither target nor
stop is hit and the data covers the expiry date, the reco is settled at
the last close on/before expiry (WIN if that close is past entry for
BUY / below entry for SELL, else LOSS) — nothing stays OPEN past its
contract. All resolved P&L is **net of modeled transaction costs**
(~0.09% of notional for FUT, ~0.25% for OPT, see `common.estimate_costs`),
so the track record does not flatter itself.

The report then shows you, from your **real** accumulated record:

* total tracked / open / resolved recommendations
* what was **resolved this run** (wins/losses with P&L)
* real win rate, avg win/loss, expectancy, net P&L
* **decision accuracy**: hit rate of your GO/CONDITIONAL calls, and on
  NO-GO calls — how many *would have won* (**missed profit**) vs *would
  have lost* (**losses avoided**)

**This is what feeds back into decision-making:** Script 3's historical
audit prefers your real tracked resolutions for a ticker and only falls
back to bootstrap seed data while no real data exists yet. So the
"historical edge" check gets sharper every time you use the system — the
NO-GO discipline is continuously validated (or corrected) by what
actually happened.

## Running Individual Scripts (BRD Blueprint)

```bash
python scripts/1_math_and_rr.py                    # > script1_out.json
python scripts/2_market_data.py --symbol BHARTIARTL --expiry 25AUG   # > script2_out.json
python scripts/3_historical_audit.py --symbol BHARTIARTL --type FUT  # > script3_out.json
python scripts/6_backtest_setup.py                 # > script6_out.json
python scripts/4_score_calculator.py               # score + flip table + activation
python scripts/5_track_and_resolve.py --score 35 --decision "NO-GO (REJECT)"  # track + resolve
python scripts/watch.py --loop                     # monitor the live price against the trigger
```

Each script emits one JSON object to stdout — the machine-readable
contract between stages. `scripts/run_analysis.py` automates all of the
above in the exact order the BRD specifies. Add `--no-track` to analyze
without recording the trade.

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
  "app_warning": "Trade is outside the entry range. Take caution",
  "recommended_on": "2026-08-15",
  "source": "univest"
}
```

`recommended_on` and `source` are optional. When `recommended_on` is
provided, the pipeline identifies the recommendation by **(symbol,
instrument type, action, expiry, reco date)** — so re-pasting the same reco
from Univest (or any app) on a later day updates the same tracked row
(refreshing price/score, counting re-analyses) instead of duplicating it.
Without it, identification falls back to the setup geometry
(symbol/type/action/entry/target/stoploss), which also merges identical
re-pastes.

## What Each Script Does

| Script | Purpose |
| :--- | :--- |
| `1_math_and_rr.py` | Raw & live (slippage-adjusted) R:R, per-lot risk/reward in INR, entry-range violation & inverted-R:R flags |
| `2_market_data.py` | Yahoo Finance (`yfinance`): 14-day ATR, 50 EMA, trend direction, stop-loss distance vs ATR |
| `3_historical_audit.py` | SQLite audit: win rate, avg win/loss, net expectancy for symbol + type — **prefers real tracked data, falls back to seeds** |
| `4_score_calculator.py` | Weighted decision engine: 25 pts R:R, 20 price discipline, 20 technical, 20 historical edge, 15 event safety (real check: days to expiry) — **waiver model**: unevaluable checks score 0 and are labelled, never auto-passed. **Empirical mode** (default): when the script-6 backtest is usable, the historical-edge check is scored from the measured win rate gated on its Wilson CI lower bound vs the setup's net-of-costs break-even win rate, and the trend/event-safety checks are waived (their signals are embedded in the backtest). Also emits the **flip table**: per-check thresholds that would change the decision, conflicts between checks, the best-case entry zone, and the activation price |
| `5_track_and_resolve.py` | **Learning loop**: resolves open recos vs live prices, records the current reco (identified by **stock + instrument + reco date** so re-pasting the same reco on a later day updates the same row), reports track-record & missed/avoided stats + **score calibration** (win rate per decision bucket with Wilson CI) |
| `6_backtest_setup.py` | **Empirical success probability**: replays the current setup — same direction, same R:R, same ATR-relative stop — across 3 years of daily bars using the resolver's trigger rules; reports hit-target-first rate, win rate **with a Wilson 95% CI**, and trend-matched vs trend-opposed buckets |
| `watch.py` | **Standing-order monitor**: computes the activation price/zone from `current_trade.json` and polls the live price until the trade triggers (`--loop`) |
| `seed_history.py` | Bootstraps `univest_history.db` with sample history (only used until real data exists) |
| `run_analysis.py` | Orchestrates the full blueprint + learning loop and renders `decision_report.md` |

## Scoring & Decisions

| Effective score | Decision |
| :--- | :--- |
| ≥ 80% of available | 🟢 **GO (EXECUTE FULL SIZE)** |
| 65 – 79% of available | 🟡 **CONDITIONAL GO (REDUCE SIZE 50%)** |
| < 65% of available | 🔴 **NO-GO (REJECT)** |

The bands apply to the **share of available points**, not a flat 0–100:
components that cannot be evaluated (market data offline, no track record
for the ticker, no expiry supplied) earn **0 and are marked waived** — they
neither inflate nor penalize the score. Event safety is a real check now:
≥ 5 days to expiry → 15 pts, 3–4 days → 10 pts, < 3 days → 0 pts, and a
sub-3-day expiry **caps the decision at CONDITIONAL GO** (never full size
into expiry-day whipsaw).

**Empirical mode (statistical gating):** when the similar-setup backtest
(script 6) succeeds with a large enough sample, the **historical-edge check
is a measured probability, not a rubric opinion**. The backtest's win rate
— read from the trend-matched bucket when possible — is gated on its
**Wilson 95% CI lower bound clearing the setup's net-of-costs break-even
win rate** `p* = 1/(1 + net_rr)`: CI lower bound > `p*` → 20 pts (edge
confirmed), win rate > `p*` but CI straddles it → 10 pts (positive but
unconfirmed), otherwise 0 pts (no edge — the data does not reject the coin
flip). Because the backtest already conditions on the 50-EMA trend context
and derives its holding window from days-to-expiry, **trend and event
safety are waived in empirical mode** (no double counting) — the sub-3-day
expiry cap still applies. If the backtest fails or its sample is too
small, the score falls back to the ticker track-record rubric.

## Decision Support (what raises the chance of success)

Beyond the score, the pipeline answers three questions that matter more than
the number itself:

### 1. Similar-setup backtest — the probability *before* you trade

`scripts/6_backtest_setup.py` replays the current setup across up to 3 years
of daily bars. The setup is treated as a **shape, not a price level**: every
historical day re-derives the stop/target from the reco's R:R and its stop
distance relative to that day's ATR, enters at the next bar's open (no
lookahead), and resolves with the exact conservative trigger rules the
learning loop uses (stop-first on same-bar ambiguity, window-end settlement
for untriggered candidates). The report shows the hit-target-first rate, the
win rate **with a Wilson 95% CI**, and the split by trend context — whether
the reco's own bullish/bearish context historically mattered. The report
also states the setup's **break-even win rate** (net of modeled costs) and
whether the empirical win rate's CI lower bound clears it — this gating is
what actually drives the historical-edge score (see *Empirical mode* above).

Caveats the report states out loud: consecutive signal days produce
**overlapping (correlated)** windows, and the test is of the setup *shape*
— it says nothing about the specific price level.

### 2. What would change the decision (flip table)

For every failing check the report states the exact condition that flips it:

* "Enter at or below ₹906.50 → R:R ≥ 1.0 (+10 → 45/80 → still NO-GO)"
* "Enter within ₹904.50–₹907.50 → price discipline (+20 → 55/80 → CONDITIONAL)"
* **Conflicts**: when the R:R ≥ 1.5 price lies outside the recommended
  range, the two price-based checks can never both pass — the
  target/stoploss geometry itself caps the score.
* **Best case by entry timing alone**: the price *zone* (e.g.
  ₹904.50–₹906.50) that reaches the best achievable decision, with the
  best fill highlighted — the standing-order trigger.

### 3. Score calibration — is the confidence honest?

Every resolved tracked recommendation is bucketed by its decision label and
reported with sample size, observed win rate, a **Wilson 95% confidence
interval**, and a Bayesian-smoothed estimate (uniform prior). Until at least
~5 resolved tracked recos exist the report says so explicitly: the score is
a *rubric opinion*, not a *measured probability*. Once outcomes accumulate
the decision bands become re-tunable from real data instead of fiat.

### 4. Watch mode — patience as an edge

```bash
python scripts/watch.py            # one status check: live price vs trigger
python scripts/watch.py --loop     # poll every 30s until the trade activates
```

`watch.py` computes the activation price/zone straight from
`current_trade.json`, fetches the live market price, and reports how far the
trade is from activation. When it triggers, re-run the pipeline to re-score
with the new price.

## Database

Schema (versioned via `PRAGMA user_version`; v1 seed-only DBs are rebuilt
automatically):

```sql
CREATE TABLE trade_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    instrument_type TEXT NOT NULL,      -- FUT / OPT
    action TEXT,                        -- BUY / SELL
    outcome TEXT NOT NULL,              -- WIN / LOSS / OPEN
    pnl_inr REAL, rr_ratio REAL,
    entry_price REAL, target REAL, stoploss REAL,
    entry_range_min REAL, entry_range_max REAL,
    live_price REAL, lot_size REAL, margin REAL,
    confidence_score INTEGER, decision TEXT,
    recommended_on TEXT, resolved_on TEXT, final_price REAL,
    expiry TEXT,       -- original Univest label, e.g. "25 AUG"
    expiry_date TEXT,  -- parsed ISO date, used for expiry settlement
    costs_inr REAL,    -- modeled transaction costs deducted from pnl_inr
    is_seed INTEGER NOT NULL DEFAULT 0, -- 1 = bootstrap data
    source TEXT,               -- where the reco came from (univest / other)
    analysis_count INTEGER NOT NULL DEFAULT 1, -- re-pastes of the same reco
    last_analyzed_on TEXT,     -- most recent re-analysis date
    UNIQUE (symbol, instrument_type, action, entry_price, target, stoploss)
);
```

Notes:

* `seed_history.py --fresh` wipes **everything, including your real
  tracked record** — only use it to restart the loop from scratch.
* Without `--fresh`, seeding is skipped once bootstrap rows exist, and it
  never touches tracked rows.
* The unique key means re-analyzing the same setup refreshes its row
  (live price, score, decision) instead of duplicating it. When the input
  carries `recommended_on`, the row is first looked up by app identity
  (stock + instrument + action + expiry + reco date) so a reco re-pasted on
  a later day is recognised as the **same** recommendation: `analysis_count`
  increments, `last_analyzed_on` updates, and the report says so. The
  originally recommended contract (entry/target/stoploss) and reco date are
  never rewritten on a match, so resolution windows and the track record
  stay honest; an identical setup always merges into one row regardless of
  paste date.

## Notes

* Script 2 needs internet access (Yahoo Finance). If it fails, the suite
  degrades gracefully: volatility/trend points are waived and the report
  says so explicitly — it never invents numbers. Offline resolution
  (`--offline`) leaves open recos pending.
* `prompt.md` is the ready-to-paste master prompt that drives the entire
  workflow — including the learning loop — in any agentic IDE
  (Antigravity / Cursor / Windsurf / VS Code).
