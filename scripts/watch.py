"""Watch the live price against the recommendation's activation triggers.

Computes the price thresholds that would flip the price-dependent checks
(R:R >= 1.5 entry price, the recommended entry range) straight from
current_trade.json, then fetches the live market price and reports how far
the trade is from activation — the "standing-order trigger" the report
points at.

Usage:
    python scripts/watch.py [--loop] [--interval 30]

Without --loop it prints one JSON status and exits. With --loop it polls the
live price every --interval seconds and exits 0 the moment the price enters
the recommended range (or reaches the R:R >= 1.5 trigger), so it can be left
running as a standing-order monitor. Ctrl-C stops it.

Emits {"error": ...} (exit code 1) when the input is missing or the live
price cannot be fetched.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INPUT_FILE, emit_json, fail, flip_prices, load_json  # noqa: E402


def fetch_live_price(symbol: str) -> float | None:
    """Best-effort live price: fast_info, then recent intraday bars."""
    import yfinance as yf

    ticker = yf.Ticker(f"{symbol}.NS")
    try:
        p = float(ticker.fast_info.last_price)
        if p and p > 0:
            return p
    except Exception:
        pass
    for interval in ("5m", "1d"):
        try:
            df = ticker.history(
                period="1d" if interval == "5m" else "2d", interval=interval
            )
            if not df.empty:
                return float(df["Close"].iloc[-1])
        except Exception:
            continue
    return None


def evaluate(data: dict[str, Any], price: float | None) -> dict[str, Any]:
    action = str(data.get("action", "BUY")).upper()
    sl = float(data["stoploss"])
    target = float(data["target"])
    live = float(data["live_price"])
    rmin = data.get("entry_range_min")
    rmax = data.get("entry_range_max")
    fp = flip_prices(sl, target, rmin, rmax)
    p15, p10 = fp["rr_1_5_price"], fp["rr_1_0_price"]

    if price is None:
        return {
            "status": "unknown",
            "message": "Live price unavailable (network?). Re-run when connectivity is restored.",
            "triggers": fp,
            "reco_live_price": live,
        }

    risk = (sl - price) if action == "SELL" else (price - sl)
    reward = (price - target) if action == "SELL" else (target - price)
    rr = reward / risk if risk > 0 else 0.0
    in_range = rmin is not None and rmax is not None and rmin <= price <= rmax

    triggers_hit: list[str] = []
    if in_range:
        triggers_hit.append(f"inside entry range ₹{rmin:,.2f}–₹{rmax:,.2f}")
    if action == "BUY":
        if price <= p15:
            triggers_hit.append(f"R:R ≥ 1.5 achieved (≤ ₹{p15:,.2f})")
    elif price >= p15:
        triggers_hit.append(f"R:R ≥ 1.5 achieved (≥ ₹{p15:,.2f})")

    bits = [f"Live ₹{price:,.2f} · reco live ₹{live:,.2f} · R:R at price {rr:.2f}"]
    if triggers_hit:
        bits.append("✓ " + " · ".join(triggers_hit))
    else:
        if action == "BUY":
            if rmin is not None and price > rmax:
                bits.append(f"wait: price must fall to ≤ ₹{rmax:,.2f} to re-enter the range")
            gap = price - p15
            if gap > 0:
                bits.append(f"wait: price must fall ₹{gap:,.2f} to reach the R:R ≥ 1.5 trigger (₹{p15:,.2f})")
            else:
                bits.append("R:R ≥ 1.5 trigger reached (price is at/below the trigger)")
        else:
            if rmin is not None and price < rmin:
                bits.append(f"wait: price must rise to ≥ ₹{rmin:,.2f} to re-enter the range")
            gap = p15 - price
            if gap > 0:
                bits.append(f"wait: price must rise ₹{gap:,.2f} to reach the R:R ≥ 1.5 trigger (₹{p15:,.2f})")
            else:
                bits.append("R:R ≥ 1.5 trigger reached (price is at/above the trigger)")

    return {
        "status": "ACTIVATED" if triggers_hit else "WAITING",
        "message": " · ".join(bits),
        "triggers": fp,
        "live_price": round(price, 2),
        "rr_at_price": round(rr, 2),
        "in_range": in_range,
        "triggers_hit": triggers_hit,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor the live price against the reco's activation triggers.")
    parser.add_argument("--loop", action="store_true", help="Poll until the price activates the trade")
    parser.add_argument("--interval", type=float, default=30.0, help="Seconds between polls in --loop mode")
    args = parser.parse_args()

    try:
        data = load_json(INPUT_FILE)
    except FileNotFoundError:
        fail("Input file not found: current_trade.json. Create it first (see current_trade.json).")

    if not args.loop:
        emit_json(evaluate(data, fetch_live_price(data["symbol"])))
        return 0

    print("Watching — Ctrl-C to stop.", file=sys.stderr)
    try:
        while True:
            res = evaluate(data, fetch_live_price(data["symbol"]))
            print(f"[{time.strftime('%H:%M:%S')}] {res['message']}", flush=True)
            if res["status"] == "ACTIVATED":
                print("ACTIVATED — the price-level triggers are met. Re-run the pipeline to re-score.", flush=True)
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nWatch stopped.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
