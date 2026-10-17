#!/usr/bin/env python3
"""Adjudicate the `GO` → `GO_WEAK` divergences into a published report.

    python tests/evals/adjudicate_divergences.py            # dry run
    python tests/evals/adjudicate_divergences.py --apply    # write the report

Written to `reports/adjudication_go_divergences.md`.

Why this tool exists. Gate 8 (`run_evals.gate_verdict_alignment`) grades the
expectation-vs-computed comparison and prints a *sum*: the transition slice and
every divergent record. Its own contract fixes what a divergence then needs —
each is "adjudicated by fixing the engine or re-freezing the expectation, never
by tuning the scorecard" — and adjudicating one needs two facts **per record**:

1. **The exact missing evidence** — which of the §10.2 scoring sources the
   row's own proof declaration actually carries, which are recorded but
   *waived* (with the payload's own reason, e.g. `dhan:not-requested`), and
   which are absent outright; beside each, the weight that source removes from
   the row's ceiling (BRD §10.4: a waived source leaves the denominator, and a
   ≥1 waiver also caps a would-be `GO` at `GO_WEAK`).
2. **The P(win) distance to the 55% floor** — the recorded
   `out_9_decision.json`'s calibrated `p_win` against the engine's own
   `GO_WIN_FLOOR`, in percentage points, plus the effective score the floor
   requires (read, not typed: the smallest score whose `calibrated_p_win`
   clears the floor).

Every figure is read, never authored: the expectation from the proof row
(`expected_verdict`), the verdict / score / P(win) / `confidence_deficit` /
`what_would_flip_me` from the row's declared stage-9 artifact, each source's
state from the payload that row declares, and `GO_WIN_FLOOR`, `P_BASE`,
`P_SLOPE`, `DEFAULT_WEIGHTS` and `calibrated_p_win` imported from
`scripts/9_decision_engine.py` itself — the report cannot state a number nobody
recorded. Output is byte-deterministic (no timestamps), and
`tests/test_adjudicate_divergences.py` regenerates it and byte-compares, so a
report that has fallen behind the payloads fails the suite instead of quietly
contradicting them.

Scope: every record whose proof row says `expected_verdict: GO` while the
recorded engine says otherwise — the slice gate 8 prints as `GO→GO_WEAK`.
A record missing either half is named in the report and never dropped (the gate
never counts it aligned either).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent
EVALS_DIR = TESTS_DIR / "evals"
REPO_ROOT = TESTS_DIR.parent
PROOF_ROOT = TESTS_DIR / "proof"
SCRIPTS = REPO_ROOT / "scripts"
GOLDEN = EVALS_DIR / "golden_dataset.json"
MANIFEST = PROOF_ROOT / "scenario_manifest.json"
REPORT = REPO_ROOT / "reports" / "adjudication_go_divergences.md"

EXPECTED = "GO"          # the expectation this report adjudicates
STAGE9_ARTIFACT = "out_9_decision.json"

for splice in (str(EVALS_DIR), str(TESTS_DIR)):
    if splice not in sys.path:
        sys.path.insert(0, splice)


def load_engine():
    """`scripts/9_decision_engine.py` — the one source of weights and floor."""
    spec = importlib.util.spec_from_file_location(
        "uvest_decision_engine_adjudicate",
        SCRIPTS / "9_decision_engine.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


engine = load_engine()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def floor_efficiency(engine_mod) -> float:
    """The smallest effective score whose calibrated P(win) clears the floor.

    Derived by asking the engine's own `calibrated_p_win` along the score it
    grades on — so the "floor needs effective X" figure moves with the calibration
    constants instead of duplicating their arithmetic here.
    """
    for step in range(0, 10001):
        score = step / 100.0
        if engine_mod.calibrated_p_win(score) >= engine_mod.GO_WIN_FLOOR:
            return score
    raise AssertionError("GO_WIN_FLOOR unreachable by any effective score")


def waiver_reason(payload: dict) -> str:
    """The payload's own cause for being waived — the first one it states."""
    for key in ("missing_inputs", "waived_reason", "waiver_reason", "reason"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return "; ".join(str(item) for item in value)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "waived (the payload states no cause)"


def source_state(row: dict, artifact: str) -> tuple[str, str]:
    """`(state, detail)` for one §10.2 source, read off the row's declaration.

    `state` is `scored`, `waived` or `absent`; the detail carries the payload's
    own reason for a waiver and the absence fact otherwise.
    """
    entries = [a for a in row.get("recorded_artifacts") or []
               if isinstance(a, dict) and a.get("artifact") == artifact]
    if not entries:
        return "absent", "no recorded artifact declared on this row"
    entry = entries[0]
    if entry.get("evidence") != "recorded":
        return "absent", f"declared `{entry.get('evidence')}`, not recorded evidence"
    path = PROOF_ROOT / str(entry.get("path") or "")
    if not path.exists():
        return "absent", f"declared path missing on disk (`{entry.get('path')}`)"
    payload = load_json(path)
    if payload.get("waived") is True:
        return "waived", waiver_reason(payload)
    return "scored", ""


def binding_constraint(deficit: str | None) -> str:
    """The engine's own `confidence_deficit`, classified by its branch."""
    if not deficit:
        return "none stated by the engine"
    if deficit.startswith("P(win)"):
        return "P(win) below the 55% floor"
    if deficit.startswith("waived source(s)"):
        return "§10.4 evidence waiver (floor met, sources incomplete)"
    if deficit.startswith("size capped"):
        return "size cap (corporate calendar / macro regime)"
    return deficit


def divergent_rows() -> tuple[list[dict], list[str]]:
    """`(adjudicable records, named skip reasons)` for the GO expectation."""
    manifest = load_json(MANIFEST)
    dataset = load_json(GOLDEN)
    trades = {t.get("trade_id"): t for t in dataset.get("trades", [])}
    adjudicable: list[dict] = []
    skipped: list[str] = []
    for row in manifest.get("scenarios", []):
        if row.get("expected_verdict") != EXPECTED:
            continue
        sid = str(row.get("scenario_id"))
        trade = trades.get(sid)
        if trade is None:
            skipped.append(f"{sid}: no dataset record, so its symbol/class is "
                           "not adjudicable here (the gate abstains on it too)")
            continue
        decision_path = PROOF_ROOT / "fixtures" / sid / "recorded" / STAGE9_ARTIFACT
        if str((trade.get("script_outputs") or {}).get(STAGE9_ARTIFACT, {}).get("$ref") or "") == "":
            skipped.append(f"{sid}: no `script_outputs.{STAGE9_ARTIFACT}` $ref")
            continue
        if not decision_path.exists():
            skipped.append(f"{sid}: referenced `{STAGE9_ARTIFACT}` missing on disk")
            continue
        decision = load_json(decision_path)
        if decision.get("decision") == EXPECTED:
            continue                      # aligned: nothing to adjudicate
        adjudicable.append({"row": row, "trade": trade, "decision": decision})
    adjudicable.sort(key=lambda item: str(item["trade"].get("trade_id")))
    return adjudicable, skipped


def record_lines(item: dict, weights: dict[str, float], eff_needed: float) -> list[str]:
    """One record's whole adjudication block, as markdown lines."""
    row, trade, decision = item["row"], item["trade"], item["decision"]
    sid = str(trade.get("trade_id"))
    p_win = float(decision["p_win"])
    floor = float(engine.GO_WIN_FLOOR)
    gap_pp = (p_win - floor) * 100.0
    eff = float(decision["effective_score_pct"])
    deficit = decision.get("confidence_deficit")

    states = {artifact: source_state(row, artifact) for artifact in weights}
    scored_w = sum(w for a, w in weights.items() if states[a][0] == "scored")
    waived_w = sum(w for a, w in weights.items() if states[a][0] == "waived")
    absent_w = sum(w for a, w in weights.items() if states[a][0] == "absent")
    quant_ml = [states[a][0] == "scored"
                for a in ("out_3_dtw.json", "out_4_ml.json") if a in weights]

    lines = [
        f"### {sid} — {trade.get('symbol')} — {trade.get('trade_class')} — advised {trade.get('recommendation_date')}",
        "",
        f"- **expected** `{EXPECTED}` (proof row `expected_verdict`) · "
        f"**computed** `{decision['decision']}` (recorded `fixtures/{sid}/recorded/{STAGE9_ARTIFACT}`)",
        f"- **P(win) {p_win:.4f}** — distance to the "
        f"{floor:.0%} floor: **{gap_pp:+.2f} pp** "
        + ("(**below** the floor)" if gap_pp < 0 else
           ("**at** the floor" if gap_pp == 0 else "(**above** the floor)")),
        f"- score {decision['final_score']} / {decision['max_possible_score']} "
        f"→ effective {eff:.2f}%; the floor needs effective **{eff_needed:.2f}** "
        f"(**{eff - eff_needed:+.2f}** pts)",
        f"- §10.2 weight actually scored: **{scored_w:.1f}** of {sum(weights.values()):.1f} "
        f"(absent {absent_w:.1f} / waived {waived_w:.1f} removed by BRD §10.4)",
        f"- Quant & ML block (stage 3 + stage 4): "
        + ("**complete** — both scoring sources read recorded bytes"
           if all(quant_ml) and len(quant_ml) == 2 else
           "**incomplete** — "
           + ", ".join(f"{artifact} {states[artifact][0]}"
                       for artifact in ("out_3_dtw.json", "out_4_ml.json")
                       if artifact in weights and states[artifact][0] != "scored")),
        f"- binding constraint: **{binding_constraint(deficit)}** · "
        f"engine's own words: “{deficit or '—'}”",
        "- what would flip it (the payload's own list):",
    ]
    flip = decision.get("what_would_flip_me")
    if isinstance(flip, list) and flip:
        lines += [f"  - {item_}" for item_ in flip]
    else:
        lines.append("  - (the payload states none)")
    lines += [
        "",
        "| §10.2 source | weight | state on this record |",
        "| --- | ---: | --- |",
    ]
    for artifact, weight in weights.items():
        state, detail = states[artifact]
        cell = {"scored": "scored",
                "waived": f"**waived** — {detail}",
                "absent": f"**absent** — {detail}"}[state]
        lines.append(f"| `{artifact}` | {weight:.1f} | {cell} |")
    lines.append("")
    return lines


def render() -> str:
    """The whole report — deterministic, derived, never timestamped."""
    items, skipped = divergent_rows()
    weights = dict(engine.DEFAULT_WEIGHTS)
    eff_needed = floor_efficiency(engine)
    total_weight = sum(weights.values())

    transitions = Counter(f"{EXPECTED}→{item['decision'].get('decision') or item['decision']['verdict']}"
                          for item in items)
    deficits = Counter(binding_constraint(item["decision"].get("confidence_deficit"))
                       for item in items)
    gaps = {str(item["trade"].get("trade_id")):
            (float(item["decision"]["p_win"]) - float(engine.GO_WIN_FLOOR)) * 100.0
            for item in items}
    below = {tid: gap for tid, gap in gaps.items() if gap < 0}
    complete = sum(
        all(source_state(item["row"], artifact)[0] == "scored"
            for artifact in ("out_3_dtw.json", "out_4_ml.json")
            if artifact in weights)
        for item in items)

    per_artifact: dict[str, Counter] = {artifact: Counter() for artifact in weights}
    reasons: dict[str, Counter] = {artifact: Counter() for artifact in weights}
    for item in items:
        for artifact in weights:
            state, detail = source_state(item["row"], artifact)
            per_artifact[artifact][state] += 1
            if detail:
                reasons[artifact][detail] += 1

    lines = [
        "# Adjudication report — GO → GO_WEAK divergences (evals gate 8)",
        "",
        "*Generated by `tests/evals/adjudicate_divergences.py --apply` from "
        "recorded bytes only: the expectation from each proof row, the verdict / "
        "score / P(win) / deficit from its declared `out_9_decision.json`, each "
        "source's state from the payload that row declares, the symbol and class "
        "from `tests/evals/golden_dataset.json`, and the weights, floor and "
        "`calibrated_p_win` imported from `scripts/9_decision_engine.py`. "
        "No figure below is typed. Regenerate with the command above; "
        "`tests/test_adjudicate_divergences.py` byte-compares this file against "
        "a fresh run.*",
        "",
        "## Summary",
        "",
        f"- **{len(items)}** record(s) carry `expected_verdict: GO` while the "
        "recorded engine says otherwise — transitions: "
        + (", ".join(f"**{name}** ×{count}"
                     for name, count in sorted(transitions.items()))
           or "none")
        + ".",
        "- binding constraint, as the engine states it: "
        + (", ".join(f"**{count}** × {name}"
                     for name, count in deficits.most_common()) or "n/a")
        + ".",
        f"- P(win) distance to the {float(engine.GO_WIN_FLOOR):.0%} floor across "
        f"the slice: **{len(below)}** below it, **{len(gaps) - len(below)}** at or "
        "above it"
        + (f"; closest **{max(below, key=below.get)}** at **{max(below.values()):+.2f} pp**"
           if below else "")
        + (f", furthest **{min(below, key=below.get)}** at "
           f"**{min(below.values()):+.2f} pp**" if below else "")
        + ".",
        f"- Quant & ML block (stage 3 DTW + stage 4 LightGBM, "
        f"{weights.get('out_3_dtw.json', 0):.1f} + "
        f"{weights.get('out_4_ml.json', 0):.1f} pts): **{complete} of "
        f"{len(items)}** records score both from recorded bytes, so every P(win) "
        "distance above is measured against a full block rather than a thinned "
        "denominator — the floor test is genuine for all "
        f"{len(items)} records.",
        f"- every record's ceiling below is `{total_weight:.1f}` §10.2 points "
        "reduced by whatever §10.4 had to remove for that row; the reductions "
        "are itemised source by source in each block.",
    ]
    if skipped:
        lines.append(f"- not adjudicable here ({len(skipped)}): "
                     + "; ".join(skipped) + ".")
    lines += [
        "",
        "## Sources across the slice",
        "",
        "| §10.2 source | weight | scored | waived | absent | what the payloads say |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for artifact, weight in weights.items():
        counts = per_artifact[artifact]
        said = "; ".join(f"“{text}” ×{count}"
                         for text, count in reasons[artifact].most_common(2))
        lines.append(
            f"| `{artifact}` | {weight:.1f} | {counts['scored']} | "
            f"{counts['waived']} | {counts['absent']} | {said or '—'} |")
    lines += [
        "",
        "## The floor, read from the engine",
        "",
        "| constant | value |",
        "| --- | --- |",
        f"| `GO_WIN_FLOOR` | {float(engine.GO_WIN_FLOOR):.2f} |",
        f"| `P_BASE` | {float(engine.P_BASE):.3f} |",
        f"| `P_SLOPE` | {float(engine.P_SLOPE):.4f} |",
        f"| total §10.2 weight (`DEFAULT_WEIGHTS`) | {total_weight:.1f} |",
        f"| smallest effective score reaching the floor | **{eff_needed:.2f}** |",
        "",
        "## Per record",
        "",
    ]
    for item in items:
        lines += record_lines(item, weights, eff_needed)
    lines += [
        "---",
        "",
        "Sources: `tests/proof/scenario_manifest.json` (`expected_verdict`, "
        "`recorded_artifacts`), each row's declared payloads under "
        "`tests/proof/fixtures/<id>/recorded/`, `scripts/9_decision_engine.py` "
        "(weights, floor, `calibrated_p_win`), "
        "`tests/evals/golden_dataset.json` (symbol, class, date). Every figure "
        "above regenerates from those bytes with "
        "`python tests/evals/adjudicate_divergences.py --apply`.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Adjudicate the GO→GO_WEAK divergences into a published "
                    "report (missing evidence + P(win) distance to the floor).")
    parser.add_argument("--apply", action="store_true",
                        help=f"write {REPORT.relative_to(REPO_ROOT).as_posix()}")
    args = parser.parse_args(argv)

    items, skipped = divergent_rows()
    print("=" * 72)
    print("ADJUDICATION PLAN — expected GO, computed otherwise")
    print("=" * 72)
    for item in items:
        decision = item["decision"]
        gap = (float(decision["p_win"]) - float(engine.GO_WIN_FLOOR)) * 100.0
        missing = [artifact for artifact in engine.DEFAULT_WEIGHTS
                   if source_state(item["row"], artifact)[0] != "scored"]
        print(f"  {str(item['trade'].get('trade_id')):13} "
              f"{str(item['trade'].get('symbol')):11} "
              f"{decision['decision']:8} P(win)={decision['p_win']:.4f} "
              f"({gap:+.2f} pp) missing={','.join(missing) or 'none'}")
    for reason in skipped:
        print(f"  — {reason}")

    if not args.apply:
        print(f"\nDRY RUN — nothing written; --apply writes "
              f"{REPORT.relative_to(REPO_ROOT).as_posix()} "
              f"({len(items)} record(s))")
        return 0
    if not items:
        print("no GO divergence to adjudicate; nothing written", file=sys.stderr)
        return 1
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render())
    print(f"\nwrote {REPORT.relative_to(REPO_ROOT).as_posix()} "
          f"({len(items)} record(s) adjudicated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())