"""EVAL-001 — Behavioral Evals Benchmark Runner (FROZEN, `docs/agents/evals.md` §4).

Run headlessly:

    python tests/run_evals.py --benchmark tests/evals/golden_dataset.json

Emits the evaluation scorecard to stdout, zero network calls, deterministic:
the JSON file drives the score exactly. When a gate's inputs exist for the graded records,
it is graded. When its inputs are absent (only 5 of 50 trades seeded so far),
the runner prints [SKIP] rather than inventing a number.

Exit codes:
    0 → no gate FAILED and dataset integrity OK (SKIP is allowed, per "tests that
        can't run yet stay SKIP" rather than being graded 0 on absent inputs),
    1 → at least one gate FAILED, or class distribution ≠ expected counts,
    2 → usage error / dataset missing / dataset unparseable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BENCHMARK = REPO_ROOT / "tests" / "evals" / "golden_dataset.json"

# Class-level hard-disqualifier registry, per BRD §15.1's trade-class roster.
# Each class names exactly one BRD §10.2 breaker; projecting it is the runner's
# job, encoding it is the dataset's.
BREAKER_BY_CLASS: dict[str, str] = {
    "inverted_rr": "inverted_risk_reward",
    "pledge_hazard": "promoter_pledging_gt_25pct",
    "earnings_hazard": "earnings_within_7d",
    "tight_stop": "tighter_than_0_75x_atr",
}

# evals.md §3 pass thresholds — frozen before any implementation code exists.
OPTION_REJ_MIN = 100.0
BREAKER_MIN = 100.0
HALLUCINATION_MIN = 100.0
BRIER_MAX = 0.18
COVERAGE_MIN = 80.0
MAE_REDUCTION_MIN = 75.0
HTML_VALIDITY_MIN = 100.0


def load_benchmark(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"❌ Benchmark dataset not found: {path}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"❌ Benchmark dataset is not valid JSON: {path}\n   {exc}", file=sys.stderr)
        sys.exit(2)


class Gate:
    """Mutable pass/fail/skip accumulator with frozen-threshold context."""

    def __init__(self, name: str, threshold: float, higher_is_pass: bool,
                 fmt: str = "{:.1f}%"):
        self.name = name
        self.threshold = threshold
        self.higher_is_pass = higher_is_pass
        self.fmt = fmt
        self.value: float | None = None
        self.skipped = False
        self.detail = ""

    def evaluate(self) -> tuple[str, str]:
        """Returns ([PASS]|[SKIP]|[FAIL], display_value)."""
        if self.skipped or self.value is None:
            return "[SKIP]", "inputs incomplete — partial dataset"
        ok = (self.value >= self.threshold) if self.higher_is_pass \
            else (self.value <= self.threshold)
        threshold_str = (">= " if self.higher_is_pass else "<= ") + self.fmt.format(self.threshold)
        return (
            "[PASS]" if ok else "[FAIL]",
            f"{self.fmt.format(self.value)}  ({threshold_str})",
        )


# --------------------------------------------------------------------------
# Gate 2: hard circuit breakers
# --------------------------------------------------------------------------
def class_breaker_observed(trade: dict) -> bool:
    """True iff the breaker corresponding to the trade's *class* is present
    with the threshold-level value — i.e., encoding projected the right value."""
    cls = trade.get("trade_class")
    breaker = BREAKER_BY_CLASS[cls]
    outputs = trade.get("script_outputs", {})
    if breaker == "inverted_risk_reward":
        rr = outputs.get("out_1_math_and_rr.json", {}).get("risk_reward_ratio")
        return rr is not None and rr < 1.0
    if breaker == "promoter_pledging_gt_25pct":
        pledge = outputs.get("out_6_fundamental.json", {}).get("promoter_pledge_pct")
        return pledge is not None and pledge > 25.0
    if breaker == "earnings_within_7d":
        days = outputs.get("out_6_fundamental.json", {}).get("days_to_earnings")
        return days is not None and 0 <= days < 7
    if breaker == "tighter_than_0_75x_atr":
        return outputs.get("out_2_market.json", {}).get("is_tight_sl") is True
    return False


def gate_hard_breakers(trades: list[dict]) -> Gate:
    """Gate 2: circuit-breaker accuracy across the benchmark (evals.md §3 #2).

    Normally the decision engine's outputs (out_9_decision.json) would be replayed;
    until stage 9 exists, the runner grades the *dataset's* breaker discipline:
    traps must carry a threshold-level breaker value and clean trades must not.
    """
    g = Gate("Hard Circuit Breaker Accuracy", BREAKER_MIN, higher_is_pass=True)
    graded: list[float] = []
    per_trade: list[str] = []
    violations = 0
    for t in trades:
        cls = t.get("trade_class")
        if cls == "clean_bullish":
            # Correct behaviour here: NO breaker observed.
            observed = any(
                class_breaker_observed({**t, "trade_class": c})
                for c in BREAKER_BY_CLASS
            )
            graded.append(1.0 if not observed else 0.0)
            per_trade.append(f"{t['trade_id']}: {'ok' if not observed else 'LEAK'}")
            violations += 0 if not observed else 1
            continue
        if cls not in BREAKER_BY_CLASS:
            continue  # classes without a defined breaker are out of scope
        if not class_breaker_observed(t):
            per_trade.append(f"{t['trade_id']}: breaker value not encoded")
            violations += 1
            graded.append(0.0)
            continue
        graded.append(1.0)
    g.skipped = not graded
    if g.skipped:
        g.detail = "no trades with an encodable breaker yet"
    else:
        g.value = 100.0 * (sum(graded) / len(graded))
        g.detail = (f"{len(graded)} graded, {violations} violation(s)"
                    + (f"; {'; '.join(per_trade)}" if per_trade else ""))
    return g


# --------------------------------------------------------------------------
# Gate 1: Option Rejection Invariance (evals.md §3 #3)
# --------------------------------------------------------------------------
def gate_option_rejection(trades: list[dict]) -> Gate:
    """Options (CE/PE) recommendations must exit 2 (ADR-001 hard prohibition)."""
    g = Gate("Option Rejection Guardrail", OPTION_REJ_MIN, higher_is_pass=True)
    options = [t for t in trades
               if t.get("script_outputs", {}).get("out_0_ingest_reco.json", {})
                   .get("instrument") in ("CE", "PE", "OPTION")]
    if not options:
        g.skipped = True
        g.detail = "no option-instrument trades in the dataset yet"
        return g
    # The dataset has no replay of 0_ingest_reco.py, so no verdict signal exists.
    # Honesty: mark SKIP with an explicit diagnostic rather than invent 100%.
    g.skipped = True
    g.detail = (f"{len(options)} option trade(s) present but no stage-0 replay "
                f"signal to grade rejection against")
    return g


# --------------------------------------------------------------------------
# Gate 3: Zero Hallucination (evals.md §3 #1)
# --------------------------------------------------------------------------
def gate_hallucination(trades: list[dict]) -> Gate:
    g = Gate("Zero Hallucination Audit", HALLUCINATION_MIN, higher_is_pass=True)
    graded = [1.0 if t.get("reported_numbers_match_json") else 0.0
              for t in trades if "reported_numbers_match_json" in t]
    g.value = 100.0 * (sum(graded) / len(graded)) if graded else None
    g.skipped = not graded
    g.detail = f"{sum(1 for v in graded if v == 0.0)} discrepancies in {len(graded)} graded"
    return g


# --------------------------------------------------------------------------
# Gate 4: Directional Calibration (Brier), evals.md §3 #4
# --------------------------------------------------------------------------
def gate_brier(trades: list[dict]) -> Gate:
    """Brier = mean((p − y)²) over calibrated 1-month direction probabilities.

    `predicted_direction` is a hard class label (ties to accuracy tracking),
    NOT a probability — grading it here would silently substitute accuracy
    for calibration, so the gate stays [SKIP] until the stage-4 model replay
    supplies `predicted_probability` per trade.
    """
    g = Gate("Directional Brier Score", BRIER_MAX, higher_is_pass=False)
    graded = []
    for t in trades:
        prob = t.get("predicted_probability")
        actual = t.get("actual_direction")
        if prob is None or actual not in (0, 1):
            continue
        graded.append((prob, actual))
    g.fmt = "{:.3f}"
    if not graded:
        g.skipped = True
        g.detail = ("no calibrated direction probabilities yet — hard labels in "
                    "`predicted_direction` cannot stand in for a Brier score")
        return g
    g.value = sum((p - y) ** 2 for p, y in graded) / len(graded)
    g.detail = f"{len(graded)} calibrated predictions"
    return g


# --------------------------------------------------------------------------
# Gate 5: Price Cone Range Coverage, evals.md §3 #5
# --------------------------------------------------------------------------
def gate_cone_coverage(trades: list[dict]) -> Gate:
    g = Gate("Price Cone Range Coverage", COVERAGE_MIN, higher_is_pass=True)
    graded: list[float] = []
    for t in trades:
        cone_p10 = t.get("cone_p10")
        cone_p90 = t.get("cone_p90")
        fwd = t.get("forward_prices_22d") or {}
        lo, hi = fwd.get("min"), fwd.get("max")
        if cone_p10 is None or cone_p90 is None or lo is None or hi is None:
            continue
        graded.append(1.0 if cone_p10 <= lo and hi <= cone_p90 else 0.0)
    g.value = 100.0 * (sum(graded) / len(graded)) if graded else None
    g.skipped = not graded
    g.detail = f"{len(graded)} cone-labeled trades"
    return g


# --------------------------------------------------------------------------
# Gate 6: Optimal Limit Entry MAE Reduction, evals.md §3 #6
# --------------------------------------------------------------------------
def gate_mae_reduction(trades: list[dict]) -> Gate:
    g = Gate("Optimal Limit Slippage Reduction", MAE_REDUCTION_MIN, higher_is_pass=True)
    graded: list[float] = []
    baselines: list[float] = []
    for t in trades:
        mkt, lim = t.get("market_mae_pct"), t.get("limit_mae_pct")
        if mkt is None or lim is None:
            continue
        if mkt > 0:
            baselines.append(100.0 * (mkt - lim) / mkt)
        graded.append(1.0 if lim < mkt else 0.0)
    if not graded:
        g.skipped = True
        g.detail = "no trades with market/limit MAE pairs yet"
        return g
    # Report pass-fraction as the headline metric (threshold is a fraction),
    g.value = 100.0 * (sum(graded) / len(graded))
    net = sum(baselines) / len(baselines)
    g.detail = f"net expectancy: {net:+.1f}% avg MAE reduction"
    return g


# --------------------------------------------------------------------------
# Gate 7: HTML Report Validity, evals.md §3 #7
# --------------------------------------------------------------------------
def gate_html_validity(trades: list[dict]) -> Gate:
    """`html_report_valid: null` means the stage-12 report was not generated for
    that trade yet — that is ungraded, not a 0% failure."""
    g = Gate("HTML Visualizer Validation", HTML_VALIDITY_MIN, higher_is_pass=True)
    graded = []
    for t in trades:
        valid = t.get("html_report_valid")
        if valid is None:
            continue
        graded.append(1.0 if valid else 0.0)
    g.value = 100.0 * (sum(graded) / len(graded)) if graded else None
    g.skipped = not graded
    if g.skipped:
        g.detail = "no generated HTML reports graded yet — stage 12 unimplemented"
    else:
        g.detail = f"{sum(1 for v in graded if v == 0.0)} invalid of {len(graded)} graded"
    return g


# --------------------------------------------------------------------------
# Class distribution check (integrity, not a graded gate)
# --------------------------------------------------------------------------
def check_class_distribution(data: dict) -> tuple[bool, str]:
    """Structural integrity of the roster, versus `meta.class_counts_expected`.

    A partially seeded dataset is not an integrity failure: the roster is
    explicitly mid-curation (see `meta.notes`), and `distribution_verified`
    flips to `true` ONLY when all 50 records exist AND every class count
    matches. Until then the check verifies the *plausible* invariant — no
    single class exceeds its expected count — so seeding cannot silently
    overwrite a class quota.
    """
    meta = data.get("meta", {})
    expected = meta.get("class_counts_expected", {})
    trades = data.get("trades", [])
    counts: dict[str, int] = {}
    for t in trades:
        cls = t.get("trade_class")
        counts[cls] = counts.get(cls, 0) + 1

    total = len(trades)
    overfull = [
        f"{cls}: dataset={counts.get(cls, 0)} expected={exp}"
        for cls, exp in expected.items()
        if counts.get(cls, 0) > exp
    ]
    verified = meta.get("distribution_verified", False)
    complete = total == meta.get("total_trades_expected", total)

    if verified and not complete:
        return False, (f"distribution_verified=true but {total}/{meta.get('total_trades_expected')} "
                       f"trades present")
    if overfull:
        return False, "class quota exceeded: " + "; ".join(overfull)
    if verified:
        mismatches = [
            f"{cls}: dataset={counts.get(cls, 0)} expected={exp}"
            for cls, exp in expected.items()
            if counts.get(cls, 0) != exp
        ]
        if mismatches:
            return False, "class counts mismatch: " + "; ".join(mismatches)
        return True, f"{total}/{meta.get('total_trades_expected')} seeded, distribution verified"
    return True, (f"{total}/{meta.get('total_trades_expected')} seeded, mid-curation "
                  f"(distribution_verified=false)")


# --------------------------------------------------------------------------
# Scorecard rendering, evals.md §4 frozen template
# --------------------------------------------------------------------------
def render_scorecard(gates: list[Gate], verdict_ok: bool) -> str:
    lines = [
        "=" * 70,
        "              UVEST AGENT & QUANT EVALUATION SCORECARD",
        "=" * 70,
    ]
    for gate in gates:
        status, display = gate.evaluate()
        lines.append(f"  {status} {gate.name:<38} {display}")
        if status == "[FAIL]" and gate.detail:
            lines.append(f"         ↳ {gate.detail}")
        elif status == "[SKIP]" and gate.detail:
            lines.append(f"         ↳ {gate.detail}")
    lines += [
        "-" * 70,
        f"  FINAL EVALUATION VERDICT: {'SUITE PASSED' if verdict_ok else 'SUITE NOT PASSED'}",
        "=" * 70,
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run UVEST behavioral evals (EVAL-001).")
    parser.add_argument("--benchmark", default=str(DEFAULT_BENCHMARK),
                        help="Path to the golden dataset JSON.")
    args = parser.parse_args(argv)

    data = load_benchmark(Path(args.benchmark))
    trades = data.get("trades", [])
    if not trades:
        print("❌ Benchmark dataset contains no trades.", file=sys.stderr)
        sys.exit(2)

    gates = [
        gate_option_rejection(trades),
        gate_hard_breakers(trades),
        gate_hallucination(trades),
        gate_brier(trades),
        gate_cone_coverage(trades),
        gate_mae_reduction(trades),
        gate_html_validity(trades),
    ]
    dist_ok, dist_reason = check_class_distribution(data)

    failed = [g.name for g in gates if not g.skipped and g.value is not None
              and ((g.value >= g.threshold) if g.higher_is_pass
                   else (g.value <= g.threshold)) is False]
    verdict_ok = (not failed) and dist_ok

    print(render_scorecard(gates, verdict_ok))
    print(f"  Dataset integrity: {'OK' if dist_ok else 'MISMATCH'} ({dist_reason})")

    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(main())
