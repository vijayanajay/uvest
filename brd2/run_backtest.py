"""run_backtest.py — End-to-end walk-forward backtest for BRD2 Stage 1.

Calls pipeline functions directly (no subprocess) for reliability.

Usage:
    python run_backtest.py --symbols RELIANCE,HDFCBANK
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common_brd2 import RESULTS_DIR, emit_json, ensure_dirs, save_json


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end BRD2 Stage 1 backtest.")
    parser.add_argument("--symbols", default="RELIANCE,HDFCBANK",
                        help="Comma-separated symbols")
    parser.add_argument("--period", default="3y", help="yfinance lookback")
    args = parser.parse_args()

    ensure_dirs()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    print("BRD2 Stage 1 - Walk-Forward Backtest")
    print(f"Symbols: {', '.join(symbols)}")
    print(f"Period: {args.period}")

    from fetch_data import download_symbol, save_to_cache, CACHE_DIR
    from features import build_features
    from labels import label_feature_df
    from purged_cv import purged_walk_forward_split
    from train import load_data, train_and_calibrate

    import pandas as pd
    from common_brd2 import FEATURES_DIR, LABELS_DIR, DATA_CACHE_DIR, resolve_ohlcv_path

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

    results = []
    for sym in symbols:
        print(f"\n{'='*50}")
        print(f"  {sym}")
        print(f"{'='*50}")

        t0 = time.time()

        # Step 1: Fetch — prefer data_cache/, fall back to download
        print("  [1/4] Fetching data...")
        existing = resolve_ohlcv_path(sym)
        if existing is not None:
            df = pd.read_csv(existing, parse_dates=["Date"], index_col="Date")
            source = "data_cache" if str(DATA_CACHE_DIR) in str(existing) else "brd2_cache"
            print(f"  OK: {len(df)} bars (from {source})")
        else:
            df = download_symbol(sym, args.period)
            if df is None:
                print(f"  SKIP: no data for {sym}")
                results.append({"symbol": sym, "error": "no data"})
                continue
            save_to_cache(sym, df)
            print(f"  OK: {len(df)} bars (downloaded)")

        # Step 2: Features
        print("  [2/4] Computing features...")
        feat = build_features(df, nifty_close)
        FEATURES_DIR.mkdir(parents=True, exist_ok=True)
        feat.to_csv(FEATURES_DIR / f"{sym}.csv", date_format="%Y-%m-%d")
        print(f"  OK: {len(feat)} rows")

        # Step 3: Labels
        print("  [3/4] Generating labels...")
        labels = label_feature_df(feat)
        LABELS_DIR.mkdir(parents=True, exist_ok=True)
        labels.to_csv(LABELS_DIR / f"{sym}.csv", date_format="%Y-%m-%d")
        valid_labels = labels["label"].notna().sum()
        print(f"  OK: {valid_labels} labeled rows")

        # Step 4: Train + backtest
        print("  [4/4] Training LightGBM + purged CV...")
        X, y, dates = load_data(sym)
        if len(X) < 250:
            print(f"  SKIP: only {len(X)} rows")
            results.append({"symbol": sym, "error": f"only {len(X)} rows"})
            continue

        report = train_and_calibrate(X, y, dates, sym, save_model=True)

        elapsed = time.time() - t0
        report["elapsed_seconds"] = round(elapsed, 1)
        results.append({"symbol": sym, "backtest": report})
        print(f"  Done in {elapsed:.1f}s")

    # ---- Consolidated report ------------------------------------------------
    stocks = []
    for r in results:
        bt = r.get("backtest", {})
        if "error" in bt or "error" in r:
            stocks.append({"symbol": r["symbol"], "error": bt.get("error", r.get("error"))})
            continue
        overall = bt.get("overall", {})
        hc = bt.get("high_confidence_calibrated", {})
        mc = bt.get("moderate_confidence_calibrated", {})
        top3 = bt.get("feature_importance", [])[:3]
        stocks.append({
            "symbol": r["symbol"],
            "n_samples": bt.get("n_samples", 0),
            "n_folds": bt.get("n_folds", 0),
            "auc_raw": overall.get("auc_raw"),
            "brier_before": overall.get("brier_before_cal"),
            "brier_after": overall.get("brier_after_cal"),
            "brier_improvement_pct": overall.get("brier_improvement_pct"),
            "high_conf_win_rate": hc.get("win_rate_pct"),
            "high_conf_n": hc.get("n_signals"),
            "high_conf_ci": f"{hc.get('ci_low_pct',0)}-{hc.get('ci_high_pct',0)}%",
            "moderate_conf_win_rate": mc.get("win_rate_pct"),
            "moderate_conf_n": mc.get("n_signals"),
            "top_features": [f["feature"] for f in top3],
        })

    valid = [s for s in stocks if "error" not in s]
    avg_auc = sum(s.get("auc_raw") or 0 for s in valid) / len(valid) if valid else 0
    avg_wr = sum(s.get("high_conf_win_rate") or 0 for s in valid) / len(valid) if valid else 0
    total_sig = sum(s.get("high_conf_n") or 0 for s in valid)

    consolidated = {
        "summary": {
            "stocks_analyzed": len(stocks),
            "avg_auc": round(avg_auc, 4),
            "avg_high_conf_win_rate": round(avg_wr, 1),
            "total_high_conf_signals": total_sig,
            "note": "Walk-forward OOS results. AUC>0.55 + WR>55% at P>=0.70 indicates potential edge.",
        },
        "stocks": stocks,
    }

    out_path = RESULTS_DIR / "consolidated_report.json"
    save_json(out_path, consolidated)

    # Print summary
    print(f"\n{'='*50}")
    print("  CONSOLIDATED RESULTS")
    print(f"{'='*50}")
    s = consolidated["summary"]
    print(f"  Stocks: {s['stocks_analyzed']}  |  Avg AUC: {s['avg_auc']}  |  Avg WR@0.70: {s['avg_high_conf_win_rate']}%")
    print(f"  Total hi-conf signals: {s['total_high_conf_signals']}")
    print()
    for st in stocks:
        if "error" in st:
            print(f"  {st['symbol']:15s} ERROR: {st['error']}")
        else:
            print(f"  {st['symbol']:15s} AUC={st.get('auc_raw',0):.3f}  WR={st.get('high_conf_win_rate',0):.1f}% (n={st.get('high_conf_n',0)})  CI={st.get('high_conf_ci','?')}")
            print(f"  {'':15s} Top: {', '.join(st.get('top_features',[]))}")

    print(f"\nReport: {out_path}")
    emit_json(consolidated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
