"""EVAL-001 — Behavioral Evals Benchmark Runner (FROZEN, `docs/agents/evals.md` §4).

Run headlessly:

    python tests/run_evals.py --benchmark tests/evals/golden_dataset.json

Emits the evaluation scorecard to stdout, zero network calls, deterministic:
the JSON file drives the score exactly. When a gate's inputs exist for the graded records,
it is graded. When its inputs are absent (only 5 of 50 trades seeded so far),
the runner prints [SKIP] rather than inventing a number.

Record payloads may be references (`$ref`, see `tests/evals/dataset_refs.py`) into
the proof root, which is where those bytes are frozen. A referenced artifact whose
provenance is `quarantined` is disclosed and excluded from grading — unverified
legacy bytes are never treated as evidence (E5, scenario-manifest-schema.md).

Outcome claims may instead be `$derive` objects naming a pinned forward tape and a
frozen rule (`tests/evals/dataset_refs.py`); the runner recomputes them from the
tape's own bars at load and prints every derivation on the scorecard.

Exit codes:
    0 → no gate FAILED and dataset integrity OK (SKIP is allowed, per "tests that
        can't run yet stay SKIP" rather than being graded 0 on absent inputs),
    1 → at least one gate FAILED, or class distribution ≠ expected counts,
    2 → usage error / dataset missing / dataset unparseable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BENCHMARK = REPO_ROOT / "tests" / "evals" / "golden_dataset.json"

sys.path.insert(0, str(Path(__file__).resolve().parent / "evals"))

import dataset_refs  # noqa: E402  (sibling eval module, spliced onto the path above)
import report_diff  # noqa: E402

# Class-level hard-disqualifier registry, per BRD §15.1's trade-class roster.
# Each class names exactly one BRD §10.3 breaker; projecting it is the runner's
# job, encoding it is the dataset's.
BREAKER_BY_CLASS: dict[str, str] = {
    "inverted_rr": "inverted_risk_reward",
    "pledge_hazard": "promoter_pledging_gt_25pct",
    "earnings_hazard": "earnings_within_7d",
    "tight_stop": "tighter_than_0_75x_atr",
}

# Which artifact each breaker reads — needed to know whether that breaker's
# evidence is even gradeable (a quarantined artifact carries no evidence).
BREAKER_ARTIFACT: dict[str, str] = {
    "inverted_risk_reward": "out_1_math.json",
    "promoter_pledging_gt_25pct": "out_6_fundamental.json",
    "earnings_within_7d": "out_6_fundamental.json",
    "tighter_than_0_75x_atr": "out_2_market.json",
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
def class_breaker_observed(trade: dict, breaker: str | None = None) -> bool | None:
    """True iff the breaker corresponding to the trade's *class* is present
    with the threshold-level value — i.e., encoding projected the right value.

    `None` means *ungradeable*, never *clean*: the breaker's artifact is
    registered quarantined, so its bytes are legacy debt kept on disk for
    migration continuity and contribute nothing to a gate.
    """
    cls = trade.get("trade_class")
    breaker = breaker or BREAKER_BY_CLASS[cls]
    if BREAKER_ARTIFACT[breaker] in dataset_refs.quarantined_in(trade):
        return None
    outputs = trade.get("script_outputs", {})
    if breaker == "inverted_risk_reward":
        # Stage 1 (TICK-002) emits BOTH frozen fields: `raw_rr` at the
        # recommended band midpoint, `live_rr` at the executable price. The
        # breaker is a property of each, not just one — a raw_rr < 1.0 record
        # can never recover at any execution price, while live_rr < 1.0 means
        # the slippage has already eaten the edge. Either trips (OR per the
        # BRD §10.2 #1 "R:R" breaker, both are 'R:R').
        stage1 = outputs.get("out_1_math.json", {})
        raw_rr = stage1.get("raw_rr")
        # live_rr carries the trap-encoding in the golden dataset (the
        # executable reality); older records may predate the pair-splitting.
        live_rr = stage1.get("live_rr", raw_rr)
        return (raw_rr is not None and raw_rr < 1.0) or (
            live_rr is not None and live_rr < 1.0
        )
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
    ungraded: list[str] = []
    violations = 0
    for t in trades:
        cls = t.get("trade_class")
        if cls == "clean_bullish":
            # Correct behaviour here: NO breaker observed. A breaker whose own
            # evidence is quarantined abstains rather than counting either way.
            verdicts = [class_breaker_observed({**t, "trade_class": c}, BREAKER_BY_CLASS[c])
                        for c in BREAKER_BY_CLASS]
            observed = [v for v in verdicts if v is not None]
            if not observed:
                ungraded.append(f"{t['trade_id']}: every breaker artifact quarantined")
                continue
            leaked = any(observed)
            graded.append(0.0 if leaked else 1.0)
            per_trade.append(f"{t['trade_id']}: {'LEAK' if leaked else 'ok'}")
            violations += 1 if leaked else 0
            continue
        if cls not in BREAKER_BY_CLASS:
            continue  # classes without a defined breaker are out of scope
        verdict = class_breaker_observed(t)
        if verdict is None:
            ungraded.append(
                f"{t['trade_id']}: {BREAKER_ARTIFACT[BREAKER_BY_CLASS[cls]]} quarantined")
            continue
        if not verdict:
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
                    + (f"; {'; '.join(per_trade)}" if per_trade else "")
                    + (f"; not graded (quarantined evidence): {'; '.join(ungraded)}"
                       if ungraded else ""))
    return g


# --------------------------------------------------------------------------
# Gate 1: Option Rejection Invariance (evals.md §3 #3)
# --------------------------------------------------------------------------
def gate_option_rejection(trades: list[dict]) -> Gate:
    """Options (CE/PE) recommendations must exit 2 (ADR-001 hard prohibition)."""
    g = Gate("Option Rejection Guardrail", OPTION_REJ_MIN, higher_is_pass=True)
    options = [
        t for t in trades
        if (
            t.get("script_outputs", {}).get("current_trade.json", {}).get("instrument_type")
            or t.get("script_outputs", {}).get("current_trade.json", {}).get("type")
            or t.get("instrument")
        ) in ("CE", "PE", "OPT", "OPTION", "CALL", "PUT")
    ]

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
def report_path_for(trade: dict) -> Path:
    """The stage-12 artifact name of a record (BRD §13, TICK-010's template).

    `reports/report_{SYMBOL}_{DATE}.html`, DATE being the recommendation date
    as YYYYMMDD — the one name the contract pins, so the gate, the visualizer
    and the differ all meet at the same file.
    """
    symbol = str(trade.get("symbol") or "").upper()
    day = str(trade.get("recommendation_date") or "").replace("-", "")
    return REPO_ROOT / "reports" / f"report_{symbol}_{day}.html"


def gate_hallucination(trades: list[dict], reports_dir: Path | None = None) -> Gate:
    """Every number a report shows must trace to that record's own JSON.

    The retired `reported_numbers_match_json` boolean was a curator's *claim*
    about a report; `tests/evals/report_diff.py` is the check itself, run here
    against the stage-12 artifact whenever one has been generated. A record
    with no report is ungraded (like the HTML-validity gate): absence of a
    report is not evidence of honesty.
    """
    g = Gate("Zero Hallucination Audit", HALLUCINATION_MIN, higher_is_pass=True)
    reports = reports_dir or (REPO_ROOT / "reports")
    graded: list[float] = []
    per_trade: list[str] = []
    ungraded: list[str] = []
    for t in trades:
        path = reports / report_path_for(t).name
        if not path.exists():
            ungraded.append(str(t.get("trade_id")))
            continue
        problems = report_diff.diff(
            path.read_text(encoding="utf-8", errors="replace"), t)
        graded.append(1.0 if not problems else 0.0)
        if problems:
            shown = "; ".join(problems[:3])
            more = f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""
            per_trade.append(f"{t.get('trade_id')}: {shown}{more}")
    g.skipped = not graded
    if g.skipped:
        g.detail = (f"no stage-12 reports generated yet — regenerate with "
                    "tests/evals/report_regen.py; the differ stands over "
                    f"reports/report_{{SYMBOL}}_{{DATE}}.html "
                    f"({len(ungraded)} record(s) without a report)")
    else:
        g.value = 100.0 * (sum(graded) / len(graded))
        g.detail = (f"{len(graded)} graded, "
                    f"{sum(1 for v in graded if v == 0.0)} with discrepancies"
                    + (f"; {'; '.join(per_trade)}" if per_trade else "")
                    + (f"; not graded (no report generated): {len(ungraded)}"
                       if ungraded else ""))
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
    g.detail = (f"{len(graded)} cone-labeled trades" if graded
                else "no trades with an engine-produced cone envelope yet — "
                     "stage 4 unimplemented")
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
        g.detail = ("no market/limit MAE pairs with an engine-derived limit entry "
                    "yet — stage 8 unimplemented")
        return g
    # Report pass-fraction as the headline metric (threshold is a fraction),
    g.value = 100.0 * (sum(graded) / len(graded))
    net = sum(baselines) / len(baselines)
    g.detail = f"net expectancy: {net:+.1f}% avg MAE reduction"
    return g


# --------------------------------------------------------------------------
# Gate 7: HTML Report Validity, evals.md §3 #7
# --------------------------------------------------------------------------
# Any element carrying a src/href pulls the report off its own bytes — a
# missing stylesheet, a broken image, a CDN. `data:`, `#fragment` and
# `mailto:` targets are self-contained and legal.
_EXTERNAL_REF_RE = re.compile(
    r"<(img|source|video|audio|iframe|frame|embed|object|script|link|track|applet)\b"
    r"[^>]*\b(?:src|href)\s*=\s*[\"']?([^\"'\s>]+)",
    re.IGNORECASE,
)


def html_validity_problems(text: str) -> list[str]:
    """The headlessly-checkable half of the frozen §3 #7 criterion:

    HTML5 doctype, zero external resources ("0 network dependencies"),
    self-contained styling, and at least one inline SVG chart. The 300 ms
    render budget is a browser property and is deliberately NOT graded here —
    an unchecked criterion is disclosed by this docstring, never claimed pass.
    """
    problems: list[str] = []
    if not text.lstrip()[:15].lower().startswith("<!doctype html"):
        problems.append("missing <!doctype html> HTML5 doctype")
    for match in _EXTERNAL_REF_RE.finditer(text):
        target = match.group(2)
        if not target.lower().startswith(("data:", "#", "mailto:")):
            problems.append(
                f"external resource <{match.group(1)} src={target[:40]}>")
    if re.search(r"https?://", text, re.IGNORECASE):
        problems.append("network URL present — 0 network dependencies required")
    lowered = text.lower()
    if "<style" not in lowered:
        problems.append("no inline <style> block — CSS must be self-contained")
    if "<svg" not in lowered:
        problems.append("no inline <svg> chart")
    return problems


def gate_html_validity(trades: list[dict], reports_dir: Path | None = None) -> Gate:
    """The stage-12 artifact itself must be standalone HTML5 (evals.md §3 #7).

    The retired `html_report_valid` boolean was a curator's *claim* about a
    report (ADR-007: the gate stays graded, the flag is gone). The check now
    reads the artifact at the canonical path — the same
    `reports/report_{SYMBOL}_{DATE}.html` the Zero-Hallucination differ grades
    — and validates what can be validated without a browser. A record without
    a generated report is ungraded, not a pass: absence is not evidence.
    """
    g = Gate("HTML Visualizer Validation", HTML_VALIDITY_MIN, higher_is_pass=True)
    reports = reports_dir or (REPO_ROOT / "reports")
    graded: list[float] = []
    per_trade: list[str] = []
    ungraded: list[str] = []
    for t in trades:
        path = reports / report_path_for(t).name
        if not path.exists():
            ungraded.append(str(t.get("trade_id")))
            continue
        problems = html_validity_problems(
            path.read_text(encoding="utf-8", errors="replace"))
        graded.append(1.0 if not problems else 0.0)
        if problems:
            shown = "; ".join(problems[:3])
            more = f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""
            per_trade.append(f"{t.get('trade_id')}: {shown}{more}")
    g.skipped = not graded
    if g.skipped:
        g.detail = (f"no stage-12 reports generated yet — regenerate with "
                    f"tests/evals/report_regen.py ({len(ungraded)} record(s) "
                    "without a report)")
    else:
        g.value = 100.0 * (sum(graded) / len(graded))
        g.detail = (f"{len(graded)} graded, "
                    f"{sum(1 for v in graded if v == 0.0)} invalid"
                    + (f"; {'; '.join(per_trade)}" if per_trade else "")
                    + (f"; not graded (no report generated): {len(ungraded)}"
                       if ungraded else ""))
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

    raw = load_benchmark(Path(args.benchmark))
    try:
        data, ref_notes, derive_notes = dataset_refs.materialize(raw)
    except dataset_refs.RefError as exc:
        print(f"❌ Benchmark dataset has an unresolvable reference/derivation: {exc}",
              file=sys.stderr)
        sys.exit(2)
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
    if ref_notes or derive_notes:
        quarantined = sum(1 for note in ref_notes if note.endswith("(quarantined)"))
        suffix = (f" ({quarantined} quarantined — excluded from grading)"
                  if quarantined else "")
        print(f"  Artifact refs resolved: {len(ref_notes)} artifact ref(s), "
              f"{len(derive_notes)} derived field(s) from tests/proof{suffix}")
        for note in [*ref_notes, *derive_notes]:
            print(f"    ↳ {note}")

    return 0 if verdict_ok else 1


if __name__ == "__main__":
    sys.exit(main())
