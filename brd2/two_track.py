"""two_track.py — Compare ML pipeline vs stats pipeline on the same stocks.

The ultimate honesty check: does the LightGBM model beat the simple
similar-setup backtest? If not, the ML is adding complexity without edge.

Track A (ML): LightGBM + purged CV + isotonic calibration → win rate at P>=0.70
Track B (Stats): Similar-setup backtest (ATR-based stop/target) → win rate at EDGE CONFIRMED

Both tracks use the same stocks, same time period, same transaction costs.
The comparison is head-to-head, not cherry-picked.

Usage:
    python brd2/two_track.py --symbols RELIANCE,HDFCBANK
    python brd2/two_track.py --all  # all stocks with features
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common_brd2 import (  # noqa: E402
    COST_RATE_FUT,
    FEATURES_DIR,
    RESULTS_DIR,
    STAGE1_FEATURES,
    emit_json,
    ensure_dirs,
    wilson_ci,
)
from purged_cv import purged_walk_forward_split  # noqa: E402
from train import load_data, load_nifty_regime  # noqa: E402

import lightgbm as lgb  # noqa: E402
from sklearn.isotonic import IsotonicRegression  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402


def track_a_ml(symbol: str, X: pd.DataFrame, y: pd.Series,
               dates: pd.DatetimeIndex) -> dict[str, Any]:
    """Track A: LightGBM purged CV with isotonic calibration.

    Returns win rate at P>=0.70 and overall AUC.
    """
    from common_brd2 import LGBM_PARAMS

    n = len(X)
    folds = purged_walk_forward_split(n)
    if not folds:
        return {"error": f"insufficient data for {symbol}", "n_samples": n}

    oof_probs = []
    oof_actuals = []

    for train_idx, test_idx, _ in folds:
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

        model = lgb.LGBMClassifier(**LGBM_PARAMS)
        model.fit(X_train, y_train)
        y_prob = model.predict_proba(X_test)[:, 1]

        oof_probs.extend(y_prob.tolist())
        oof_actuals.extend(y_test.tolist())

    oof_arr = np.array(oof_probs)
    actual_arr = np.array(oof_actuals)

    # Isotonic calibration
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(oof_arr, actual_arr)
    cal_probs = iso.predict(oof_arr)

    # Metrics
    auc = roc_auc_score(actual_arr, oof_arr) if len(set(actual_arr)) > 1 else 0.5

    # High-confidence (P >= 0.70)
    hc_mask = cal_probs >= 0.70
    n_hc = int(hc_mask.sum())
    wins_hc = int(actual_arr[hc_mask].sum()) if n_hc > 0 else 0
    wr_hc = wins_hc / n_hc * 100 if n_hc > 0 else 0
    ci = wilson_ci(wins_hc, n_hc) if n_hc > 0 else (0, 0)

    # Overall
    wins_total = int(actual_arr.sum())
    wr_total = wins_total / len(actual_arr) * 100

    return {
        "track": "ML (LightGBM + Purged CV + Isotonic)",
        "n_samples": n,
        "n_oof": len(oof_arr),
        "auc": round(auc, 4),
        "overall_win_rate": round(wr_total, 1),
        "high_conf_threshold": 0.70,
        "high_conf_n": n_hc,
        "high_conf_wins": wins_hc,
        "high_conf_win_rate": round(wr_hc, 1),
        "ci_low": round(ci[0] * 100, 1),
        "ci_high": round(ci[1] * 100, 1),
    }


def track_b_stats(symbol: str, cache_path: Path,
                  stop_atr: float = 1.5, hit_pct: float = 0.04,
                  horizon: int = 10) -> dict[str, Any]:
    """Track B: Similar-setup backtest (ATR-based stop/target).

    Mirrors the logic in scripts/screen_stats.py but runs standalone
    so the comparison is self-contained.
    """
    try:
        df = pd.read_csv(cache_path, parse_dates=["Date"], index_col="Date")
    except Exception as e:
        return {"error": f"failed to load {cache_path}: {e}"}

    if len(df) < 60 + horizon:
        return {"error": f"insufficient data: {len(df)} bars"}

    # Indicators
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    opens = df["Open"].tolist()
    highs = df["High"].tolist()
    lows = df["Low"].tolist()
    closes = df["Close"].tolist()
    n = len(df)

    # Current state
    close_now = float(closes[-1])
    ema_now = float(ema50.iloc[-1])
    atr_now = float(atr.iloc[-1])
    if not atr_now or atr_now <= 0:
        return {"error": "ATR is zero or NaN"}
    atr_pct = atr_now / close_now
    bullish = close_now > ema_now
    direction = "BUY" if bullish else "SELL"

    # Replay similar setups
    wins = 0
    total = 0
    for d in range(50, n - horizon):
        atr_d = atr.iloc[d]
        if not atr_d or atr_d <= 0:
            continue
        ema_d = float(ema50.iloc[d])
        close_d = float(closes[d])
        if direction == "BUY" and close_d <= ema_d:
            continue
        if direction == "SELL" and close_d >= ema_d:
            continue

        entry = float(opens[d + 1])
        if entry != entry or entry <= 0:
            continue
        risk = stop_atr * float(atr_d)
        stop = entry - risk if direction == "BUY" else entry + risk
        target = entry * (1 + hit_pct) if direction == "BUY" else entry * (1 - hit_pct)

        outcome = None
        for j in range(d + 1, d + horizon + 1):
            lo, hi = lows[j], highs[j]
            if lo != lo or hi != hi:
                continue
            if direction == "BUY":
                if lo <= stop:
                    outcome = False
                    break
                if hi >= target:
                    outcome = True
                    break
            else:
                if hi >= stop:
                    outcome = False
                    break
                if lo <= target:
                    outcome = True
                    break

        if outcome is None:
            final = float(closes[d + horizon])
            outcome = final > entry if direction == "BUY" else final < entry

        total += 1
        if outcome:
            wins += 1

    if total == 0:
        return {"error": "no similar setups found"}

    wr = wins / total * 100
    ci = wilson_ci(wins, total)

    # Break-even win rate
    rr = (hit_pct - COST_RATE_FUT) / (stop_atr * atr_pct + COST_RATE_FUT)
    p_star = 1.0 / (1.0 + rr) * 100 if rr > 0 else 100.0

    return {
        "track": "Stats (ATR-based similar-setup backtest)",
        "n_setups": total,
        "wins": wins,
        "win_rate": round(wr, 1),
        "ci_low": round(ci[0] * 100, 1),
        "ci_high": round(ci[1] * 100, 1),
        "break_even_win_rate": round(p_star, 1),
        "rr": round(rr, 2),
        "direction": direction,
        "atr_pct": round(atr_pct * 100, 2),
    }


def compare(track_a: dict, track_b: dict) -> dict[str, Any]:
    """Compare the two tracks and produce a verdict."""
    if "error" in track_a or "error" in track_b:
        return {
            "verdict": "INCOMPLETE",
            "reason_a": track_a.get("error"),
            "reason_b": track_b.get("error"),
        }

    ml_wr = track_a["high_conf_win_rate"]
    ml_n = track_a["high_conf_n"]
    stats_wr = track_b["win_rate"]
    stats_n = track_b["n_setups"]
    p_star = track_b["break_even_win_rate"]

    # ML edge: does ML win rate exceed break-even?
    ml_edge = ml_wr > p_star if ml_n >= 10 else None
    stats_edge = stats_wr > p_star if stats_n >= 30 else None

    if ml_edge is True and stats_edge is True:
        both_have_edge = True
        ml_better = ml_wr > stats_wr
        verdict = "ML WINS" if ml_better else "STATS WINS"
    elif ml_edge is True:
        verdict = "ML ONLY"
    elif stats_edge is True:
        verdict = "STATS ONLY"
    else:
        verdict = "NEITHER"

    return {
        "verdict": verdict,
        "ml_high_conf_win_rate": ml_wr,
        "ml_n": ml_n,
        "ml_edge": ml_edge,
        "stats_win_rate": stats_wr,
        "stats_n": stats_n,
        "stats_edge": stats_edge,
        "break_even_win_rate": p_star,
        "delta": round(ml_wr - stats_wr, 1) if ml_n >= 10 and stats_n >= 10 else None,
        "note": ("ML WINS = ML beats stats AND both beat break-even. "
                 "Delta > 0 means ML adds value over the simpler approach."),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Two-track validation: ML vs Stats.")
    parser.add_argument("--symbols", default="",
                        help="Comma-separated symbols (default: all with features)")
    parser.add_argument("--stop-atr", type=float, default=1.5,
                        help="Stats track ATR multiplier (default: 1.5)")
    parser.add_argument("--hit-pct", type=float, default=0.04,
                        help="Stats track target %% (default: 4%%)")
    args = parser.parse_args()

    ensure_dirs()

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        symbols = sorted(p.stem for p in FEATURES_DIR.glob("*.csv"))

    results: list[dict[str, Any]] = []

    for sym in symbols:
        print(f"\n{'='*50}")
        print(f"  {sym}")
        print(f"{'='*50}")

        t0 = time.time()

        # Track A: ML
        try:
            X, y, dates = load_data(sym)
            if len(X) >= 250:
                track_a = track_a_ml(sym, X, y, dates)
            else:
                track_a = {"error": f"only {len(X)} rows, need >= 250"}
        except Exception as e:
            track_a = {"error": str(e)}

        # Track B: Stats
        from common_brd2 import resolve_ohlcv_path
        cache_path = resolve_ohlcv_path(sym)
        if cache_path is not None:
            track_b = track_b_stats(sym, cache_path, args.stop_atr, args.hit_pct)
        else:
            track_b = {"error": "no data cache found"}

        # Compare
        comparison = compare(track_a, track_b)

        elapsed = time.time() - t0

        result = {
            "symbol": sym,
            "track_a": track_a,
            "track_b": track_b,
            "comparison": comparison,
            "elapsed_seconds": round(elapsed, 1),
        }
        results.append(result)

        # Print summary
        ml_wr = track_a.get("high_conf_win_rate", "N/A")
        stats_wr = track_b.get("win_rate", "N/A")
        print(f"  ML:  {ml_wr}% (n={track_a.get('high_conf_n', 0)})")
        print(f"  Stats: {stats_wr}% (n={track_b.get('n_setups', 0)})")
        print(f"  Verdict: {comparison['verdict']}")

    # Consolidated
    valid = [r for r in results if "error" not in r.get("track_a", {}) and
             "error" not in r.get("track_b", {})]
    ml_wins = sum(1 for r in valid if r["comparison"]["verdict"] == "ML WINS")
    stats_wins = sum(1 for r in valid if r["comparison"]["verdict"] == "STATS WINS")
    both = sum(1 for r in valid if "BOTH" in r["comparison"]["verdict"] or
               r["comparison"]["verdict"] in ("ML WINS", "STATS WINS"))

    summary = {
        "total_stocks": len(results),
        "valid_comparisons": len(valid),
        "ml_wins": ml_wins,
        "stats_wins": stats_wins,
        "neither": sum(1 for r in valid if r["comparison"]["verdict"] == "NEITHER"),
        "ml_only": sum(1 for r in valid if r["comparison"]["verdict"] == "ML ONLY"),
        "stats_only": sum(1 for r in valid if r["comparison"]["verdict"] == "STATS ONLY"),
        "note": ("If ML doesn't beat stats on most stocks, the ML is adding "
                 "complexity without edge. Consider whether the extra features "
                 "and calibration are worth the engineering cost."),
    }

    payload = {"summary": summary, "results": results}

    out_path = RESULTS_DIR / "two_track_report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\n{'='*50}")
    print("  TWO-TRACK SUMMARY")
    print(f"{'='*50}")
    print(f"  Stocks compared: {summary['valid_comparisons']}/{summary['total_stocks']}")
    print(f"  ML wins: {summary['ml_wins']} | Stats wins: {summary['stats_wins']} | "
          f"Neither: {summary['neither']}")
    print(f"\nReport: {out_path}")

    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
