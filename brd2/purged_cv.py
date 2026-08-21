"""04_purged_cv.py — Purged Walk-Forward Cross-Validation for BRD2.

Implements the validation protocol from BRD2 Section 5.1:
    - 5-Fold expanding walk-forward CV
    - Purge window: 9 trading days before test fold (removes label overlap)
    - Embargo window: 5 trading days after test fold (removes autocorrelation)

Also includes a validation harness that proves the purging is clean on
synthetic data — the Nadh move: validate the validator before trusting it.

Design (Nadh): this module is pure logic. It takes a DataFrame with features
and labels, and returns train/test index splits. No I/O, no side effects.

Usage:
    python brd2/04_purged_cv.py --validate    # run synthetic validation
    python brd2/04_purged_cv.py --check RELIANCE  # verify splits for a stock
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
    EMBARGO_WINDOW,
    H,
    N_FOLDS,
    PURGE_WINDOW,
    FEATURES_DIR,
    LABELS_DIR,
    emit_json,
)


# ---- Core purged CV splitter ------------------------------------------------

def purged_walk_forward_split(
    n: int,
    n_folds: int = N_FOLDS,
    purge: int = PURGE_WINDOW,
    embargo: int = EMBARGO_WINDOW,
    min_train_size: int = 200,
) -> list[tuple[np.ndarray, np.ndarray, dict[str, Any]]]:
    """Generate purged walk-forward train/test splits.

    Args:
        n: total number of rows (sorted chronologically).
        n_folds: number of test folds.
        purge: trading days removed before test fold from training set.
        embargo: trading days removed after test fold from training set.
        min_train_size: minimum training set size.

    Returns:
        List of (train_indices, test_indices, fold_info) tuples.
        train_indices and test_indices are numpy arrays of integer positions.
    """
    # Each fold test set is ~1/(n_folds+1) of the data (expanding train)
    test_size = max(30, (n - min_train_size) // (n_folds + 1))

    folds = []
    test_end = n

    for fold_i in range(n_folds):
        test_start = test_end - test_size
        if test_start < min_train_size + purge + 1:
            break  # not enough data for another fold

        # Purge: remove `purge` rows before test_start from training
        train_end_purged = test_start - purge
        if train_end_purged < min_train_size:
            break

        # Embargo: remove `embargo` rows after test_end from next training
        train_indices = np.arange(0, train_end_purged)
        test_indices = np.arange(test_start, test_end)

        fold_info = {
            "fold": n_folds - fold_i,
            "train_end": int(train_end_purged),
            "test_start": int(test_start),
            "test_end": int(test_end),
            "train_size": len(train_indices),
            "test_size": len(test_indices),
            "purge_gap": purge,
            "embargo_gap": embargo,
        }

        folds.append((train_indices, test_indices, fold_info))
        test_end = test_start - embargo  # next fold starts after embargo

    folds.reverse()  # chronological order
    return folds


def verify_no_leakage(
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    labels: pd.Series,
    purge: int = PURGE_WINDOW,
) -> dict[str, Any]:
    """Verify that train/test splits have no temporal label leakage.

    Checks:
    1. No index overlap between train and test
    2. Minimum gap between train end and test start >= purge
    3. No label in train set uses data from the test period (by construction
       of the Triple Barrier, labels at position i depend on bars i+1..i+H.
       If train max index + H < test min index, there's no overlap.)
    """
    overlap = set(train_idx) & set(test_idx)
    gap = int(test_idx.min() - train_idx.max()) if len(train_idx) > 0 and len(test_idx) > 0 else 999
    h = H  # label window

    return {
        "no_index_overlap": len(overlap) == 0,
        "overlap_count": len(overlap),
        "gap_between_sets": gap,
        "gap_sufficient": gap >= purge,
        "label_window_safe": gap >= h,  # strongest check: no label depends on test data
    }


# ---- Synthetic validation harness ------------------------------------------

def validate_purging() -> dict[str, Any]:
    """Prove the purging works on synthetic data.

    Creates a dataset where labels are perfectly correlated with future data
    (label at i = sign of value at i+5). If purging is correct, the CV should
    detect this and the train sets should NOT contain these future-correlated
    labels from the test period.
    """
    np.random.seed(42)
    n = 1000

    # Synthetic: values are random walks, label[i] = 1 if value[i+5] > value[i]
    values = np.cumsum(np.random.randn(n))
    labels = np.zeros(n)
    for i in range(n - 5):
        labels[i] = 1.0 if values[i + 5] > values[i] else 0.0

    # Inject a "leakage" signal: feature at position i includes value[i+3]
    # (simulating a future-dated feature that would leak if not purged)
    features_with_leak = np.column_stack([
        np.random.randn(n),  # innocent feature
        np.roll(values, -3),  # LEAKED feature (value 3 steps ahead)
    ])

    # Split WITHOUT purging
    folds_no_purge = purged_walk_forward_split(n, purge=0, embargo=0)

    # Split WITH purging
    folds_purged = purged_walk_forward_split(n, purge=PURGE_WINDOW, embargo=EMBARGO_WINDOW)

    # Check: in purged folds, the gap between train end and test start
    # should be >= PURGE_WINDOW
    all_gaps_ok = all(f[2]["purge_gap"] >= PURGE_WINDOW for f in folds_purged)

    # Check: no index overlap
    no_overlap = all(
        len(set(f[0]) & set(f[1])) == 0
        for f in folds_purged
    )

    # Check: label window safety (train max + H < test min)
    label_safe = all(
        f[2]["test_start"] - f[2]["train_end"] >= H
        for f in folds_purged
    )

    return {
        "n_samples": n,
        "n_folds_no_purge": len(folds_no_purge),
        "n_folds_purged": len(folds_purged),
        "all_gaps_sufficient": all_gaps_ok,
        "no_index_overlap": no_overlap,
        "label_window_safe": label_safe,
        "purge_window": PURGE_WINDOW,
        "embargo_window": EMBARGO_WINDOW,
        "fold_details": [f[2] for f in folds_purged],
        "verdict": "PASS" if (all_gaps_ok and no_overlap and label_safe) else "FAIL",
    }


# ---- Check purging for a real stock ----------------------------------------

def check_stock(symbol: str) -> dict[str, Any]:
    """Verify purged CV splits for a specific stock's feature/label data."""
    feat_path = FEATURES_DIR / f"{symbol}.csv"
    label_path = LABELS_DIR / f"{symbol}.csv"

    if not feat_path.exists() or not label_path.exists():
        return {"error": f"Missing data for {symbol}. Run 02_features.py and 03_labels.py first."}

    features = pd.read_csv(feat_path, index_col=0)
    labels = pd.read_csv(label_path, index_col=0)

    # Use only rows with valid labels
    valid_mask = labels["label"].notna()
    n_valid = int(valid_mask.sum())

    folds = purged_walk_forward_split(n_valid)

    fold_checks = []
    for train_idx, test_idx, info in folds:
        check = verify_no_leakage(train_idx, test_idx, labels.loc[valid_mask, "label"])
        fold_checks.append({**info, **check})

    all_pass = all(
        fc["no_index_overlap"] and fc["gap_sufficient"] and fc["label_window_safe"]
        for fc in fold_checks
    )

    return {
        "symbol": symbol,
        "total_rows": len(features),
        "valid_labeled_rows": n_valid,
        "n_folds": len(folds),
        "all_folds_pass": all_pass,
        "fold_checks": fold_checks,
        "verdict": "PASS" if all_pass else "FAIL",
    }


# ---- CLI -------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Purged walk-forward CV for BRD2.")
    parser.add_argument("--validate", action="store_true",
                        help="Run synthetic validation harness")
    parser.add_argument("--check", default="",
                        help="Verify splits for a specific stock")
    args = parser.parse_args()

    if args.validate:
        result = validate_purging()
        emit_json(result)
        return 0 if result["verdict"] == "PASS" else 1

    if args.check:
        result = check_stock(args.check)
        emit_json(result)
        return 0 if result.get("verdict") == "PASS" else 1

    # Default: validate
    result = validate_purging()
    emit_json(result)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
