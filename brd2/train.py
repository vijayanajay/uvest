"""05_train.py — LightGBM classifier + Isotonic Calibration for BRD2.

Trains a LightGBM model using purged walk-forward CV, calibrates
probabilities with Isotonic Regression, and produces a walk-forward
backtest report.

Design (Nadh): the model is simple, constrained, and honest. Every fold
is out-of-sample. Isotonic calibration maps raw scores to real win
probabilities. The report shows what actually happened, not what we
hoped would happen.

Usage:
    python brd2/05_train.py --symbol RELIANCE
    python brd2/05_train.py --symbol HDFCBANK --save-model
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

import lightgbm as lgb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common_brd2 import (  # noqa: E402
    CACHE_DIR,
    COST_RATE_FUT,
    DATA_CACHE_DIR,
    FEATURES_DIR,
    LABELS_DIR,
    LGBM_PARAMS,
    MODELS_DIR,
    RESULTS_DIR,
    STAGE1_FEATURES,
    emit_json,
    ensure_dirs,
    wilson_ci,
)
from purged_cv import purged_walk_forward_split  # noqa: E402


def load_nifty_regime() -> dict[str, bool] | None:
    """Load NIFTY 200-DMA regime state per date.

    Returns {date_str: True/False} where True = NIFTY above 200-DMA (bull tape).
    Nadh: reuse existing data, don't recompute what screen_regime.py already does.
    """
    import pandas as pd
    nifty_path = None
    for candidate in [DATA_CACHE_DIR / "NIFTY50.csv", CACHE_DIR / "^NSEI.csv"]:
        if candidate.exists():
            nifty_path = candidate
            break
    if nifty_path is None:
        return None
    try:
        df = pd.read_csv(nifty_path, parse_dates=["Date"], index_col="Date")
        if len(df) < 200:
            return None
        close = df["Close"]
        dma200 = close.rolling(200).mean()
        state = {}
        for ts, c in close.items():
            m = dma200.loc[ts]
            if m == m:  # skip NaN
                state[ts.strftime("%Y-%m-%d")] = bool(c > m)
        return state or None
    except Exception:
        return None


def load_data(symbol: str) -> tuple[pd.DataFrame, pd.Series, pd.DatetimeIndex]:
    """Load features and labels for a symbol, aligned and cleaned."""
    feat_path = FEATURES_DIR / f"{symbol}.csv"
    label_path = LABELS_DIR / f"{symbol}.csv"

    features = pd.read_csv(feat_path, parse_dates=["Date"], index_col="Date")
    labels_df = pd.read_csv(label_path, parse_dates=["Date"], index_col="Date")

    # Align and drop rows with NaN labels or NaN features
    df = features[STAGE1_FEATURES].copy()
    df["label"] = labels_df["label"]
    df["Close"] = features["Close"]

    # Drop NaN labels (last H rows) and rows with NaN features
    valid = df.dropna(subset=["label"] + STAGE1_FEATURES)

    X = valid[STAGE1_FEATURES]
    y = valid["label"].astype(int)
    dates = valid.index

    return X, y, dates


def train_and_calibrate(
    X: pd.DataFrame,
    y: pd.Series,
    dates: pd.DatetimeIndex,
    symbol: str,
    save_model: bool = False,
) -> dict[str, Any]:
    """Run purged walk-forward CV with LightGBM + Isotonic Calibration.

    Returns a comprehensive report dict.
    """
    n = len(X)
    folds = purged_walk_forward_split(n)

    if not folds:
        return {"error": f"Insufficient data for {symbol}: {n} rows, need >= 200"}

    # Load NIFTY regime state for regime-conditioned reporting
    nifty_regime = load_nifty_regime()

    # Collect out-of-fold predictions for calibration
    oof_predictions = []
    oof_actuals = []
    oof_dates = []
    fold_results = []
    # Regime buckets: {"bull": [(prob, actual), ...], "bear": [...]}
    regime_buckets: dict[str, list[tuple[float, int]]] = {"bull": [], "bear": []}

    for train_idx, test_idx, fold_info in folds:
        X_train = X.iloc[train_idx]
        y_train = y.iloc[train_idx]
        X_test = X.iloc[test_idx]
        y_test = y.iloc[test_idx]
        test_dates = dates[test_idx]

        # Train LightGBM
        model = lgb.LGBMClassifier(**LGBM_PARAMS)
        model.fit(X_train, y_train)

        # Raw predictions
        y_prob_raw = model.predict_proba(X_test)[:, 1]

        # Collect for global calibration
        oof_predictions.extend(y_prob_raw.tolist())
        oof_actuals.extend(y_test.tolist())
        oof_dates.extend([d.strftime("%Y-%m-%d") for d in test_dates])

        # Fold-level metrics
        fold_auc = roc_auc_score(y_test, y_prob_raw) if len(set(y_test)) > 1 else 0.5
        fold_brier = brier_score_loss(y_test, y_prob_raw)
        fold_ll = log_loss(y_test, y_prob_raw)

        # Win rate at high-confidence threshold (P >= 0.70)
        high_conf_mask = y_prob_raw >= 0.70
        n_high = int(high_conf_mask.sum())
        wins_high = int((y_test.values[high_conf_mask]).sum()) if n_high > 0 else 0
        wr_high = wins_high / n_high * 100 if n_high > 0 else 0

        ci_low, ci_high = wilson_ci(wins_high, n_high) if n_high > 0 else (0, 0)

        # Feature importance (top 5)
        importance = dict(zip(STAGE1_FEATURES, model.feature_importances_))
        top5 = sorted(importance.items(), key=lambda x: -x[1])[:5]

        # Regime conditioning: bucket predictions by NIFTY 200-DMA state
        if nifty_regime is not None:
            for j, (prob, actual, dt) in enumerate(
                zip(y_prob_raw.tolist(), y_test.tolist(),
                    [d.strftime("%Y-%m-%d") for d in test_dates])
            ):
                is_bull = nifty_regime.get(dt)
                if is_bull is not None:
                    key = "bull" if is_bull else "bear"
                    regime_buckets[key].append((prob, actual))

        fold_results.append({
            **fold_info,
            "auc": round(fold_auc, 4),
            "brier": round(fold_brier, 4),
            "log_loss": round(fold_ll, 4),
            "high_conf_count": n_high,
            "high_conf_wins": wins_high,
            "high_conf_win_rate": round(wr_high, 1),
            "ci_low": round(ci_low * 100, 1),
            "ci_high": round(ci_high * 100, 1),
            "top_features": [{"feature": f, "importance": int(imp)} for f, imp in top5],
        })

    # ---- Isotonic Calibration on out-of-fold predictions -------------------
    oof_arr = np.array(oof_predictions)
    actual_arr = np.array(oof_actuals)

    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(oof_arr, actual_arr)

    calibrated_probs = iso.predict(oof_arr)

    # Brier score before/after calibration
    brier_before = brier_score_loss(actual_arr, oof_arr)
    brier_after = brier_score_loss(actual_arr, calibrated_probs)
    brier_improvement = (brier_before - brier_after) / brier_before * 100 if brier_before > 0 else 0

    # AUC is invariant to calibration, but report for completeness
    overall_auc = roc_auc_score(actual_arr, oof_arr) if len(set(actual_arr)) > 1 else 0.5

    # High-confidence performance (calibrated)
    high_conf_mask_cal = calibrated_probs >= 0.70
    n_high_cal = int(high_conf_mask_cal.sum())
    wins_high_cal = int(actual_arr[high_conf_mask_cal].sum()) if n_high_cal > 0 else 0
    wr_high_cal = wins_high_cal / n_high_cal * 100 if n_high_cal > 0 else 0
    ci_cal = wilson_ci(wins_high_cal, n_high_cal) if n_high_cal > 0 else (0, 0)

    # Overall calibrated stats
    above_55_mask = calibrated_probs >= 0.55
    n_55 = int(above_55_mask.sum())
    wins_55 = int(actual_arr[above_55_mask].sum()) if n_55 > 0 else 0

    # Save calibration curve data (for plotting)
    cal_curve_bins = []
    for threshold in np.arange(0.3, 0.9, 0.05):
        mask = (calibrated_probs >= threshold - 0.025) & (calibrated_probs < threshold + 0.025)
        if mask.sum() >= 5:
            cal_curve_bins.append({
                "threshold": round(float(threshold), 2),
                "n": int(mask.sum()),
                "predicted_prob": round(float(calibrated_probs[mask].mean()), 3),
                "actual_win_rate": round(float(actual_arr[mask].mean()) * 100, 1),
            })

    # Feature importance from last fold model (representative)
    last_fold_model = lgb.LGBMClassifier(**LGBM_PARAMS)
    last_fold_model.fit(X, y)  # full data for feature importance
    importance_full = dict(zip(STAGE1_FEATURES, last_fold_model.feature_importances_))
    top10_full = sorted(importance_full.items(), key=lambda x: -x[1])[:10]

    # Save model if requested
    model_path = None
    if save_model:
        ensure_dirs()
        model_path = MODELS_DIR / f"{symbol}_lgbm.txt"
        last_fold_model.booster_.save_model(str(model_path))

        # Save isotonic calibrator
        iso_path = MODELS_DIR / f"{symbol}_iso.json"
        ensure_dirs()
        with open(iso_path, "w") as f:
            json.dump({
                "x_thresholds": iso.X_thresholds_.tolist(),
                "y_thresholds": iso.y_thresholds_.tolist(),
            }, f)

    # ---- Regime-conditioned metrics -------------------------------------------
    regime_report = None
    if nifty_regime and any(regime_buckets.values()):
        regime_report = {}
        for regime_key in ("bull", "bear"):
            bucket = regime_buckets[regime_key]
            if not bucket:
                continue
            probs, actuals = zip(*bucket)
            probs_arr = np.array(probs)
            actuals_arr = np.array(actuals)
            n_regime = len(bucket)
            wins_regime = int(actuals_arr.sum())
            # High-confidence in this regime
            hc_mask = probs_arr >= 0.70
            n_hc = int(hc_mask.sum())
            wins_hc = int(actuals_arr[hc_mask].sum()) if n_hc > 0 else 0
            ci_r = wilson_ci(wins_hc, n_hc) if n_hc > 0 else (0, 0)
            regime_report[regime_key] = {
                "n": n_regime,
                "win_rate_pct": round(wins_regime / n_regime * 100, 1),
                "high_conf_n": n_hc,
                "high_conf_win_rate_pct": round(wins_hc / n_hc * 100, 1) if n_hc > 0 else 0,
                "ci_low_pct": round(ci_r[0] * 100, 1),
                "ci_high_pct": round(ci_r[1] * 100, 1),
            }

    report = {
        "symbol": symbol,
        "n_samples": n,
        "n_folds": len(folds),
        "oof_predictions": len(oof_predictions),
        "overall": {
            "auc_raw": round(overall_auc, 4),
            "brier_before_cal": round(brier_before, 4),
            "brier_after_cal": round(brier_after, 4),
            "brier_improvement_pct": round(brier_improvement, 1),
        },
        "high_confidence_calibrated": {
            "threshold": 0.70,
            "n_signals": n_high_cal,
            "wins": wins_high_cal,
            "win_rate_pct": round(wr_high_cal, 1),
            "ci_low_pct": round(ci_cal[0] * 100, 1),
            "ci_high_pct": round(ci_cal[1] * 100, 1),
        },
        "moderate_confidence_calibrated": {
            "threshold": 0.55,
            "n_signals": n_55,
            "wins": wins_55,
            "win_rate_pct": round(wins_55 / n_55 * 100, 1) if n_55 > 0 else 0,
        },
        "regime": regime_report,
        "fold_results": fold_results,
        "calibration_curve": cal_curve_bins,
        "feature_importance": [{"feature": f, "importance": int(imp)} for f, imp in top10_full],
        "model_saved": str(model_path) if model_path else None,
    }

    return report


# ---- CLI -------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Train LightGBM + Isotonic Calibration for BRD2.")
    parser.add_argument("--symbol", required=True, help="Stock symbol (e.g. RELIANCE)")
    parser.add_argument("--save-model", action="store_true", help="Save trained model to disk")
    args = parser.parse_args()

    ensure_dirs()

    symbol = args.symbol.upper()
    X, y, dates = load_data(symbol)

    if len(X) < 250:
        emit_json({"error": f"Insufficient data for {symbol}: {len(X)} rows, need >= 250"})
        return 1

    report = train_and_calibrate(X, y, dates, symbol, args.save_model)

    # Save report
    out_path = RESULTS_DIR / f"{symbol}_backtest.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        f.write("\n")

    emit_json(report)
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    sys.exit(main())
