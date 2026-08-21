"""daily_inference.py — Post-market scoring of the full universe.

Runs the ML pipeline on every stock that has a trained model, applies
isotonic calibration, filters by P_calib >= 0.70, and outputs a ranked
watchlist JSON.

Design (Nadh): thin orchestrator that calls existing functions. No new
frameworks, no new abstractions. Just reads features, loads models,
predicts, calibrates, and writes a JSON.

Usage:
    python brd2/daily_inference.py
    python brd2/daily_inference.py --threshold 0.65
    python brd2/daily_inference.py --top 20
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common_brd2 import (  # noqa: E402
    FEATURES_DIR,
    MODELS_DIR,
    RESULTS_DIR,
    STAGE1_FEATURES,
    emit_json,
    ensure_dirs,
    load_universe,
    resolve_ohlcv_path,
    wilson_ci,
)
from features import build_features  # noqa: E402
from train import load_nifty_regime  # noqa: E402


def load_calibrator(symbol: str) -> tuple[list[float], list[float]] | None:
    """Load isotonic calibration thresholds for a symbol.

    Returns (x_thresholds, y_thresholds) or None if not found.
    """
    iso_path = MODELS_DIR / f"{symbol}_iso.json"
    if not iso_path.exists():
        return None
    try:
        with open(iso_path, "r") as f:
            data = json.load(f)
        return data["x_thresholds"], data["y_thresholds"]
    except Exception:
        return None


def calibrate(raw_prob: float, x_thresholds: list[float],
              y_thresholds: list[float]) -> float:
    """Apply isotonic calibration to a raw probability.

    Linear interpolation between the stored thresholds.
    Nadh: simple, no sklearn dependency at inference time.
    """
    if not x_thresholds or not y_thresholds:
        return raw_prob
    # Clip to training range
    if raw_prob <= x_thresholds[0]:
        return y_thresholds[0]
    if raw_prob >= x_thresholds[-1]:
        return y_thresholds[-1]
    # Find the bracket and interpolate
    for i in range(len(x_thresholds) - 1):
        if x_thresholds[i] <= raw_prob <= x_thresholds[i + 1]:
            frac = ((raw_prob - x_thresholds[i]) /
                    (x_thresholds[i + 1] - x_thresholds[i]))
            return y_thresholds[i] + frac * (y_thresholds[i + 1] - y_thresholds[i])
    return raw_prob


def score_stock(symbol: str, features_df: pd.DataFrame,
                regime: dict[str, bool] | None) -> dict[str, Any] | None:
    """Score a single stock using its trained model.

    Returns a dict with symbol, raw_prob, calibrated_prob, regime, top features.
    Returns None if the model doesn't exist or scoring fails.
    """
    import lightgbm as lgb

    model_path = MODELS_DIR / f"{symbol}_lgbm.txt"
    if not model_path.exists():
        return None

    try:
        model = lgb.Booster(model_file=str(model_path))
    except Exception:
        return None

    # Get the latest valid feature row
    valid = features_df[STAGE1_FEATURES].dropna()
    if valid.empty:
        return None

    latest = valid.iloc[[-1]]  # last row as DataFrame
    latest_date = valid.index[-1].strftime("%Y-%m-%d")

    # Raw prediction
    raw_prob = float(model.predict(latest)[0])

    # Calibrate
    cal_data = load_calibrator(symbol)
    if cal_data:
        cal_prob = calibrate(raw_prob, cal_data[0], cal_data[1])
    else:
        cal_prob = raw_prob

    # Regime
    regime_state = None
    if regime is not None:
        regime_state = regime.get(latest_date)

    # Feature importances from the model
    importance = dict(zip(STAGE1_FEATURES, model.feature_importance(importance_type="gain")))
    top5 = sorted(importance.items(), key=lambda x: -x[1])[:5]

    return {
        "symbol": symbol,
        "date": latest_date,
        "raw_prob": round(raw_prob, 4),
        "calibrated_prob": round(cal_prob, 4),
        "regime": "bull" if regime_state else ("bear" if regime_state is False else "unknown"),
        "top_features": [{"feature": f, "importance": int(imp)} for f, imp in top5],
        "close": float(features_df["Close"].iloc[-1]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily post-market ML scoring.")
    parser.add_argument("--threshold", type=float, default=0.70,
                        help="Minimum calibrated probability (default: 0.70)")
    parser.add_argument("--top", type=int, default=20,
                        help="Max watchlist entries (default: 20)")
    args = parser.parse_args()

    ensure_dirs()
    regime = load_nifty_regime()

    # Find all stocks with both features and a trained model
    model_symbols = {p.stem.replace("_lgbm", "") for p in MODELS_DIR.glob("*_lgbm.txt")}
    feature_symbols = {p.stem for p in FEATURES_DIR.glob("*.csv")}
    scored_symbols = sorted(model_symbols & feature_symbols)

    if not scored_symbols:
        emit_json({"error": "No stocks with both features and trained models found.",
                    "models": sorted(model_symbols), "features": sorted(feature_symbols)})
        return 1

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for sym in scored_symbols:
        try:
            feat_path = FEATURES_DIR / f"{sym}.csv"
            feat_df = pd.read_csv(feat_path, parse_dates=["Date"], index_col="Date")
            score = score_stock(sym, feat_df, regime)
            if score is None:
                errors.append({"symbol": sym, "error": "scoring failed"})
                continue
            results.append(score)
        except Exception as e:
            errors.append({"symbol": sym, "error": str(e)})

    # Sort by calibrated probability (highest first)
    results.sort(key=lambda x: -x["calibrated_prob"])

    # Filter high-confidence
    high_conf = [r for r in results if r["calibrated_prob"] >= args.threshold]

    # Wilson CI for the high-confidence set
    n_hc = len(high_conf)
    # We need actual outcomes to compute CI — at inference time we don't have them.
    # So we report the count and leave CI for the post-resolution learning loop.

    watchlist = high_conf[:args.top]

    # Regime summary
    regime_counts = {"bull": 0, "bear": 0, "unknown": 0}
    for r in results:
        regime_counts[r["regime"]] = regime_counts.get(r["regime"], 0) + 1

    payload = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "threshold": args.threshold,
        "universe_scored": len(results),
        "high_confidence_count": n_hc,
        "watchlist_count": len(watchlist),
        "regime_distribution": regime_counts,
        "regime_note": ("Bull = NIFTY above 200-DMA, Bear = below. "
                        "Regime is a risk gate, not an alpha claim."),
        "watchlist": watchlist,
        "all_scored": results,
        "errors": errors,
        "model_count": len(model_symbols),
        "feature_count": len(feature_symbols),
    }

    # Save to results
    out_path = RESULTS_DIR / "daily_inference.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Print summary
    print(f"Daily Inference — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Scored: {len(results)} stocks | High confidence (P>={args.threshold}): {n_hc}")
    print(f"Regime: {regime_counts}")
    print()
    if watchlist:
        print(f"{'Symbol':15s} {'P_cal':>6s} {'Regime':>7s} {'Close':>10s}  Top Features")
        print("-" * 70)
        for w in watchlist:
            feats = ", ".join(f["feature"] for f in w["top_features"][:3])
            print(f"{w['symbol']:15s} {w['calibrated_prob']:6.3f} {w['regime']:>7s} "
                  f"{w['close']:10.2f}  {feats}")
    else:
        print("No high-confidence signals this session.")

    print(f"\nReport: {out_path}")
    emit_json(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
