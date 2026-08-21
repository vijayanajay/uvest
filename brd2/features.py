"""02_features.py — Feature engineering pipeline for the BRD2 prediction engine.

Reads cached daily OHLCV CSVs and computes the 12 Stage 1 features:

    1.  Log_Ret_1D       — 1-day log return
    2.  Log_Ret_5D       — 5-day log return
    3.  Log_Ret_10D      — 10-day log return
    4.  Normalized_ATR_14 — ATR(14) / Close (vol-normalized range)
    5.  Parkinson_Vol_10  — Parkinson High-Low volatility estimator (10-day)
    6.  Normalized_RSI_14 — (RSI(14) - 50) / 100  → range [-0.5, 0.5]
    7.  MACD_Hist_Ratio   — (MACD - Signal) / Close
    8.  Bollinger_PctB    — (Close - Lower) / (Upper - Lower) → [0, 1]
    9.  Volume_ZScore_20  — Z-score of volume over 20 days
    10. Rel_Strength_NIFTY — Stock 10d return minus Nifty 50 10d return
    11. EMA_50_Slope      — Rate of change of 50-EMA (momentum of trend)
    12. Close_vs_EMA50    — (Close - EMA50) / EMA50 (trend position)

Design (Nadh): every indicator is a pure function on a DataFrame. No classes,
no state, no I/O. You can test each one in a notebook cell.

Usage:
    python brd2/02_features.py --symbols RELIANCE,HDFCBANK
    python brd2/02_features.py                       # all cached symbols
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
    CACHE_DIR,
    DATA_CACHE_DIR,
    FEATURES_DIR,
    STAGE1_FEATURES,
    emit_json,
    ensure_dirs,
    resolve_ohlcv_path,
)


# ---- Individual indicator functions (pure, testable) -----------------------

def log_returns(close: pd.Series, window: int) -> pd.Series:
    """Log return over `window` days."""
    return np.log(close / close.shift(window))


def atr_14(df: pd.DataFrame) -> pd.Series:
    """14-day Average True Range."""
    h, l, c = df["High"], df["Low"], df["Close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(14).mean()


def parkinson_vol(df: pd.DataFrame, window: int = 10) -> pd.Series:
    """Parkinson volatility estimator: sqrt(1/(4*n*ln2) * sum(ln(H/L)^2))."""
    log_hl = np.log(df["High"] / df["Low"])
    return np.sqrt((log_hl ** 2).rolling(window).sum() / (window * 4 * np.log(2)))


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, histogram."""
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal
    return macd_line, signal, hist


def bollinger_bands(close: pd.Series, window: int = 20, num_std: float = 2.0):
    """Bollinger Bands: (upper, lower, %B)."""
    mid = close.rolling(window).mean()
    std = close.rolling(window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    pctb = (close - lower) / (upper - lower).replace(0, np.nan)
    return upper, lower, pctb


def volume_zscore(volume: pd.Series, window: int = 20) -> pd.Series:
    """Z-score of volume over `window` days."""
    mu = volume.rolling(window).mean()
    sigma = volume.rolling(window).std()
    return (volume - mu) / sigma.replace(0, np.nan)


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=span, adjust=False).mean()


# ---- Fractional Differentiation (López de Prado, AFML Ch. 5) ---------------

def frac_diff_weights(d: float, threshold: float = 1e-5) -> list[float]:
    """Compute FracDiff weights: w_0=1, w_k = w_{k-1} * (d - k + 1) / k.

    Truncates when |w_k| < threshold to keep the window finite.
    The d parameter controls the memory-stationarity tradeoff:
        d=0  → original series (non-stationary, full memory)
        d=0.5 → fracdiff (stationary, some memory)
        d=1  → first difference (stationary, no memory)
    """
    weights = [1.0]
    k = 1
    while True:
        w_k = weights[-1] * (d - k + 1) / k
        if abs(w_k) < threshold:
            break
        weights.append(w_k)
        k += 1
    return weights


def frac_diff(close: pd.Series, d: float = 0.4, threshold: float = 1e-5) -> pd.Series:
    """Fractional differentiation of a price series.

    Applies the rolling fracdiff window so every output row uses only
    past data (no lookahead). Returns a series aligned with the input.
    """
    weights = frac_diff_weights(d, threshold)
    window = len(weights)
    # Flip weights for convolution (w[0] multiplies the most recent)
    w = np.array(weights[::-1])
    vals = close.values
    result = np.full(len(vals), np.nan)
    for i in range(window - 1, len(vals)):
        window_vals = vals[i - window + 1: i + 1]
        if np.any(np.isnan(window_vals)):
            continue
        result[i] = np.dot(w, window_vals)
    return pd.Series(result, index=close.index)


# ---- Aggregate feature builder ---------------------------------------------

def build_features(df: pd.DataFrame, nifty_close: pd.Series | None = None) -> pd.DataFrame:
    """Compute all 12 Stage 1 features from daily OHLCV bars.

    Args:
        df: daily OHLCV DataFrame with columns Open, High, Low, Close, Volume.
        nifty_close: optional NIFTY 50 close series (aligned by date) for
                     relative strength. If None, Rel_Strength_NIFTY = 0.

    Returns:
        DataFrame with Date index and 12 feature columns (plus Close for label use).
    """
    close = df["Close"]
    features = pd.DataFrame(index=df.index)

    # 1-3: Log returns
    features["Log_Ret_1D"] = log_returns(close, 1)
    features["Log_Ret_5D"] = log_returns(close, 5)
    features["Log_Ret_10D"] = log_returns(close, 10)

    # 4: Normalized ATR
    atr = atr_14(df)
    features["Normalized_ATR_14"] = atr / close

    # 5: Parkinson volatility
    features["Parkinson_Vol_10"] = parkinson_vol(df, 10)

    # 6: Normalized RSI
    features["Normalized_RSI_14"] = (rsi(close, 14) - 50) / 100

    # 7: MACD histogram ratio
    _, _, hist = macd(close)
    features["MACD_Hist_Ratio"] = hist / close

    # 8: Bollinger %B
    _, _, pctb = bollinger_bands(close)
    features["Bollinger_PctB"] = pctb

    # 9: Volume Z-score
    features["Volume_ZScore_20"] = volume_zscore(df["Volume"], 20)

    # 10: Relative strength vs Nifty
    if nifty_close is not None:
        # Align by date — forward-fill Nifty on trading days
        nifty_reindexed = nifty_close.reindex(df.index, method="ffill")
        stock_ret10 = log_returns(close, 10)
        nifty_ret10 = log_returns(nifty_reindexed, 10)
        features["Rel_Strength_NIFTY"] = stock_ret10 - nifty_ret10
    else:
        features["Rel_Strength_NIFTY"] = 0.0

    # 11: EMA 50 slope (rate of change of the 50-EMA, normalized)
    ema50 = ema(close, 50)
    features["EMA_50_Slope"] = ema50.pct_change(5)

    # 12: Close vs EMA50 (trend position)
    features["Close_vs_EMA50"] = (close - ema50) / ema50

    # 13: Fractional Differentiation (Stage 2)
    # d=0.4 with threshold=1e-4 → 282-weight window
    # Preserves memory while achieving stationarity; fits both 500-bar
    # and 2500-bar datasets.
    features["FracDiff_Close"] = frac_diff(close, d=0.4, threshold=1e-4)

    # Carry forward Close for label generation (not a feature)
    features["Close"] = close
    features["High"] = df["High"]
    features["Low"] = df["Low"]

    return features


# ---- CLI -------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Compute Stage 1 features for cached OHLCV data.")
    parser.add_argument("--symbols", default="",
                        help="Comma-separated symbols (default: all cached)")
    args = parser.parse_args()

    ensure_dirs()

    # Load Nifty for relative strength — prefer data_cache/NIFTY50.csv
    nifty_close = None
    for nifty_candidate in [DATA_CACHE_DIR / "NIFTY50.csv", CACHE_DIR / "^NSEI.csv"]:
        if nifty_candidate.exists():
            try:
                nifty_df = pd.read_csv(nifty_candidate, parse_dates=["Date"], index_col="Date")
                nifty_close = nifty_df["Close"]
                break
            except Exception:
                continue

    # Discover cached symbols
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    else:
        symbols = sorted(p.stem for p in CACHE_DIR.glob("*.csv")
                         if p.stem != "^NSEI")

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for sym in symbols:
        csv_path = resolve_ohlcv_path(sym)
        if csv_path is None:
            errors.append({"symbol": sym, "error": "not cached — run fetch_data.py first"})
            continue

        df = pd.read_csv(csv_path, parse_dates=["Date"], index_col="Date")
        if len(df) < 60:
            errors.append({"symbol": sym, "error": f"only {len(df)} bars, need >= 60"})
            continue

        feat = build_features(df, nifty_close)

        # Save features CSV
        out_path = FEATURES_DIR / f"{sym}.csv"
        feat.to_csv(out_path, date_format="%Y-%m-%d")

        # Stats
        valid = feat[STAGE1_FEATURES].dropna()
        results.append({
            "symbol": sym,
            "total_rows": len(feat),
            "valid_rows": len(valid),
            "date_range": f"{feat.index[0].strftime('%Y-%m-%d')} to {feat.index[-1].strftime('%Y-%m-%d')}",
            "output": str(out_path),
        })

    emit_json({
        "computed": len(results),
        "errors": len(errors),
        "features": STAGE1_FEATURES,
        "symbols": results,
        "error_details": errors,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
