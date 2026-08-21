"""Shared constants, paths, and helpers for the BRD2 prediction engine.

Design philosophy (Nadh): flat modules, JSON contracts, zero framework magic.
Every script reads from and writes to well-defined paths. No singletons, no
dependency injection, no abstractions for their own sake.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# ---- Paths ----------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
PROJECT_PARENT = PROJECT_ROOT.parent  # repo root
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = PROJECT_ROOT / "cache"
# Unified data source: prefer the shared data_cache/ (150+ stocks, 2y depth)
# over the brd2-local cache (2 stocks, 10y depth). The fallback chain is:
#   data_cache/ > brd2/cache/ > fresh download
DATA_CACHE_DIR = PROJECT_PARENT / "data_cache"
UNIVERSE_FILE = DATA_DIR / "liquid_nse100.csv"
FEATURES_DIR = PROJECT_ROOT / "features"
LABELS_DIR = PROJECT_ROOT / "labels"
MODELS_DIR = PROJECT_ROOT / "models"
RESULTS_DIR = PROJECT_ROOT / "results"
RECS_DB = PROJECT_ROOT / "recs.db"

# ---- Pipeline constants (BRD2 Section 4.1) --------------------------------
ATR_WINDOW = 14
K_U = 1.5          # upper barrier multiplier
K_L = 1.0          # lower barrier multiplier
H = 10             # vertical barrier (trading days)
LOOKBACK = 250     # ~1 year of daily bars for feature warmup
TOTAL_BARS = 2500  # ~10 years of daily data to fetch

# ---- Purged CV (BRD2 Section 5.1) -----------------------------------------
PURGE_WINDOW = 10  # trading days removed before test fold (= H, prevents label leakage)
EMBARGO_WINDOW = 5 # trading days removed after test fold
N_FOLDS = 5

# ---- LightGBM params (BRD2 Section 6.1) -----------------------------------
LGBM_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "boosting_type": "gbdt",
    "n_estimators": 350,
    "learning_rate": 0.012,
    "num_leaves": 16,
    "max_depth": 4,
    "min_child_samples": 25,
    "subsample": 0.75,
    "colsample_bytree": 0.70,
    "reg_alpha": 0.15,
    "reg_lambda": 1.2,
    "random_state": 42,
    "verbose": -1,
}

# ---- Transaction costs (BRD2 Section 8.1, same as common.py) ---------------
COST_RATE_FUT = 0.0009  # ~0.09% round-trip (STT + exchange + brokerage + GST)

# ---- Stage 1 features (12 core technical) ----------------------------------
STAGE1_FEATURES = [
    "Log_Ret_1D",
    "Log_Ret_5D",
    "Log_Ret_10D",
    "Normalized_ATR_14",
    "Parkinson_Vol_10",
    "Normalized_RSI_14",
    "MACD_Hist_Ratio",
    "Bollinger_PctB",
    "Volume_ZScore_20",
    "Rel_Strength_NIFTY",
    "EMA_50_Slope",
    "Close_vs_EMA50",
    "FracDiff_Close",
]


def ensure_dirs() -> None:
    """Create all output directories if they don't exist."""
    for d in (CACHE_DIR, FEATURES_DIR, LABELS_DIR, MODELS_DIR, RESULTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---- Fractional Kelly Sizing (Stage 3, ready to activate) -----------------
# Nadh: define the formula now, activate only after 100+ resolved trades.
# The track record must be stable before Kelly can estimate edge reliably.

def fractional_kelly(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    fraction: float = 0.5,
    min_trades: int = 100,
    n_trades: int = 0,
) -> dict[str, Any]:
    """Fractional Kelly position sizing from accumulated track record.

    Kelly fraction = (edge / odds) * fraction
    where:
        edge = win_rate * avg_win - (1 - win_rate) * avg_loss
        odds = avg_win / avg_loss (payoff ratio)
        fraction = 0.5 (half-Kelly, conservative)

    Returns the Kelly fraction as a percentage of capital, plus diagnostics.
    Returns 0% if insufficient data (n_trades < min_trades).

    Usage (Stage 3, after 100+ resolved trades):
        kelly = fractional_kelly(0.62, 15000, 10000, n_trades=150)
        position_size = capital * kelly["kelly_pct"] / 100
    """
    if n_trades < min_trades:
        return {
            "kelly_pct": 0.0,
            "reason": f"insufficient trades ({n_trades} < {min_trades})",
            "edge": None,
            "odds": None,
            "full_kelly": None,
            "fraction_used": fraction,
        }
    if avg_loss <= 0 or avg_win <= 0:
        return {
            "kelly_pct": 0.0,
            "reason": "invalid avg_win or avg_loss",
            "edge": None, "odds": None, "full_kelly": None,
            "fraction_used": fraction,
        }

    odds = avg_win / avg_loss
    edge = win_rate * avg_win - (1 - win_rate) * avg_loss
    full_kelly = edge / avg_win if avg_win > 0 else 0.0
    kelly = max(0.0, full_kelly * fraction)  # never negative

    return {
        "kelly_pct": round(kelly * 100, 2),
        "edge": round(edge, 2),
        "odds": round(odds, 2),
        "full_kelly_pct": round(full_kelly * 100, 2),
        "fraction_used": fraction,
        "n_trades": n_trades,
        "note": ("Half-Kelly (fraction=0.5) is conservative. "
                  "Activate only after 100+ resolved trades with stable edge.")
    }


def emit_json(data: Any) -> None:
    """Print a dict/list as JSON to stdout — the machine-readable contract."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print(json.dumps(data, indent=2, ensure_ascii=False))


def load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_universe() -> list[dict[str, str]]:
    """Load the stock universe from liquid_nse100.csv."""
    rows = []
    with open(UNIVERSE_FILE, "r", encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        for line in f:
            vals = line.strip().split(",")
            if len(vals) >= 2:
                rows.append({"symbol": vals[0], "name": vals[1],
                             "sector": vals[2] if len(vals) > 2 else "",
                             "lot_size": int(vals[3]) if len(vals) > 3 else 0})
    return rows


def resolve_ohlcv_path(symbol: str) -> Path | None:
    """Find the deepest available OHLCV CSV for a symbol.

    Search order:
      1. data_cache/SYMBOL.csv   (shared, 150+ stocks, ~2y)
      2. brd2/cache/SYMBOL.csv   (local, 2 stocks, ~10y)

    Returns the path with more bars, or None if neither exists.
    Nadh principle: archive the data, refresh with a flag.
    """
    candidates = []
    for base in (DATA_CACHE_DIR, CACHE_DIR):
        p = base / f"{symbol}.csv"
        if p.exists():
            try:
                import pandas as _pd
                n = sum(1 for _ in open(p)) - 1  # fast row count
                candidates.append((n, p))
            except Exception:
                candidates.append((0, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])  # most bars first
    return candidates[0][1]


def wilson_ci(wins: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Wilson 95% confidence interval for a proportion (wins/n)."""
    if n <= 0:
        return 0.0, 0.0
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / denom
    return max(0.0, center - half), min(1.0, center + half)
