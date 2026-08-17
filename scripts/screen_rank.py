"""Screen 3: Gating, ranking and the pooled horizon/target summary.

Reads the per-stock backtest (screen_stats_out.json) and produces the
ranked watchlist plus the summary that re-answers the horizon question
live on the current universe:

    * per-cell pooled table (windows pooled across stocks): hit-target-
      first rate, win rate, Wilson CI, net expectancy
    * per-cell cross-sectional counts: how many stocks have n >= 30 and a
      confirmed edge (CI low > break-even) or positive expectancy

RANKING IS A FILTER, NOT AN ALPHA CLAIM: measured per-stock hit rates have
~zero out-of-sample persistence (corr 0.00-0.08 on a 60-name, 2y test), so
the ranked list is how you *select candidates for the sentiment overlay*,
never a promise. Every row carries its flags (inverted R:R, low n) and the
honest tri-state verdict so the daily prompt never confuses a high hit
rate with a positive edge.

Output contract (screen_rank_out.json):
    generated_on, params, summary, return_pool (pooled forward-return range
    over EDGE CONFIRMED BUY names), top: [rows with full cells matrix and
    the per-horizon forward-return distribution]

Usage:
    python scripts/screen_rank.py --stats screen_stats_out.json
        [--horizon 10] [--hit 4.0] [--top 20] [--min-n 30]
        [--sort hit|expectancy|edge] [--long-only]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PROJECT_ROOT, emit_json, load_json, wilson_ci  # noqa: E402

VERDICT_RANK = {"EDGE CONFIRMED": 0, "POSITIVE BUT UNCONFIRMED": 1, "NO EDGE": 2}


def risk_tier(stop_pct: float) -> str:
    """Risk tier from the ATR-based stop distance (% of price)."""
    if stop_pct <= 2.5:
        return "LOW"
    if stop_pct <= 5.0:
        return "MEDIUM"
    return "HIGH"


def summarize(symbols: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Pooled + cross-sectional view per (horizon, hit) cell."""
    cells: dict[str, dict[str, Any]] = {}
    for hz in params["horizons"]:
        for hit in params["hit_levels"]:
            key = f"{hz}:{hit:.1f}"
            rows: list[dict[str, Any]] = []
            for sym, st in symbols.items():
                cell = st.get("cells", {}).get(key)
                if cell and cell.get("n"):
                    rows.append((sym, st, cell))
            if not rows:
                continue
            n = sum(r[2]["n"] for r in rows)
            wins = sum(r[2]["wins"] for r in rows)
            tf = sum(r[2]["target_first"] for r in rows)
            exp = sum(r[2]["expectancy_pct"] * r[2]["n"] for r in rows) / n
            moved = sum(r[2]["p_moved_any"] * r[2]["n"] for r in rows) / n
            ci_low, ci_high = wilson_ci(wins, n)
            confirmed = sum(1 for _, st, c in rows if c["n"] >= 30 and c["verdict"] == "EDGE CONFIRMED")
            pos_exp = sum(1 for _, st, c in rows if c["expectancy_pct"] > 0)
            cells[key] = {
                "n_windows": n,
                "n_stocks": len(rows),
                "target_first_rate": round(tf / n * 100, 1),
                "win_rate": round(wins / n * 100, 1),
                "ci_low": round(ci_low * 100, 1),
                "ci_high": round(ci_high * 100, 1),
                "avg_expectancy_pct": round(exp, 2),
                "p_moved_any": round(moved, 1),
                "confirmed_edges": confirmed,
                "positive_expectancy_stocks": pos_exp,
            }
    return cells


def return_pool(symbols: dict[str, Any], horizon: int, hit: float) -> dict[str, Any]:
    """Pooled forward-return range over the "solid possibility of a rise" set.

    A stock qualifies when it is BUY-direction and its ranking cell is
    EDGE CONFIRMED (the same gate the daily prompt picks under). Per stock
    the full 1W/2W/4W return distribution is listed; the pooled line uses
    exact weighted means / weighted % positive (pooled quantiles would need
    the raw samples, which are not persisted — per-stock quantiles are in
    `stocks[].returns`).
    """
    key = f"{horizon}:{hit:.1f}"
    stocks: list[dict[str, Any]] = []
    for sym, st in symbols.items():
        if st.get("direction") != "BUY":
            continue
        cell = st.get("cells", {}).get(key)
        if not cell or cell.get("verdict") != "EDGE CONFIRMED":
            continue
        rets = st.get("returns") or {}
        if not rets:
            continue
        stocks.append({
            "symbol": sym,
            "close": st["close"],
            "stop_pct": st["stop_pct"],
            "returns": rets,
        })
    horizons: dict[str, Any] = {}
    for hz in ("5", "10", "20"):
        n_w = 0
        wsum = 0.0
        wpos = 0.0
        for s in stocks:
            r = s["returns"].get(hz)
            if r and r.get("n"):
                n_w += r["n"]
                wsum += r["mean"] * r["n"]
                wpos += r["pct_positive"] * r["n"]
        if n_w:
            horizons[hz] = {
                "n_windows": n_w,
                "n_stocks": len(stocks),
                "mean": round(wsum / n_w, 2),
                "pct_positive": round(wpos / n_w, 1),
            }
    return {"qualifying_stocks": len(stocks), "horizons": horizons, "stocks": stocks}


def build_rows(symbols: dict[str, Any], horizon: int, hit: float, min_n: int,
               long_only: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    key = f"{horizon}:{hit:.1f}"
    for sym, st in symbols.items():
        if long_only and st.get("direction") != "BUY":
            continue
        cell = st.get("cells", {}).get(key)
        if not cell or not cell.get("n"):
            continue
        flags: list[str] = []
        if cell["rr"] < 1.0:
            flags.append("INVERTED RR")
        if cell["n"] < min_n:
            flags.append("LOW N")
        rows.append({
            "symbol": sym,
            "close": st["close"],
            "atr_pct": st["atr_pct"],
            "trend": st["trend"],
            "direction": st["direction"],
            "stop_pct": st["stop_pct"],
            "rr": cell["rr"],
            "p_hit": round(cell["target_first"] / cell["n"] * 100, 1),
            "win_rate": cell["win_rate"],
            "ci_low": cell["ci_low"],
            "ci_high": cell["ci_high"],
            "expectancy_pct": cell["expectancy_pct"],
            "verdict": cell["verdict"],
            "risk": risk_tier(st["stop_pct"]),
            "n": cell["n"],
            "flags": flags,
            "returns": st.get("returns"),
            "cells": st["cells"],
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Rank the daily screen's candidates and summarize the horizon question.")
    parser.add_argument("--stats", default=PROJECT_ROOT / "screen_stats_out.json",
                        help="Path to screen_stats_out.json")
    parser.add_argument("--horizon", type=int, default=10, help="Ranking horizon in trading bars: 5/10/20 (default: 10)")
    parser.add_argument("--hit", type=float, default=4.0, help="Ranking target %% (default: 4.0)")
    parser.add_argument("--top", type=int, default=20, help="How many candidates to emit (default: 20)")
    parser.add_argument("--min-n", type=int, default=30, help="Minimum backtest sample for a row to be trusted (default: 30)")
    parser.add_argument("--sort", choices=["hit", "expectancy", "edge"], default="hit",
                        help="Rank sort key (default: hit = target-first rate)")
    parser.add_argument("--long-only", action="store_true", help="Only BUY setups (skip SELL)")
    args = parser.parse_args()

    try:
        stats = load_json(args.stats)
    except FileNotFoundError:
        emit_json({"error": f"Stats file not found: {args.stats} (run scripts/screen_stats.py first)"})
        return 1
    if "error" in stats:
        emit_json(stats)
        return 1

    symbols: dict[str, Any] = stats.get("symbols", {})
    rows = build_rows(symbols, args.horizon, args.hit, args.min_n, args.long_only)
    if args.sort == "expectancy":
        rows.sort(key=lambda r: r["expectancy_pct"], reverse=True)
    elif args.sort == "edge":
        rows.sort(key=lambda r: (VERDICT_RANK.get(r["verdict"], 9), -r["expectancy_pct"]))
    else:
        rows.sort(key=lambda r: r["p_hit"], reverse=True)

    summary = summarize(symbols, stats.get("params", {}))
    payload = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "params": {
            "horizon": args.horizon,
            "hit": args.hit,
            "top": args.top,
            "min_n": args.min_n,
            "sort": args.sort,
            "long_only": args.long_only,
            "stop_atr": stats.get("params", {}).get("stop_atr"),
            "cost_rate": stats.get("params", {}).get("cost_rate"),
        },
        "summary": summary,
        "return_pool": return_pool(symbols, args.horizon, args.hit),
        "note": (
            "Ranking is a filter, not an alpha claim: per-stock hit rates have ~zero "
            "out-of-sample persistence. Use this list to select candidates for the "
            "sentiment overlay (prompt_daily.md), and let screen_record.py measure "
            "whether the picks actually beat the list."
        ),
        "top": rows[:args.top],
        "total_ranked": len(rows),
        "scanned": stats.get("scanned", 0),
        "skipped": stats.get("skipped", []),
        "shortable": stats.get("shortable", []),
    }
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
