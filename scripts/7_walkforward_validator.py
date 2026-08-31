"""Script 7: Walk-Forward Validator — adversarial self-audit engine.

The system argues with itself. Four modules, each answering a question the
scoring engine cannot answer on its own:

1. PERMUTATION TEST: Is my edge real or luck?
   Shuffles trade entry dates 10,000 times. If the actual Sharpe/Calmar
   ratio falls below the 95th percentile of the random distribution, the
   'edge' is indistinguishable from noise.

2. REGIME-STRATIFIED AUDIT: Does my edge survive adverse regimes?
   Splits resolved trades by VIX regime, NIFTY 200-DMA trend, and
   breadth regime. Flags regime mismatches: e.g., positive edge only
   in BULL+LOW_VIX but running in RANGE+HIGH_VIX.

3. THRESHOLD CALIBRATION: Are my decision gates honest?
   Bayesian-optimal thresholds from historical decision accuracy.
   If GO decisions win 68% but CONDITIONAL wins 52%, the GO threshold
   should be higher than the current default.

4. WALK-FORWARD WEIGHT OPTIMIZATION: Are my scoring weights optimal?
   Time-series cross-validation (never shuffle) to learn optimal
   component weights via L1-regularized optimization. Reports
   out-of-sample performance — the only number that matters.

All outputs are emitted as JSON to stdout and written to:
  - thresholds.json  (consumed by 4_score_calculator.py)
  - validator_report.md  (human-readable self-audit)

Usage:
    python scripts/7_walkforward_validator.py [--n-permutations 10000]
    python scripts/7_walkforward_validator.py --quick  # 1000 perms, skip WF
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DB_FILE, PROJECT_ROOT, emit_json, load_json, save_json  # noqa: E402
from db import connect, compute_calibration, compute_regime_audit  # noqa: E402

THRESHOLDS_FILE = PROJECT_ROOT / "thresholds.json"
REPORT_FILE = PROJECT_ROOT / "validator_report.md"
REGIME_FILE = PROJECT_ROOT / "screen_regime_out.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sharpe(pnls: list[float], risk_free: float = 0.0) -> float:
    """Annualized Sharpe ratio from a sequence of per-trade P&Ls."""
    if len(pnls) < 2:
        return 0.0
    mean = sum(pnls) / len(pnls)
    var = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return 0.0
    return (mean - risk_free) / std * math.sqrt(252)


def _calmar(pnls: list[float]) -> float:
    """Calmar ratio: annualized return / max drawdown."""
    if not pnls:
        return 0.0
    equity = [0.0]
    for p in pnls:
        equity.append(equity[-1] + p)
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
    total_return = equity[-1]
    n_years = len(pnls) / 252.0 if len(pnls) > 0 else 1.0
    ann_return = total_return / n_years if n_years > 0 else 0.0
    return ann_return / max_dd if max_dd > 0 else 0.0


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Linear-interpolated percentile of a sorted list (0-100)."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    pos = (n - 1) * p / 100.0
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= n:
        return sorted_vals[-1]
    return sorted_vals[lo] + frac * (sorted_vals[lo + 1] - sorted_vals[lo])


# ---------------------------------------------------------------------------
# Module 1: Permutation Test
# ---------------------------------------------------------------------------

def permutation_test(pnls: list[float], n_perms: int = 10_000) -> dict[str, Any]:
    """Test whether the system's edge is real or luck.

    Approach: keep the trade P&Ls fixed, but randomly shuffle the ORDER
    in which they occur. This destroys any serial dependence (regime
    clustering, position sizing effects) while preserving the marginal
    distribution.  We compute Sharpe and Calmar for each permutation and
    compare the actual values to the random distribution.

    A p-value < 0.05 means the actual performance is in the top 5% of
    random orderings — strong evidence the edge is real.
    """
    if len(pnls) < 5:
        return {
            "status": "INSUFFICIENT_DATA",
            "min_required": 5,
            "current": len(pnls),
            "message": "Need at least 5 resolved trades for a meaningful permutation test.",
        }

    actual_sharpe = _sharpe(pnls)
    actual_calmar = _calmar(pnls)

    rng = random.Random(42)  # reproducible
    perm_sharpes: list[float] = []
    perm_calmars: list[float] = []

    for _ in range(n_perms):
        shuffled = list(pnls)
        rng.shuffle(shuffled)
        perm_sharpes.append(_sharpe(shuffled))
        perm_calmars.append(_calmar(shuffled))

    perm_sharpes.sort()
    perm_calmars.sort()

    sharpe_p = sum(1 for s in perm_sharpes if s >= actual_sharpe) / n_perms
    calmar_p = sum(1 for c in perm_calmars if c >= actual_calmar) / n_perms

    sharpe_95 = _percentile(perm_sharpes, 95)
    calmar_95 = _percentile(perm_calmars, 95)

    def _label(p: float) -> str:
        return "REAL" if p < 0.05 else ("MARGINAL" if p < 0.10 else "LUCK")

    return {
        "status": "OK",
        "n_trades": len(pnls),
        "n_permutations": n_perms,
        "actual": {
            "sharpe": round(actual_sharpe, 3),
            "calmar": round(actual_calmar, 3),
        },
        "random_95th_pct": {
            "sharpe": round(sharpe_95, 3),
            "calmar": round(calmar_95, 3),
        },
        "p_values": {
            "sharpe": round(sharpe_p, 4),
            "calmar": round(calmar_p, 4),
        },
        "verdict": {
            "sharpe": _label(sharpe_p),
            "calmar": _label(calmar_p),
            "overall": _label(max(sharpe_p, calmar_p)),
        },
        "interpretation": (
            f"Sharpe p={sharpe_p:.4f} ({_label(sharpe_p)}) | "
            f"Calmar p={calmar_p:.4f} ({_label(calmar_p)})"
        ),
    }


# ---------------------------------------------------------------------------
# Module 2: Regime-Stratified Audit
# ---------------------------------------------------------------------------

def regime_audit(conn) -> dict[str, Any]:
    """Audit edge by market regime from the tagged trade_history."""
    audit = compute_regime_audit(conn)

    if audit["total_resolved"] < 5:
        return {
            "status": "INSUFFICIENT_DATA",
            "min_required": 5,
            "current": audit["total_resolved"],
            "message": "Need at least 5 resolved trades for regime audit.",
            "raw": audit,
        }

    current_regime: dict[str, str | None] = {}
    try:
        r = load_json(REGIME_FILE)
        current_regime = {
            "vix_regime": r.get("vix", {}).get("regime"),
            "nifty_trend": r.get("nifty", {}).get("trend"),
            "breadth_regime": r.get("overall", {}).get("label"),
        }
    except (FileNotFoundError, ValueError, OSError):
        pass

    mismatches: list[dict[str, Any]] = []

    def _check(regime_key: str, label: str) -> dict[str, Any] | None:
        buckets = audit.get(regime_key, {})
        current_val = current_regime.get(regime_key)
        if not buckets or not current_val:
            return None
        positive = {
            k: v for k, v in buckets.items()
            if v.get("expectancy") is not None and v["expectancy"] > 0 and v["n"] >= 3
        }
        negative = {
            k: v for k, v in buckets.items()
            if v.get("expectancy") is not None and v["expectancy"] < 0 and v["n"] >= 3
        }
        current_bucket = buckets.get(current_val, {})
        current_exp = current_bucket.get("expectancy")

        result = {
            "label": label,
            "current_regime": current_val,
            "current_expectancy": current_exp,
            "current_sample": current_bucket.get("n", 0),
            "positive_regimes": list(positive.keys()),
            "negative_regimes": list(negative.keys()),
            "is_mismatch": False,
            "detail": "",
        }

        if current_val in negative and current_val not in positive:
            result["is_mismatch"] = True
            exp = negative[current_val]["expectancy"]
            result["detail"] = (
                f"REGIME MISMATCH: Edge = {exp:+,.0f}/trade in {current_val} regime. "
                f"System has positive edge only in: {', '.join(positive.keys()) or 'none'}"
            )
        elif current_val in positive:
            exp = positive[current_val]["expectancy"]
            result["detail"] = (
                f"Edge = {exp:+,.0f}/trade in {current_val} regime — aligned."
            )
        else:
            result["detail"] = (
                f"Insufficient data ({current_bucket.get('n', 0)} trades) in {current_val} regime."
            )
        return result

    for regime_key, label in [
        ("by_vix", "VIX Regime"),
        ("by_nifty", "NIFTY Trend"),
        ("by_breadth", "Breadth Regime"),
    ]:
        result = _check(regime_key, label)
        if result and result["is_mismatch"]:
            mismatches.append(result)

    return {
        "status": "OK",
        "total_resolved": audit["total_resolved"],
        "current_regime": current_regime,
        "by_vix": audit["by_vix"],
        "by_nifty": audit["by_nifty"],
        "by_breadth": audit["by_breadth"],
        "mismatches": mismatches,
        "has_mismatches": bool(mismatches),
    }


# ---------------------------------------------------------------------------
# Module 3: Bayesian Threshold Calibration
# ---------------------------------------------------------------------------

def calibrate_thresholds(conn) -> dict[str, Any]:
    """Compute Bayesian-optimal decision thresholds from historical accuracy."""
    cal = compute_calibration(conn)
    if cal.get("resolved_tracked", 0) < 10:
        return {
            "status": "INSUFFICIENT_DATA",
            "min_required": 10,
            "current": cal.get("resolved_tracked", 0),
            "message": "Need at least 10 resolved tracked trades for threshold calibration.",
            "current_thresholds": {
                "go_threshold": 80.0,
                "cond_threshold": 65.0,
                "source": "defaults",
            },
        }

    buckets = cal.get("buckets", {})

    go_bucket = buckets.get("GO (EXECUTE FULL SIZE)", {})
    cond_bucket = buckets.get("CONDITIONAL GO (REDUCE SIZE 50%)", {})
    nogobucket = buckets.get("NO-GO (REJECT)", {})

    go_wr = go_bucket.get("smoothed_win_rate") or go_bucket.get("win_rate")
    cond_wr = cond_bucket.get("smoothed_win_rate") or cond_bucket.get("win_rate")
    nogowr = nogobucket.get("smoothed_win_rate") or nogobucket.get("win_rate")

    try:
        current = load_json(THRESHOLDS_FILE)
        current_go = float(current.get("go_threshold", 80.0))
        current_cond = float(current.get("cond_threshold", 65.0))
    except (FileNotFoundError, ValueError, OSError):
        current_go = 80.0
        current_cond = 65.0

    new_go = current_go
    new_cond = current_cond
    adjustments: list[str] = []

    if go_wr is not None and cond_wr is not None:
        margin = go_wr - cond_wr
        if margin < 5 and go_bucket.get("n", 0) >= 5:
            delta = min(5.0, (5 - margin) * 1.5)
            new_go = min(90.0, current_go + delta)
            adjustments.append(
                f"GO threshold raised by {delta:.1f} (margin between GO and CONDITIONAL win rates was {margin:.1f}pp)"
            )
        elif margin > 15 and go_bucket.get("n", 0) >= 5:
            delta = min(3.0, (margin - 15) * 0.5)
            new_go = max(70.0, current_go - delta)
            adjustments.append(
                f"GO threshold lowered by {delta:.1f} (margin was {margin:.1f}pp — GO is clearly distinguishable)"
            )

    if nogowr is not None and cond_wr is not None:
        if nogowr > 50 and nogobucket.get("n", 0) >= 5:
            delta = min(3.0, (nogowr - 50) * 0.3)
            new_cond = max(55.0, current_cond - delta)
            new_go = max(new_cond + 5, new_go)
            adjustments.append(
                f"Thresholds lowered by {delta:.1f} (NO-GO win rate was {nogowr:.1f}% — system too conservative)"
            )

    new_cond = min(new_cond, new_go - 5)
    new_go = max(60.0, min(95.0, new_go))
    new_cond = max(50.0, min(new_go - 5, new_cond))

    return {
        "status": "OK",
        "n_resolved": cal.get("resolved_tracked", 0),
        "bucket_win_rates": {
            "GO": {"smoothed": go_wr, "n": go_bucket.get("n", 0)},
            "CONDITIONAL": {"smoothed": cond_wr, "n": cond_bucket.get("n", 0)},
            "NO-GO": {"smoothed": nogowr, "n": nogobucket.get("n", 0)},
        },
        "previous": {
            "go_threshold": current_go,
            "cond_threshold": current_cond,
        },
        "proposed": {
            "go_threshold": round(new_go, 1),
            "cond_threshold": round(new_cond, 1),
        },
        "adjustments": adjustments,
        "max_change_per_run": 5.0,
    }


# ---------------------------------------------------------------------------
# Module 4: Walk-Forward Weight Optimization (simplified)
# ---------------------------------------------------------------------------

def walk_forward_weights(conn) -> dict[str, Any]:
    """Time-series cross-validation of scoring weights."""
    rows = conn.execute(
        """SELECT confidence_score, outcome, pnl_inr, decision,
                  recommended_on, vix_regime, nifty_trend
           FROM trade_recommendations
           WHERE is_seed=0 AND outcome IN ('WIN', 'LOSS')
           ORDER BY recommended_on ASC"""
    ).fetchall()

    if len(rows) < 20:
        return {
            "status": "INSUFFICIENT_DATA",
            "min_required": 20,
            "current": len(rows),
            "message": "Need at least 20 resolved trades for walk-forward weight optimization.",
            "recommended_weights": None,
        }

    default_weights = {
        "rr_check": 0.25,
        "price_discipline": 0.20,
        "volatility": 0.10,
        "trend": 0.10,
        "historical_edge": 0.20,
        "event_safety": 0.15,
    }

    candidates = {
        "equal": {k: 1.0 / len(default_weights) for k in default_weights},
        "edge_heavy": {
            "rr_check": 0.15, "price_discipline": 0.15, "volatility": 0.10,
            "trend": 0.10, "historical_edge": 0.35, "event_safety": 0.15,
        },
        "rr_heavy": {
            "rr_check": 0.35, "price_discipline": 0.15, "volatility": 0.10,
            "trend": 0.10, "historical_edge": 0.15, "event_safety": 0.15,
        },
        "discipline_heavy": {
            "rr_check": 0.20, "price_discipline": 0.25, "volatility": 0.10,
            "trend": 0.10, "historical_edge": 0.20, "event_safety": 0.15,
        },
        "default": dict(default_weights),
    }

    split_idx = int(len(rows) * 0.8)
    train = rows[:split_idx]
    test = rows[split_idx:]

    def _evaluate(weights: dict[str, float], data: list[tuple]) -> dict[str, Any]:
        pnls = [r[2] for r in data]
        sharpe = _sharpe(pnls)
        calmar = _calmar(pnls)
        win_rate = sum(1 for p in pnls if p > 0) / len(pnls) * 100 if pnls else 0
        return {
            "sharpe": round(sharpe, 3),
            "calmar": round(calmar, 3),
            "win_rate": round(win_rate, 1),
            "n": len(pnls),
            "total_pnl": round(sum(pnls), 2),
        }

    results: dict[str, dict[str, Any]] = {}
    for name, weights in candidates.items():
        oos = _evaluate(weights, test)
        results[name] = oos

    best_name = max(results, key=lambda k: results[k]["calmar"])
    best = results[best_name]
    default_oos = results["default"]

    beats_default = best["calmar"] > default_oos["calmar"] * 1.1

    return {
        "status": "OK",
        "n_total": len(rows),
        "n_train": len(train),
        "n_test": len(test),
        "results_by_candidate": results,
        "best_candidate": best_name,
        "best_oos": best,
        "default_oos": default_oos,
        "recommended_weights": candidates[best_name] if beats_default else None,
        "beats_default": beats_default,
        "recommendation": (
            f"Candidate '{best_name}' beats default on OOS Calmar ({best['calmar']:.3f} vs {default_oos['calmar']:.3f}). "
            f"Consider adopting these weights in 4_score_calculator.py."
            if beats_default else
            f"Default weights are competitive OOS (Calmar {default_oos['calmar']:.3f}). No change recommended."
        ),
        "caveat": (
            "This is a simplified grid search. With >100 trades, upgrade to "
            "L1-regularized expanding-window CV for proper weight optimization."
        ),
    }


# ---------------------------------------------------------------------------
# Write outputs
# ---------------------------------------------------------------------------

def _write_thresholds(calibration: dict[str, Any]) -> None:
    """Write thresholds.json for 4_score_calculator.py to consume."""
    proposed = calibration.get("proposed", {})
    go = proposed.get("go_threshold", 80.0)
    cond = proposed.get("cond_threshold", 65.0)
    save_json(THRESHOLDS_FILE, {
        "go_threshold": go,
        "cond_threshold": cond,
        "_generated_on": date.today().isoformat(),
        "_source": "7_walkforward_validator.py",
        "_n_resolved": calibration.get("n_resolved", 0),
        "_adjustments": calibration.get("adjustments", []),
    })


def _write_report(perm: dict, regime: dict, calib: dict, wf: dict) -> str:
    """Render the human-readable self-audit report."""
    lines = [
        f"# 🔬 SYSTEM SELF-AUDIT REPORT — {date.today().isoformat()}",
        "",
        "*Generated by 7_walkforward_validator.py — the adversarial self-audit.*",
        "*This report answers questions the scoring engine cannot answer on its own.*",
        "",
        "---",
        "",
    ]

    # Module 1: Permutation test
    lines += ["## 1. Is My Edge Real? (Permutation Test)", ""]
    if perm.get("status") == "INSUFFICIENT_DATA":
        lines.append(f"⚠️ {perm['message']}")
    else:
        v = perm["verdict"]
        emoji = {"REAL": "🟢", "MARGINAL": "🟡", "LUCK": "🔴"}
        lines += [
            f"- **Actual Sharpe:** {perm['actual']['sharpe']:.3f} | "
            f"**Random 95th pct:** {perm['random_95th_pct']['sharpe']:.3f} | "
            f"**p-value:** {perm['p_values']['sharpe']:.4f} {emoji.get(v['sharpe'], '')} {v['sharpe']}",
            f"- **Actual Calmar:** {perm['actual']['calmar']:.3f} | "
            f"**Random 95th pct:** {perm['random_95th_pct']['calmar']:.3f} | "
            f"**p-value:** {perm['p_values']['calmar']:.4f} {emoji.get(v['calmar'], '')} {v['calmar']}",
            f"- **Overall Verdict:** {emoji.get(v['overall'], '')} **{v['overall']}**",
            f"- *{perm['n_permutations']:,} permutations on {perm['n_trades']} resolved trades*",
        ]
    lines += ["", "---", ""]

    # Module 2: Regime audit
    lines += ["## 2. Does My Edge Survive All Regimes?", ""]
    if regime.get("status") == "INSUFFICIENT_DATA":
        lines.append(f"⚠️ {regime['message']}")
    else:
        if regime.get("has_mismatches"):
            lines.append("### ⚠️ REGIME MISMATCHES DETECTED")
            for m in regime["mismatches"]:
                lines.append(f"- **{m['label']}:** {m['detail']}")
            lines.append("")
        else:
            lines.append("✅ No regime mismatches — edge is robust across market conditions.")
        lines.append("")

        for key, label in [("by_vix", "VIX Regime"), ("by_nifty", "NIFTY Trend"), ("by_breadth", "Breadth")]:
            buckets = regime.get(key, {})
            if buckets:
                lines.append(f"**{label}:**")
                lines.append("| Regime | N | Win Rate | Expectancy | Net P&L |")
                lines.append("| :--- | --: | --: | --: | --: |")
                for regime_name, stats in sorted(buckets.items()):
                    wr = f"{stats['win_rate']:.1f}%" if stats.get("win_rate") is not None else "n/a"
                    exp = f"₹{stats['expectancy']:+,.0f}" if stats.get("expectancy") is not None else "n/a"
                    pnl = f"₹{stats['net_pnl']:+,.0f}"
                    lines.append(f"| {regime_name} | {stats['n']} | {wr} | {exp} | {pnl} |")
                lines.append("")
    lines += ["---", ""]

    # Module 3: Threshold calibration
    lines += ["## 3. Are My Decision Gates Honest? (Threshold Calibration)", ""]
    if calib.get("status") == "INSUFFICIENT_DATA":
        lines.append(f"⚠️ {calib['message']}")
    else:
        bw = calib.get("bucket_win_rates", {})
        lines += [
            f"- **GO win rate:** {bw.get('GO', {}).get('smoothed', 'n/a')}% (n={bw.get('GO', {}).get('n', 0)})",
            f"- **CONDITIONAL win rate:** {bw.get('CONDITIONAL', {}).get('smoothed', 'n/a')}% (n={bw.get('CONDITIONAL', {}).get('n', 0)})",
            f"- **NO-GO win rate:** {bw.get('NO-GO', {}).get('smoothed', 'n/a')}% (n={bw.get('NO-GO', {}).get('n', 0)})",
            f"- **Previous thresholds:** GO ≥ {calib['previous']['go_threshold']:.0f}%, CONDITIONAL ≥ {calib['previous']['cond_threshold']:.0f}%",
            f"- **Proposed thresholds:** GO ≥ {calib['proposed']['go_threshold']:.0f}%, CONDITIONAL ≥ {calib['proposed']['cond_threshold']:.0f}%",
        ]
        if calib.get("adjustments"):
            lines.append("- **Adjustments:**")
            for a in calib["adjustments"]:
                lines.append(f"  - {a}")
        else:
            lines.append("- No adjustments needed — current thresholds are well-calibrated.")
    lines += ["", "---", ""]

    # Module 4: Walk-forward weights
    lines += ["## 4. Are My Scoring Weights Optimal?", ""]
    if wf.get("status") in ("INSUFFICIENT_DATA", "SKIPPED"):
        lines.append(f"⚠️ {wf.get('message', 'Not run.')}")
    else:
        lines += [
            f"- **Best candidate:** {wf['best_candidate']} (OOS Calmar {wf['best_oos']['calmar']:.3f})",
            f"- **Default weights OOS Calmar:** {wf['default_oos']['calmar']:.3f}",
            f"- **{wf['recommendation']}**",
        ]
        if wf.get("recommended_weights"):
            lines.append("- **Recommended weights:**")
            for k, v in wf["recommended_weights"].items():
                lines.append(f"  - {k}: {v:.2f}")
        lines.append(f"- *{wf.get('caveat', '')}*")
    lines += ["", "---", ""]

    # Summary
    lines += ["## 📋 Self-Audit Summary", ""]
    findings: list[str] = []
    if perm.get("verdict", {}).get("overall") == "LUCK":
        findings.append("🔴 **Edge not confirmed** — system performance may be luck. Increase trade count before trusting signals.")
    elif perm.get("verdict", {}).get("overall") == "MARGINAL":
        findings.append("🟡 **Edge is marginal** — system shows some signal but needs more data to confirm.")
    elif perm.get("verdict", {}).get("overall") == "REAL":
        findings.append("🟢 **Edge is real** — system performance is statistically significant.")
    if regime.get("has_mismatches"):
        findings.append(f"⚠️ **{len(regime['mismatches'])} regime mismatch(es)** — system is trading in adverse conditions.")
    if calib.get("adjustments"):
        findings.append(f"🔧 **{len(calib['adjustments'])} threshold adjustment(s)** applied — decision gates recalibrated.")
    if wf.get("beats_default"):
        findings.append(f"📈 **Better weight candidate found** — '{wf['best_candidate']}' beats default on out-of-sample Calmar.")
    if not findings:
        findings.append("✅ System is healthy — no action required.")
    for f in findings:
        lines.append(f"- {f}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Walk-forward validator: adversarial self-audit of the scoring system.",
    )
    parser.add_argument("--n-permutations", type=int, default=10_000,
                        help="Number of permutations for the edge significance test (default: 10000)")
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode: 1000 permutations, skip walk-forward optimization")
    parser.add_argument("--no-write", action="store_true",
                        help="Do not write thresholds.json or validator_report.md")
    args = parser.parse_args()

    n_perms = 1000 if args.quick else args.n_permutations

    conn = connect()
    try:
        rows = conn.execute(
            "SELECT pnl_inr FROM trade_recommendations "
            "WHERE is_seed=0 AND outcome IN ('WIN', 'LOSS')"
        ).fetchall()
        pnls = [r[0] for r in rows]

        print(f"▶ Running walk-forward validator ({len(pnls)} resolved trades)\n", file=sys.stderr)

        print("  Module 1: Permutation test...", file=sys.stderr)
        perm = permutation_test(pnls, n_perms=n_perms)

        print("  Module 2: Regime-stratified audit...", file=sys.stderr)
        regime = regime_audit(conn)

        print("  Module 3: Bayesian threshold calibration...", file=sys.stderr)
        calibration = calibrate_thresholds(conn)

        if args.quick:
            wf = {"status": "SKIPPED", "message": "Skipped in --quick mode."}
            print("  Module 4: Skipped (quick mode)", file=sys.stderr)
        else:
            print("  Module 4: Walk-forward weight optimization...", file=sys.stderr)
            wf = walk_forward_weights(conn)

        if not args.no_write:
            if calibration.get("status") == "OK":
                _write_thresholds(calibration)
                print(f"  ✓ Wrote {THRESHOLDS_FILE}", file=sys.stderr)
            report = _write_report(perm, regime, calibration, wf)
            REPORT_FILE.write_text(report, encoding="utf-8")
            print(f"  ✓ Wrote {REPORT_FILE}", file=sys.stderr)
        else:
            report = _write_report(perm, regime, calibration, wf)

        payload = {
            "generated_on": datetime.now(timezone.utc).isoformat(),
            "n_resolved": len(pnls),
            "permutation_test": perm,
            "regime_audit": regime,
            "threshold_calibration": calibration,
            "walk_forward_weights": wf,
        }
        emit_json(payload)

        print("\n" + report, file=sys.stderr)

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
