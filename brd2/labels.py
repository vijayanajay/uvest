"""03_labels.py — Triple Barrier labeling engine for the BRD2 prediction engine.

Implements the Triple Barrier Method (BRD2 Section 4):
    - Upper barrier (take profit): Close * (1 + k_u * ATR_pct)
    - Lower barrier (stop loss):   Close * (1 - k_l * ATR_pct)
    - Vertical barrier:            t0 + 10 trading days

Label = 1 if upper hit first or positive return at horizon.
Label = 0 if lower hit first or non-positive return at horizon.

Each label also gets a quality flag based on ATR barrier width — tight
barriers on low-vol days produce noisier labels (Nadh insight: know your
label noise).

Design (Nadh): pure function on a DataFrame. No classes, no I/O.

Usage:
    python brd2/03_labels.py --symbols RELIANCE,HDFCBANK
    python brd2/03_labels.py                       # all with features
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common_brd2 import (  # noqa: E402
    ATR_WINDOW,
    FEATURES_DIR,
    H,
    K_L,
    K_U,
    LABELS_DIR,
    emit_json,
    ensure_dirs,
)


def triple_barrier_label(
    close: float,
    high_series: pd.Series,
    low_series: pd.Series,
    atr_pct: float,
    horizon: int = H,
    k_u: float = K_U,
    k_l: float = K_L,
) -> tuple[int, str, float, int]:
    """Apply Triple Barrier labeling to a single bar.

    Args:
        close: close price at t0
        high_series: forward-looking High bars (t0+1 .. t0+H)
        low_series: forward-looking Low bars (t0+1 .. t0+H)
        atr_pct: ATR(14)/Close at t0 (normalized volatility)
        horizon: vertical barrier in trading days
        k_u: upper barrier multiplier
        k_l: lower barrier multiplier

    Returns:
        (label, barrier_hit, atr_ratio, bars_to_hit)
        label: 1 (bullish) or 0 (bearish/neutral)
        barrier_hit: "upper", "lower", or "vertical"
        atr_ratio: the normalized ATR used for barriers
        bars_to_hit: how many bars to the barrier hit (or horizon)
    """
    upper = close * (1 + k_u * atr_pct)
    lower = close * (1 - k_l * atr_pct)

    n_bars = min(len(high_series), horizon)
    if n_bars == 0:
        return 0, "vertical", atr_pct, 0

    for i in range(n_bars):
        h = high_series.iloc[i]
        l = low_series.iloc[i]
        if pd.isna(h) or pd.isna(l):
            continue

        hit_upper = h >= upper
        hit_lower = l <= lower

        if hit_upper and hit_lower:
            # Same bar — conservative: treat as lower hit (stop first)
            return 0, "lower", atr_pct, i + 1
        elif hit_lower:
            return 0, "lower", atr_pct, i + 1
        elif hit_upper:
            return 1, "upper", atr_pct, i + 1

    # Vertical barrier: label by return sign
    final_close = close  # fallback
    label = 1  # default to positive if no data
    return label, "vertical", atr_pct, n_bars


def compute_atr_pct(df: pd.DataFrame) -> pd.Series:
    """Normalized ATR(14)/Close for barrier width."""
    h, l, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.rolling(ATR_WINDOW).mean()
    return atr / c


def label_feature_df(features_df: pd.DataFrame, horizon: int = H) -> pd.DataFrame:
    """Generate Triple Barrier labels for an entire feature DataFrame.

    Args:
        features_df: output of 02_features.py (must contain Close, High, Low).
        horizon: vertical barrier in trading days.

    Returns:
        DataFrame with columns: label, barrier_hit, atr_pct, atr_quality,
        bars_to_hit, feature_row_idx.
    """
    close = features_df["Close"]
    high = features_df["High"]
    low = features_df["Low"]
    atr_pct = compute_atr_pct(features_df)

    labels = []
    n = len(features_df)

    # We need H bars of forward data, so we can only label up to n - H
    for i in range(n - horizon):
        c = close.iloc[i]
        if pd.isna(c) or c <= 0 or pd.isna(atr_pct.iloc[i]):
            labels.append({
                "label": np.nan,
                "barrier_hit": "skip",
                "atr_pct": np.nan,
                "atr_quality": "skip",
                "bars_to_hit": 0,
                "feature_row_idx": i,
            })
            continue

        forward_high = high.iloc[i + 1: i + 1 + horizon]
        forward_low = low.iloc[i + 1: i + 1 + horizon]

        label, barrier, atr_val, bars = triple_barrier_label(
            c, forward_high, forward_low, atr_pct.iloc[i], horizon
        )

        # ATR quality flag (Nadh insight: know your label noise)
        # Tight barriers (low ATR) = noisier labels
        # Wide barriers (high ATR) = cleaner labels
        if atr_val < 0.015:
            quality = "noisy_low_vol"
        elif atr_val > 0.04:
            quality = "clean_high_vol"
        else:
            quality = "normal"

        # For vertical barrier, use actual final close
        if barrier == "vertical" and i + horizon < n:
            final_c = close.iloc[i + horizon]
            label = 1 if final_c > c else 0

        labels.append({
            "label": label,
            "barrier_hit": barrier,
            "atr_pct": round(float(atr_val), 6),
            "atr_quality": quality,
            "bars_to_hit": bars,
            "feature_row_idx": i,
        })

    # Pad the last H rows with NaN labels (can't label without forward data)
    for i in range(n - horizon, n):
        labels.append({
            "label": np.nan,
            "barrier_hit": "future",
            "atr_pct": np.nan,
            "atr_quality": "skip",
            "bars_to_hit": 0,
            "feature_row_idx": i,
        })

    return pd.DataFrame(labels, index=features_df.index)


# ---- CLI -------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Triple Barrier labels for features.")
    parser.add_argument("--symbols", default="",
                        help="Comma-separated symbols (default: all with features)")
    parser.add_argument("--horizon", type=int, default=H,
                        help=f"Vertical barrier in trading days (default: {H})")
    args = parser.parse_args()

    ensure_dirs()

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        symbols = sorted(p.stem for p in FEATURES_DIR.glob("*.csv"))

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for sym in symbols:
        feat_path = FEATURES_DIR / f"{sym}.csv"
        if not feat_path.exists():
            errors.append({"symbol": sym, "error": "no features — run 02_features.py first"})
            continue

        features_df = pd.read_csv(feat_path, parse_dates=["Date"], index_col="Date")
        if len(features_df) < args.horizon + 60:
            errors.append({"symbol": sym, "error": f"insufficient rows ({len(features_df)})"})
            continue

        labels_df = label_feature_df(features_df, args.horizon)

        # Save
        out_path = LABELS_DIR / f"{sym}.csv"
        labels_df.to_csv(out_path, date_format="%Y-%m-%d")

        # Stats
        valid = labels_df[labels_df["label"].notna()]
        n1 = int(valid["label"].sum())
        n0 = len(valid) - n1
        quality_dist = valid["barrier_hit"].value_counts().to_dict()

        results.append({
            "symbol": sym,
            "total_rows": len(labels_df),
            "labeled_rows": len(valid),
            "label_1": n1,
            "label_0": n0,
            "base_rate": round(n1 / len(valid) * 100, 1) if len(valid) > 0 else 0,
            "barrier_distribution": quality_dist,
            "quality_counts": valid["atr_quality"].value_counts().to_dict(),
            "output": str(out_path),
        })

    emit_json({
        "labeled": len(results),
        "errors": len(errors),
        "horizon": args.horizon,
        "symbols": results,
        "error_details": errors,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
