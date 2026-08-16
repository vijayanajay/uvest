"""Script 6: Similar-Setup Backtest — empirical success probability.

Measures the CURRENT setup against its own history. The setup is treated as a
*shape*, not a price level: same direction, same reward:risk, and the same
stop tightness relative to volatility. On every eligible historical day D the
reco's stop/target are re-derived from the 14-day ATR at D, the entry is the
open of bar D+1 (no lookahead), and the same conservative trigger rules used
for live resolution (see scripts/5_track_and_resolve.py) decide WIN/LOSS over
a holding window of `window` trading bars:

    * stop hit first -> LOSS, target hit first -> WIN (same-bar ambiguity
      resolves conservatively as LOSS, stop assumed hit first)
    * neither hit -> settled at the window-end close vs entry (mirrors the
      expiry-settlement rule)

The 50-EMA state at each signal day is recorded so the sample splits into
trend-matched and trend-opposed buckets — showing whether the reco's own
technical context historically mattered.

Output contract (script6_out.json):
    symbol, action, rr, stop_atr_ratio, current_atr, window_bars,
    window_origin, period_bars, trend_state,
    sample.overall  : n, target_first, stop_first, settled_at_window_end,
                      wins, losses, success_rate, win_rate, avg_pnl_inr,
                      expectancy_inr
    sample.by_trend : the same shape per 50-EMA state at the signal day
    notes           : caveats (overlapping windows, settlement, costs)

Usage:
    python scripts/6_backtest_setup.py [--symbol X] [--action BUY] [--entry P]
        [--stoploss S] [--target T] [--lot N] [--instrument FUT]
        [--expiry "25 AUG"] [--period 3y] [--window N]

Defaults come from current_trade.json (supplemented by script1_out.json for
days-to-expiry and script2_out.json for the current ATR when present). On
data failure a JSON error payload is emitted (exit code 1) so the
orchestrator degrades gracefully.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    INPUT_FILE,
    OUT1_FILE,
    OUT2_FILE,
    days_to_expiry,
    emit_json,
    estimate_costs,
    fail,
    load_json,
)

DEFAULT_WINDOW = 10   # trading bars when the reco has no usable expiry
MAX_WINDOW = 30       # cap so a long expiry never dilutes the sample
HISTORY_BARS = 50     # bars of history needed before the EMA(50) is meaningful


def load_optional(path: Path) -> dict[str, Any] | None:
    try:
        return load_json(path)
    except (FileNotFoundError, ValueError):
        return None


def fetch_bars(symbol: str, period: str):
    import yfinance as yf

    return yf.Ticker(f"{symbol}.NS").history(period=period, interval="1d")


def indicators(df):
    """Rolling ATR(14) and EMA(50); the value at bar i uses only bars <= i."""
    import pandas as pd

    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(14).mean()
    ema = close.ewm(span=50, adjust=False).mean()
    return atr, ema


def backtest_setup(data: dict[str, Any], bars, window: int, lot: float,
                   inst: str | None) -> dict[str, Any]:
    action = str(data.get("action", "BUY")).upper()
    entry = float(data["entry_price"])
    sl = float(data["stoploss"])
    target = float(data["target"])

    # ---- Setup signature: R:R at the recommended entry + stop tightness ----
    if action == "SELL":
        risk = sl - entry
        reward = entry - target
    else:
        risk = entry - sl
        reward = target - entry
    rr = reward / risk if risk > 0 else 0.0
    if rr <= 0 or risk <= 0:
        fail("Setup has no usable R:R (check entry/stoploss/target).")

    # Reference ATR: prefer script2's live fetch, else the last bar here.
    s2 = load_optional(OUT2_FILE)
    if s2 and "error" not in s2 and s2.get("current_atr"):
        current_atr = float(s2["current_atr"])
    else:
        atr_all, _ = indicators(bars)
        last_atr = atr_all.iloc[-1]
        if not last_atr or last_atr <= 0:
            fail("Cannot compute a reference ATR for the setup signature.")
        current_atr = float(last_atr)
    stop_atr_ratio = abs(entry - sl) / current_atr if current_atr > 0 else 0.0

    atr_s, ema_s = indicators(bars)
    opens = bars["Open"].tolist()
    highs = bars["High"].tolist()
    lows = bars["Low"].tolist()
    closes = bars["Close"].tolist()
    n = len(bars)

    if n < HISTORY_BARS + window + 1:
        fail(f"Not enough history for a backtest ({n} bars; need >= {HISTORY_BARS + window + 1}).")

    # ---- Replay: every bar d (with enough history) is a candidate signal ----
    rows: list[dict[str, Any]] = []
    for d in range(HISTORY_BARS - 1, n - window):
        atr_d = atr_s.iloc[d]
        if not atr_d or atr_d <= 0:
            continue
        ema_d = ema_s.iloc[d]
        close_d = float(closes[d])
        risk_d = stop_atr_ratio * float(atr_d)
        reward_d = risk_d * rr
        if risk_d <= 0 or reward_d <= 0:
            continue

        entry_px = float(opens[d + 1])
        if action == "SELL":
            stop_c, target_c = entry_px + risk_d, entry_px - reward_d
        else:
            stop_c, target_c = entry_px - risk_d, entry_px + reward_d

        outcome: str | None = None
        final: float | None = None
        triggered = False
        for j in range(d + 1, d + window + 1):
            lo, hi = lows[j], highs[j]
            if action == "SELL":
                if hi >= stop_c:            # price rose to the stop first
                    outcome, final = "LOSS", stop_c
                    triggered = True
                    break
                if lo <= target_c:          # price fell to target
                    outcome, final = "WIN", target_c
                    triggered = True
                    break
            else:
                if lo <= stop_c:            # stop first (conservative on same-bar ambiguity)
                    outcome, final = "LOSS", stop_c
                    triggered = True
                    break
                if hi >= target_c:
                    outcome, final = "WIN", target_c
                    triggered = True
                    break
        if outcome is None:                 # neither within the window: settle at window-end close
            final = float(closes[d + window])
            gross = (final - entry_px) if action == "BUY" else (entry_px - final)
            outcome = "WIN" if gross > 0 else "LOSS"

        costs = estimate_costs(entry_px, lot, inst)
        pnl = ((final - entry_px) if action == "BUY" else (entry_px - final)) * lot - costs
        rows.append({
            "trend": "BULLISH" if close_d > ema_d else "BEARISH",
            "outcome": outcome,
            "triggered": triggered,
            "pnl_inr": round(pnl, 2),
        })

    def agg(rs: list[dict[str, Any]]) -> dict[str, Any]:
        nn = len(rs)
        tf = sum(1 for r in rs if r["triggered"] and r["outcome"] == "WIN")
        sf = sum(1 for r in rs if r["triggered"] and r["outcome"] == "LOSS")
        neu = sum(1 for r in rs if not r["triggered"])
        wins = sum(1 for r in rs if r["outcome"] == "WIN")
        losses = nn - wins
        return {
            "n": nn,
            "target_first": tf,
            "stop_first": sf,
            "settled_at_window_end": neu,
            "wins": wins,
            "losses": losses,
            "success_rate": round(tf / nn * 100, 1) if nn else None,
            "win_rate": round(wins / nn * 100, 1) if nn else None,
            "avg_pnl_inr": round(sum(r["pnl_inr"] for r in rs) / nn, 2) if nn else 0.0,
            "net_pnl_inr": round(sum(r["pnl_inr"] for r in rs), 2),
        }

    bullish = [r for r in rows if r["trend"] == "BULLISH"]
    bearish = [r for r in rows if r["trend"] == "BEARISH"]
    return {
        "symbol": data["symbol"],
        "action": action,
        "rr": round(rr, 2),
        "stop_atr_ratio": round(stop_atr_ratio, 2),
        "current_atr": round(current_atr, 2),
        "window_bars": window,
        "period_bars": n,
        "trend_state": "BULLISH" if float(closes[-1]) > ema_s.iloc[-1] else "BEARISH",
        "sample": {
            "overall": agg(rows),
            "by_trend": {"BULLISH": agg(bullish), "BEARISH": agg(bearish)},
        },
        "notes": [
            "The setup is tested as a shape (same R:R and ATR-relative stop distance), not a specific price level.",
            "Consecutive signal days produce overlapping windows - treat the sample as correlated, not independent trades.",
            "Candidates that never hit target or stop within the window are settled at the window-end close vs entry (mirrors expiry settlement).",
            "P&L is net of modeled transaction costs.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest the current setup against its own history.")
    parser.add_argument("--symbol", help="Ticker symbol (default: from current_trade.json)")
    parser.add_argument("--action", help="BUY/SELL (default: from current_trade.json)")
    parser.add_argument("--entry", type=float, help="Entry price (default: from current_trade.json)")
    parser.add_argument("--stoploss", type=float, help="Stoploss (default: from current_trade.json)")
    parser.add_argument("--target", type=float, help="Target (default: from current_trade.json)")
    parser.add_argument("--lot", type=float, help="Lot size for P&L (default: from current_trade.json)")
    parser.add_argument("--instrument", help="FUT/OPT for cost modeling (default: from current_trade.json)")
    parser.add_argument("--expiry", help="Expiry label, e.g. '25 AUG' (default: from current_trade.json)")
    parser.add_argument("--period", default="3y", help="yfinance lookback, e.g. 2y/3y (default: 3y)")
    parser.add_argument("--window", type=int, help="Holding window in trading bars (default: from days-to-expiry, capped at 30)")
    args = parser.parse_args()

    try:
        data = load_json(INPUT_FILE)
    except FileNotFoundError:
        fail("Input file not found: current_trade.json. Create it first (see current_trade.json).")

    for field, arg in (("symbol", args.symbol), ("entry_price", args.entry),
                       ("stoploss", args.stoploss), ("target", args.target)):
        if arg is not None:
            data[field] = arg
    if args.action:
        data["action"] = args.action
    if args.expiry:
        data["expiry"] = args.expiry

    for required in ("symbol", "entry_price", "stoploss", "target"):
        if required not in data:
            fail(f"Missing required field: {required} (provide it or add it to current_trade.json)")

    lot = float(args.lot if args.lot is not None else data.get("lot_size", 0))
    inst = args.instrument or data.get("instrument_type")

    # Holding window: derived from the reco's days-to-expiry when available.
    window = args.window
    window_origin = "cli"
    if window is None:
        expiry_text = data.get("expiry")
        days = None
        if expiry_text:
            _, days = days_to_expiry(expiry_text)
        if days and days > 0:
            window = max(1, min(round(days * 5 / 7), MAX_WINDOW))
            window_origin = "expiry"
        else:
            window = DEFAULT_WINDOW
            window_origin = "default"

    try:
        bars = fetch_bars(data["symbol"], args.period)
    except Exception as exc:
        fail(f"Backtest data fetch failed for {data['symbol']}: {exc}")
    if bars is None or bars.empty:
        fail(f"No historical bars returned for {data['symbol']}.NS")

    out = backtest_setup(data, bars, window, lot, inst)
    out["window_origin"] = window_origin
    emit_json(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
