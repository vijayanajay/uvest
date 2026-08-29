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

MOMENTUM_TIER = {
    range(70, 101): "STRONG",
    range(50, 70): "MODERATE",
    range(0, 50): "WEAK",
}


def momentum_tier(score: float) -> str:
    for r, label in MOMENTUM_TIER.items():
        if score in r:
            return label
    return "WEAK"


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
    the full return distribution at every horizon is listed; the pooled
    line uses exact weighted means / weighted % positive.
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
            "sector": st.get("sector", "OTHERS"),
            "momentum": st.get("momentum", {}),
            "returns": rets,
        })
    # Pool across ALL horizons present in returns (2/3/5/10/20)
    all_hz_keys = sorted({k for s in stocks for k in s["returns"].keys()},
                         key=lambda x: int(x))
    horizons: dict[str, Any] = {}
    for hz_key in all_hz_keys:
        n_w = 0
        wsum = 0.0
        wpos = 0.0
        for s in stocks:
            r = s["returns"].get(hz_key)
            if r and r.get("n"):
                n_w += r["n"]
                wsum += r["mean"] * r["n"]
                wpos += r["pct_positive"] * r["n"]
        if n_w:
            horizons[hz_key] = {
                "n_windows": n_w,
                "n_stocks": len(stocks),
                "mean": round(wsum / n_w, 2),
                "pct_positive": round(wpos / n_w, 1),
            }
    return {"qualifying_stocks": len(stocks), "horizons": horizons, "stocks": stocks}


def compute_rs_percentiles(symbols: dict[str, Any]) -> dict[str, float]:
    """Compute cross-sectional percentile rank of RS_20 across the universe."""
    rs_vals = [(sym, st.get("rs_20", 0.0)) for sym, st in symbols.items()]
    if not rs_vals:
        return {}
    rs_vals.sort(key=lambda x: x[1])
    n = len(rs_vals)
    return {sym: round((i / (n - 1) if n > 1 else 1.0) * 100.0, 1) for i, (sym, _) in enumerate(rs_vals)}


def compute_sector_rs(symbols: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Compute sector-level average RS and momentum score.

    For each sector, averages the rs_20 and momentum composite_score of
    all stocks in that sector. Returns {sector: {avg_rs_20, avg_momentum,
    n_stocks, direction}}.
    """
    sectors: dict[str, list[dict[str, Any]]] = {}
    for sym, st in symbols.items():
        sector = st.get("sector", "OTHERS")
        sectors.setdefault(sector, []).append(st)
    result: dict[str, dict[str, Any]] = {}
    for sector, stocks in sectors.items():
        n = len(stocks)
        avg_rs = sum(s.get("rs_20", 0.0) for s in stocks) / n
        avg_mom = sum(s.get("momentum", {}).get("composite_score", 50.0) for s in stocks) / n
        n_bull = sum(1 for s in stocks if s.get("trend") == "BULLISH")
        result[sector] = {
            "avg_rs_20": round(avg_rs, 2),
            "avg_momentum": round(avg_mom, 1),
            "n_stocks": n,
            "n_bullish": n_bull,
            "pct_bullish": round(n_bull / n * 100, 1),
            "direction": "BULLISH" if avg_rs > 0 and n_bull / n > 0.5 else "BEARISH",
        }
    return result


def build_rows(symbols: dict[str, Any], horizon: int, hit: float, min_n: int,
               long_only: bool, sector_rs: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    key = f"{horizon}:{hit:.1f}"
    rs_pcts = compute_rs_percentiles(symbols)

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

        rs_20 = st.get("rs_20", 0.0)
        rs_50 = st.get("rs_50", 0.0)
        rs_pct = rs_pcts.get(sym, 50.0)
        if rs_pct < 30.0 or rs_20 < -5.0:
            flags.append("LAGGING RS")

        # Momentum flags
        mom = st.get("momentum", {})
        mom_score = mom.get("composite_score", 50.0)
        mom_tier = momentum_tier(mom_score)
        rsi = mom.get("rsi")
        if rsi is not None and rsi > 70:
            flags.append("RSI OVERBOUGHT")
        if rsi is not None and rsi < 30:
            flags.append("RSI OVERSOLD")
        vol_ratio = mom.get("volume_ratio")
        if vol_ratio is not None and vol_ratio > 2.0:
            flags.append("VOLUME SURGE")

        # Sector alignment flags
        sector = st.get("sector", "OTHERS")
        sector_aligned = True
        if sector_rs and sector in sector_rs:
            sr = sector_rs[sector]
            if sr["direction"] == "BEARISH" and st.get("direction") == "BUY":
                flags.append("SECTOR LAGGING")
                sector_aligned = False

        tb_stats = (st.get("triple_barrier") or {}).get(str(horizon))

        rows.append({
            "symbol": sym,
            "close": st["close"],
            "atr_pct": st["atr_pct"],
            "trend": st["trend"],
            "direction": st["direction"],
            "stop_pct": st["stop_pct"],
            "rs_20": rs_20,
            "rs_50": rs_50,
            "rs_percentile": rs_pct,
            "sector": sector,
            "sector_aligned": sector_aligned,
            "momentum_score": mom_score,
            "momentum_tier": mom_tier,
            "rsi": rsi,
            "volume_ratio": vol_ratio,
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
            "triple_barrier": tb_stats,
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
    parser.add_argument("--sort", choices=["hit", "expectancy", "edge", "momentum"], default="hit",
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
    sector_rs = compute_sector_rs(symbols)
    rows = build_rows(symbols, args.horizon, args.hit, args.min_n, args.long_only, sector_rs)
    if args.sort == "expectancy":
        rows.sort(key=lambda r: r["expectancy_pct"], reverse=True)
    elif args.sort == "edge":
        rows.sort(key=lambda r: (VERDICT_RANK.get(r["verdict"], 9), -r["expectancy_pct"]))
    elif args.sort == "momentum":
        # Composite: 50% momentum_score + 30% p_hit + 20% rs_percentile
        rows.sort(key=lambda r: (
            0.5 * r["momentum_score"] + 0.3 * r["p_hit"] + 0.2 * r["rs_percentile"]
        ), reverse=True)
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
        "sector_rotation": sector_rs,
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
