#!/usr/bin/env python3
"""Shape-ladder campaign — spread each geometry class across its shape family.

    python tests/evals/reshape_records.py                        # dry run: the plan
    python tests/evals/reshape_records.py --apply

**The finding this answers.** Gate 2's rule-exercise census counts the distinct
`(arm, value)` rules a class's graded records stand for. The three geometry
classes each stood at one frozen shape — `clean_bullish` 2.0xATR & 2.2R,
`inverted_rr` 1.2xATR & 0.9R, `tight_stop` 0.6xATR — replicated across every row
of the class, so fourteen clean records probed one shape and its three arms. The
census said so out loud instead of letting the record count imply otherwise, and
this campaign is the curation half of that finding: instantiate several genuinely
distinct shapes per class, and re-freeze the floor at the higher measured number.

**Rungs are declared for coverage.** `LADDERS` names, per class, the shapes that
class offers: the frozen curator shape first, then rungs spanning the class's
documented parameter space — the ATR risk multiples and R:R bands of
`docs/agents/evals.md` §2 (`clean_bullish` R:R ≥ 1.8, `tight_stop` under
0.75xATR, `inverted_rr` under 1.0). The grain is deliberately coarse and
business-meaningful: a ladder of meaningful multiples, never a sweep of 0.01
steps that would inflate the count without teaching anything. Rungs are chosen
for coverage — no forward tape is consulted to pick them.

**Only the rows the class shape authored may move.** A row is eligible iff its
geometry reproduces the class's frozen shape when recomputed from the row's own
pinned ≤T_rec tape. A row whose levels are its own published claim — the legacy
`GB-2024-0004` (2.17R) and `GB-2025-0005` (a legacy target) — is refused: its
measurement is not the class constant's, so it is not the campaign's to re-author.

**A rung must be admissible for the row, and must teach something.** Two tests,
both disclosed per row:

1. *Class coherence.* The class's frozen characterization (evals.md §2) is not
   negotiable: a rung counts only if the row's pinned forward tape realizes the
   class's documented outcome at it (clean rows achieve their target; the trap
   classes stop out). A rung that fails is printed with the tape's verdict and
   the row walks on. This is `scaffold_catalog`'s own discipline — a geometry
   contradicting the class's documented outcome is never authored — applied to a
   family of shapes instead of a single one; it is also what keeps this campaign
   from manufacturing FP/FN rows to decorate a census.
2. *New rule evidence.* A rung is used only if it adds at least one exercise the
   class does not already stand for (standing rows' own payloads count). A rung
   that would repeat an exercise the class already has is skipped and said so: a
   row that can teach the class nothing new stays at the frozen shape rather than
   being moved for the look of it. This is why moving every row is not the goal —
   the census ceiling is the class's declared parameter grid, not the row count.

Assignment walks the ladder in roster (`trade_id`) order, taking the first rung
that passes both tests. A row with no such rung is left standing, disclosed.

**What it writes.** Per moved row: the same trade re-derived at the new rung from
the row's own pinned tape (entry stays the T_rec close), stages 1–2 re-recorded
through the repo's `--record` mechanism so every graded artifact is a real stage
stdout, the row's geometry/scoring/authoring updated, and the dataset's three
level fields rewritten (its outcome claims are `$derive` references and follow
the tape). Then `manifest.sha256` and the baseline — including the census floor —
are deliberately re-frozen in the same change (V12). Every placement is frozen on
its row under `authoring.reshape`, with the shape it came from and the outcome
that changed if it did.

Disclosed limitations:

* **The ladder is a curation choice, not a measurement.** Its rungs are the
  multiples the class documents; a different declaration would move different
  numbers, which is why the family is printed by every run and frozen in the
  docs rather than left implicit in this file.
* **Rungs are walked, not searched.** Roster order decides who gets which rung;
  a row earlier in the roster can claim a rung a later row also admits. No
  assignment optimizes anything but coverage, and none re-rolls to change an
  outcome — admissibility is the class's definition, never a preference.
* **The hazard classes are untouched.** Their measurements come from the frozen
  catalog (`business_test_cases.md` §3: pledge levels, 2–4 session windows), not
  from a curation parameter, so there is no shape for this campaign to author.
"""

from __future__ import annotations

import argparse
import copy
import shutil
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "evals"))
sys.path.insert(0, str(REPO_ROOT / "tests"))   # the runner: the census's own unit

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

import curate  # noqa: E402  (the frozen ATR convention the shapes are sized in)
import dataset_refs as dr  # noqa: E402  (the frozen resolution rule)
import pin_legacy_records as plr  # noqa: E402  (fixture/row bookkeeping, re-recording)
import provenance  # noqa: E402  (the ledger's name, for the stale-report warning)
import run_evals  # noqa: E402  (the census unit: `rule_exercises`)
import scaffold_catalog as sc  # noqa: E402  (hash/baseline re-freeze)

PROOF_ROOT = REPO_ROOT / "tests" / "proof"
GOLDEN = REPO_ROOT / "tests" / "evals" / "golden_dataset.json"
BASELINE = PROOF_ROOT / sc.BASELINE_FILE

CAMPAIGN = "shape-ladder/1"
TIGHT_TARGET_RR = 2.5          # the frozen tight shape's target multiple (`rr: None`)
TIGHT_LIMIT_ATR = 0.75         # the breaker's own threshold: below this the stop is tight
PAISA = 0.011                  # rounding tolerance when matching levels to a derived shape

# The single shape each class's fleet stood at — the curator's own constants, so
# the campaign cannot drift from what authored the fleet. A row is eligible only
# if its geometry reproduces this shape from its own pinned tape.
CLASS_SHAPE = {
    "clean_bullish": (curate.CLEAN["risk_atr"], curate.CLEAN["rr"]),
    "inverted_rr": (curate.INVERTED["risk_atr"], curate.INVERTED["rr"]),
    "tight_stop": (curate.TIGHT["risk_atr"], curate.TIGHT["rr"]),
}

# Each class's frozen characterization (docs/agents/evals.md §2) — the outcome a
# rung must realize on a row's tape before it may be authored for that row.
CLASS_OUTCOME = {
    "clean_bullish": "WIN",
    "inverted_rr": "LOSS",
    "tight_stop": "LOSS",
}

# The shape families, per class, in the order rows walk them. The frozen shape is
# rung 0; the rest span the class's parameter space (ATR risk multiples x R:R
# bands). Ordered so successive rungs add new coverage rather than re-treading a
# single axis.
LADDERS: dict[str, tuple[tuple[float, float | None], ...]] = {
    "clean_bullish": (
        (2.0, 2.2), (1.0, 1.8), (1.5, 2.0), (2.5, 2.5), (3.0, 3.0),
        (1.0, 2.5), (1.5, 1.8), (2.0, 2.0), (2.5, 1.8), (3.0, 2.2),
        (1.0, 3.0), (1.5, 2.2), (2.0, 2.5), (2.5, 2.0), (3.0, 1.8),
    ),
    "inverted_rr": (
        (1.2, 0.9), (0.9, 0.75), (1.6, 0.55), (2.0, 0.35),
        (2.0, 0.55), (0.9, 0.9), (1.2, 0.55), (1.6, 0.35),
        (2.0, 0.75), (1.2, 0.35), (2.0, 0.9), (0.9, 0.55),
        (1.6, 0.9), (1.2, 0.75), (0.9, 0.35), (1.6, 0.75),
    ),
    "tight_stop": (
        (0.60, None), (0.35, None), (0.40, None), (0.45, None),
        (0.50, None), (0.55, None), (0.65, None), (0.70, None),
    ),
}

# docs/agents/evals.md §2 states each class's shape constraints; a ladder that
# breaks one is a bug in this file, not a finding.
def ladder_problems() -> list[str]:
    """The declared families must respect the frozen class specs."""
    problems: list[str] = []
    for cls, rungs in LADDERS.items():
        frozen = CLASS_SHAPE[cls]
        if rungs[0] != frozen:
            problems.append(f"{cls}: rung 0 is {rungs[0]}, not the frozen shape {frozen}")
        if len(set(rungs)) != len(rungs):
            problems.append(f"{cls}: the family repeats a rung")
        for risk, rr in rungs:
            if cls == "clean_bullish":
                if rr is None or rr < 1.8:
                    problems.append(f"{cls}: rung ({risk}, {rr}) breaks the documented R:R >= 1.8")
                if risk < TIGHT_LIMIT_ATR:
                    problems.append(f"{cls}: rung ({risk}, {rr}) would trip the tight-stop breaker")
            elif cls == "inverted_rr":
                if rr is None or rr >= 1.0:
                    problems.append(f"{cls}: rung ({risk}, {rr}) does not encode R:R < 1.0")
                if risk < 0.9:
                    problems.append(f"{cls}: rung ({risk}, {rr}) reads tight too, mixing the classes")
            elif cls == "tight_stop":
                if rr is not None:
                    problems.append(f"{cls}: rung ({risk}, {rr}) is not the class's 2.5R shape")
                if risk >= TIGHT_LIMIT_ATR:
                    problems.append(f"{cls}: rung ({risk}, {rr}) is not under {TIGHT_LIMIT_ATR}x ATR")
    return problems


class Blocked(Exception):
    """The tape or the fixtures cannot support the row — report, never invent."""


# ---------------------------------------------------------------------------
# The row's own pinned evidence
# ---------------------------------------------------------------------------
def fixture_dir(sid: str) -> Path:
    return PROOF_ROOT / "fixtures" / sid


def pinned_tape(sid: str) -> tuple[list[dict], list[dict]]:
    """`(prefix sessions, forward bars)` from the row's own committed fixtures."""
    fd = fixture_dir(sid)
    try:
        prefix = sc.read_json(fd / "ohlcv_prefix.json")["sessions"]
        forward = sc.read_json(fd / "forward_tape.json")["bars"]
    except (OSError, KeyError, TypeError) as exc:
        raise Blocked(f"pinned tape unreadable: {exc}") from exc
    if not prefix or not forward:
        raise Blocked("pinned tape is empty")
    return prefix, forward


def geometry_of(prefix: list[dict], risk_atr: float, rr: float | None) -> dict:
    """The shape, sized off the row's own ≤T_rec tape (entry = the T_rec close)."""
    atr = curate.atr14_simple_mean(prefix)
    entry = round(prefix[-1]["close"], 2)
    stop = round(entry - risk_atr * atr, 2)
    risk = round(entry - stop, 2)
    multiple = rr if rr is not None else TIGHT_TARGET_RR
    target = round(entry + multiple * risk, 2)
    if stop <= 0 or target <= entry:
        raise Blocked(f"degenerate geometry at ({risk_atr}, {rr}): stop {stop} target {target}")
    return {"entry": entry, "stop": stop, "target": target, "atr": atr,
            "band": curate.entry_band(entry)}


def at_class_shape(trade: dict, cls: str, prefix: list[dict]) -> bool:
    """Does the row's committed geometry reproduce the class's frozen shape?

    The test is structural and point-in-time: the class shape is recomputed from
    the row's own ≤T_rec tape and compared to the levels the row publishes. Rows
    it refuses are rows whose levels are their own claim (a legacy target, a
    published R:R), which this campaign has no mandate to re-author.
    """
    geom = geometry_of(prefix, *CLASS_SHAPE[cls])
    return (abs(trade["entry_price"] - geom["entry"]) <= PAISA
            and abs(trade["stop_loss"] - geom["stop"]) <= PAISA
            and abs(trade["target_price"] - geom["target"]) <= PAISA)


# ---------------------------------------------------------------------------
# The census unit, projected
# ---------------------------------------------------------------------------
def projected_exercises(cls: str, rr: float | None, risk_atr: float) -> frozenset:
    """The rule exercises a row at this rung will stand for — in the runner's unit.

    The projection is the payload shape the re-recording is about to produce
    (`out_1_math` carries the R:R pair, `out_2_market` the ATR multiple and its
    boolean), handed to the runner's own `rule_exercises`. Planning therefore
    speaks the census's exact vocabulary instead of a parallel copy of it, and
    `apply` asserts the recorded bytes reproduce the projection.
    """
    synthetic = {
        "trade_id": f"PROJECTED-{cls}",
        "trade_class": cls,
        "script_outputs": {
            "out_1_math.json": {"raw_rr": rr, "live_rr": rr},
            "out_2_market.json": {"sl_atr_ratio": risk_atr,
                                 "is_tight_sl": risk_atr < TIGHT_LIMIT_ATR},
        },
    }
    return run_evals.rule_exercises(cls, synthetic)


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------
def build_plan(trades: list[dict], wanted: set[str] | None = None,
               settled: set[str] | None = None) -> dict:
    """Place every eligible row on the first rung that is admissible and new.

    Coverage is seeded from the rows the campaign will *not* move — the ones
    already placed at a campaign rung, and the ones whose geometry is their own
    claim — and grows as rows are placed. A mover's current shape therefore never
    counts as coverage it already provides: if nothing ends up at the frozen
    shape, or at a rung the class only had because every row stood there, that is
    the census' business to report, not the plan's to preserve.
    """
    placed: list[dict] = []
    standing: list[dict] = []
    refused: list[dict] = []
    settled = settled or set()
    for cls, ladder in LADDERS.items():
        mine = sorted((t for t in trades if t["trade_class"] == cls),
                      key=lambda t: t["trade_id"])
        eligible: list[tuple[dict, list[dict], list[dict]]] = []
        covered: set = set()
        for trade in mine:
            sid = trade["trade_id"]
            try:
                prefix, forward = pinned_tape(sid)
                movable = (wanted is None or sid in wanted) and at_class_shape(trade, cls, prefix)
            except Blocked as exc:
                prefix, forward, movable = None, None, False
                refused.append({"trade_id": sid, "trade_class": cls,
                                "why": f"cannot be shaped: {exc}"})
            if movable:
                eligible.append((trade, prefix, forward))
                continue
            covered |= run_evals.rule_exercises(cls, trade)
            if sid in settled or any(item["trade_id"] == sid for item in refused):
                continue
            refused.append({"trade_id": sid, "trade_class": cls,
                            "why": "geometry is not the class shape (its own claim)"})

        for trade, prefix, forward in eligible:
            sid = trade["trade_id"]
            rejections: list[dict] = []
            for index, (risk, rr) in enumerate(ladder):
                geom = geometry_of(prefix, risk, rr)
                outcome, basis = dr.resolution_of(forward, geom["stop"], geom["target"])
                if outcome != CLASS_OUTCOME[cls]:
                    rejections.append({"rung": [risk, rr], "index": index,
                                       "why": f"tape resolves {outcome} ({basis})"})
                    continue
                projected = projected_exercises(cls, rr, risk)
                if not (projected - covered):
                    rejections.append({"rung": [risk, rr], "index": index,
                                       "why": "adds no rule exercise the class does not "
                                              "already stand for"})
                    continue
                gained = sorted(repr(key) for key in projected - covered)
                covered |= projected
                placed.append({
                    "trade_id": sid, "trade_class": cls, "rung_index": index,
                    "gained": gained,
                    "from_shape": {"risk_atr": CLASS_SHAPE[cls][0], "rr": CLASS_SHAPE[cls][1]},
                    "to_shape": {"risk_atr": risk, "rr": rr},
                    "from_levels": {"entry_price": trade["entry_price"],
                                    "stop_loss": trade["stop_loss"],
                                    "target_price": trade["target_price"]},
                    "outcome": {"was": trade.get("resolution_outcome"), "now": outcome,
                                "basis": basis},
                    "geometry": geom, "rejections": rejections,
                    "unchanged": index == 0 and abs(trade["stop_loss"] - geom["stop"]) <= PAISA,
                })
                break
            else:
                standing.append({"trade_id": sid, "trade_class": cls,
                                 "why": "no admissible rung left that adds a new rule exercise",
                                 "rejections": rejections})
    return {"placed": placed, "standing": standing, "refused": refused}


def predicted_census(plan: dict, trades: list[dict]) -> dict[str, int]:
    """The census the plan would realize — recomputed in the runner's own unit.

    A placed row contributes the exercises of its *new* rung (projected exactly as
    the re-recording will speak them); every other row contributes what it stands
    for today. Nothing is asserted from the ladder's arithmetic: the number this
    returns is the one `dataset_rule_census` will read after `--apply`.
    """
    moved = {p["trade_id"]: p for p in plan["placed"]}
    census: dict[str, set] = {t["trade_class"]: set() for t in trades}
    for trade in trades:
        cls = trade["trade_class"]
        placed = moved.get(trade["trade_id"])
        if placed is None:
            census[cls] |= run_evals.rule_exercises(cls, trade)
        else:
            shape = placed["to_shape"]
            census[cls] |= projected_exercises(cls, shape["rr"], shape["risk_atr"])
    return {cls: len(keys) for cls, keys in sorted(census.items())}


# ---------------------------------------------------------------------------
# Applying the plan
# ---------------------------------------------------------------------------
def write_replay_input(placed: dict) -> None:
    """Update the row's canonical trade fixture to the new rung (entry unchanged)."""
    path = fixture_dir(placed["trade_id"]) / "current_trade.json"
    canonical = sc.read_json(path)
    for key, value in (("target", placed["geometry"]["target"]),
                       ("stoploss", placed["geometry"]["stop"]),
                       ("entry_price", placed["geometry"]["entry"]),
                       ("live_price", placed["geometry"]["entry"]),
                       ("entry_range_min", placed["geometry"]["band"][0]),
                       ("entry_range_max", placed["geometry"]["band"][1])):
        if key not in canonical:
            raise Blocked(f"{placed['trade_id']}: canonical trade has no {key!r}")
        canonical[key] = value
    sc.write_json(path, canonical)


def re_record(placed: dict) -> dict:
    """Re-run stages 1–2 for the new rung; the graded bytes become real stdout."""
    sid = placed["trade_id"]
    bundle_dir = fixture_dir(sid) / "recorded"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    recorded = plr.record_stages({"row": {"scenario_id": sid}})
    invariants = plr.check_class_invariant({"trade": {"trade_class": placed["trade_class"]}},
                                          recorded)
    # The recording must reproduce the projection the plan was built on, in the
    # census's own granule: a rung that lands somewhere else is a bug, not a
    # finding, and the census would silently measure a shape nobody chose.
    risk, rr = placed["to_shape"]["risk_atr"], placed["to_shape"]["rr"]
    got_rr = [recorded["stage1"].get("raw_rr"), recorded["stage1"].get("live_rr")]
    got_ratio = recorded["stage2"].get("sl_atr_ratio")
    if rr is not None and any(abs(round(v, 2) - round(rr, 2)) > 1e-9 for v in got_rr):
        raise Blocked(f"{sid}: recorded R:R {got_rr} is not the rung's {rr}")
    if got_ratio is None or abs(round(got_ratio, 2) - round(risk, 2)) > 1e-9:
        raise Blocked(f"{sid}: recorded ATR multiple {got_ratio} is not the rung's {risk}")
    return {"invariants": invariants, "stage1": recorded["stage1"], "stage2": recorded["stage2"]}


def reshaped_row(row: dict, placed: dict, today: str) -> dict:
    """The row as it must read after the campaign, with its provenance frozen on it."""
    new = copy.deepcopy(row)
    geom = placed["geometry"]
    new["entry_price"] = geom["entry"]
    new["stop_loss"] = geom["stop"]
    new["target_price"] = geom["target"]
    new["entry_range_min"], new["entry_range_max"] = geom["band"]
    scoring = new["scoring"]
    scoring["resolution_outcome"] = placed["outcome"]["now"]
    scoring["resolution_basis"] = placed["outcome"]["basis"]
    quadrant = plr.OUTCOME_QUADRANT.get((new.get("expected_verdict"),
                                          placed["outcome"]["now"]))
    if quadrant:
        scoring["quadrant_target"] = quadrant
    elif "quadrant_target" in scoring:
        scoring.pop("quadrant_target")
    shape = placed["to_shape"]
    new["authoring"]["reshape"] = {
        "campaign": CAMPAIGN,
        "reason": ("the class stood at one frozen shape replicated across rows; this "
                   "row was re-derived at a declared rung of the class's shape family "
                   "so the class's rule-exercise census measures coverage, not rows"),
        "rung_index": placed["rung_index"],
        "shape": {"risk_atr": shape["risk_atr"], "rr": shape["rr"]},
        "from_shape": placed["from_shape"],
        "from_levels": placed["from_levels"],
        "sizing": ("re-derived from the row's own pinned ≤T_rec tape: entry = T_rec "
                   "close, stop = entry − risk_atr×ATR(14), target at the class's "
                   "target multiple"),
        "outcome": placed["outcome"],
        "refused_rungs": placed["rejections"],
    }
    note = new["authoring"].get("note", "").strip("; ")
    new["authoring"]["note"] = "; ".join(filter(None, [
        note,
        f"reshaped by {CAMPAIGN} to the class ladder rung (risk_atr "
        f"{shape['risk_atr']}, rr {shape['rr']}) from ({placed['from_shape']['risk_atr']}, "
        f"{placed['from_shape']['rr']}) — {len(placed['rejections'])} rung(s) refused "
        "before it, each recorded in authoring.reshape.refused_rungs",
    ]))
    return new


def reshaped_record(trade: dict, placed: dict) -> dict:
    """The dataset record: three level fields; every outcome claim stays `$derive`."""
    new = copy.deepcopy(trade)
    new["entry_price"] = placed["geometry"]["entry"]
    new["stop_loss"] = placed["geometry"]["stop"]
    new["target_price"] = placed["geometry"]["target"]
    return new


def campaign_marker(row: dict | None) -> bool:
    """Whether a manifest row carries this campaign's placement disclosure."""
    return ((row or {}).get("authoring") or {}).get("reshape", {}).get("campaign") == CAMPAIGN


def campaign_counts(data: dict, rows: dict[str, dict]) -> tuple[int, int]:
    """`(re-recorded, left at the frozen shape)` — counted from what is committed.

    Both numbers are read back from the dataset and the manifest rather than from
    the plan, so they describe the campaign that is on disk even when the tool is
    re-run (whose plan sees the work as already done) — a note that shrank with
    every re-run would misreport the change it documents.
    """
    recorded = sum(1 for row in rows.values() if campaign_marker(row))
    frozen = 0
    for trade in data["trades"]:
        cls = trade["trade_class"]
        sid = trade["trade_id"]
        if cls not in LADDERS or campaign_marker(rows.get(sid)):
            continue
        try:
            prefix, _forward = pinned_tape(sid)
        except Blocked:
            continue
        if at_class_shape(trade, cls, prefix):
            frozen += 1
    return recorded, frozen


def campaign_note(plan: dict, recorded: int, frozen: int) -> str:
    """The dataset's own account of the campaign — a function of the committed state."""
    ladder_text = "; ".join(
        f"{cls} " + ", ".join(
            f"{risk}xATR" + (f"&{rr}R" if rr is not None else "&2.5R")
            for risk, rr in rungs)
        for cls, rungs in LADDERS.items())
    return (
        f"Shape-ladder campaign ({CAMPAIGN}): {recorded} row(s) re-derived at declared "
        f"rungs of their class's shape family and re-recorded from their own pinned "
        f"≤T_rec tape; {frozen} left at the frozen class shape (the plan walked each "
        f"and found no admissible rung that added a rule exercise the class did not "
        f"already stand for); {len(plan['refused'])} kept as their own published "
        f"geometry. Families: {ladder_text}. A rung is authored only where the row's "
        f"pinned tape realizes the class's documented outcome (docs/agents/evals.md "
        f"§2) and only where it adds new rule coverage, so the census measures the "
        f"classes' parameter coverage rather than their row counts; each placement, "
        f"its refusals and the outcome it changed are frozen on its row under "
        f"`authoring.reshape`. Authored by tests/evals/reshape_records.py.")


def apply_plan(plan: dict, data: dict, manifest: dict, full_run: bool = True) -> int:
    """Re-record every placement, rewrite rows/dataset, re-freeze hashes + baseline.

    A plan with nothing to re-record writes no evidence and re-freezes nothing, so
    re-running an applied campaign cannot churn the proof root or a baseline it did
    not change; the only thing a no-op apply may still correct is the prose the
    committed state derives (the dataset's campaign note), and only a whole-roster
    run may do even that (a `--records` subset would describe a partial plan).
    """
    writable = [p for p in plan["placed"] if not p["unchanged"]]
    trades = {t["trade_id"]: t for t in data["trades"]}
    rows = {r["scenario_id"]: r for r in manifest["scenarios"]}
    today = date.today().isoformat()
    moved = 0

    def refresh_note() -> bool:
        """Write the campaign note if the committed state implies a different one."""
        if not full_run:
            return False
        recorded, frozen = campaign_counts(data, rows)
        note = campaign_note(plan, recorded, frozen)
        if note in data["meta"]["notes"]:
            return False
        data["meta"]["notes"] = [n for n in data["meta"]["notes"] if CAMPAIGN not in n]
        data["meta"]["notes"].append(note)
        return True

    if not writable:
        if refresh_note():
            sc.write_json(GOLDEN, data)
            print("nothing to re-record; the campaign note was refreshed to match the "
                  "committed placements")
        elif full_run:
            print(f"nothing to do: {len(plan['placed'])} placement(s) already at their "
                  "campaign rung(s)")
        else:
            print("nothing to do for the selected records")
        return 0
    for placed in plan["placed"]:
        sid = placed["trade_id"]
        if placed["unchanged"]:
            print(f"  {sid}: already at rung {placed['rung_index']} "
                  f"({placed['to_shape']['risk_atr']}, {placed['to_shape']['rr']}) — kept")
            continue
        write_replay_input(placed)
        recorded = re_record(placed)
        for check in recorded["invariants"]:
            print(f"  {sid}: recorded invariant OK — {check}")
        rows[sid] = reshaped_row(rows[sid], placed, today)
        trades[sid] = reshaped_record(trades[sid], placed)
        moved += 1
        print(f"  {sid}: rung {placed['rung_index']} "
              f"({placed['to_shape']['risk_atr']}xATR, "
              f"{placed['to_shape']['rr'] if placed['to_shape']['rr'] is not None else '2.5R'}) "
              f"-> {placed['outcome']['now']} "
              f"(was {placed['outcome']['was']}) | raw_rr "
              f"{round(recorded['stage1']['raw_rr'], 6)} ratio "
              f"{round(recorded['stage2']['sl_atr_ratio'], 6)}")

    data["trades"] = [trades[k] for k in sorted(trades)]
    refresh_note()
    sc.write_json(GOLDEN, data)

    manifest["scenarios"] = [rows[k] for k in sorted(rows)]
    manifest["generated_on"] = today
    sc.write_json(PROOF_ROOT / sc.MANIFEST_FILE, manifest)

    hashed = sc.regenerate_hashes()
    sc.re_freeze_baseline()
    baseline = sc.read_json(BASELINE)
    print(f"\nreshaped {moved} row(s); manifest.sha256 covers {hashed} file(s); "
          f"baseline re-frozen; census floor now {baseline.get('rule_exercises')}")
    reports = REPO_ROOT / "reports"
    if (reports / provenance.RECORD_NAME).exists():
        print("\nNOTE: reports/ holds artifacts recorded before this reshape. A warm "
              "ledger proves stale bytes, so the runner would grade their old numbers "
              "as hallucinations; reproduce them from the proof root first "
              "(`git clean -Xdf reports`, then re-run tests/run_evals.py).")
    return 0


def report(plan: dict, trades: list[dict]) -> None:
    """Print the plan as findings: placements, refusals, and the census it buys."""
    before = run_evals.dataset_rule_census()
    after = predicted_census(plan, trades)
    print("=" * 78)
    print("SHAPE-LADDER PLAN — declared rungs, and what each placement buys")
    print("=" * 78)
    for cls, ladder in LADDERS.items():
        print(f"\n{cls}: family of {len(ladder)} shape(s) — "
              + ", ".join(f"{risk}xATR/{rr if rr is not None else '2.5R'}" for risk, rr in ladder))
    print()
    for placed in plan["placed"]:
        shape = placed["to_shape"]
        print(f"  PLACE {placed['trade_id']:14s} {placed['trade_class']:13s} rung "
              f"{placed['rung_index']:2d} = {shape['risk_atr']}xATR/"
              f"{shape['rr'] if shape['rr'] is not None else '2.5R'} "
              f"stop {placed['geometry']['stop']:>10} "
              f"target {placed['geometry']['target']:>10} "
              f"(+{len(placed['gained'])} new rule exercise(s))")
        for key in placed["gained"]:
            print(f"         ~ {key}")
        for item in placed["rejections"]:
            print(f"         ↳ refused rung {item['index']:2d} "
                  f"({item['rung'][0]}xATR/{item['rung'][1]}): {item['why']}")
    for item in plan["standing"]:
        print(f"  STAND {item['trade_id']:14s} {item['trade_class']:13s} {item['why']}")
        for rej in item["rejections"][:3]:
            print(f"         ↳ refused rung {rej['index']:2d} "
                  f"({rej['rung'][0]}xATR/{rej['rung'][1]}): {rej['why']}")
        if len(item["rejections"]) > 3:
            print(f"         ↳ … {len(item['rejections']) - 3} more refused rung(s)")
    for item in plan["refused"]:
        print(f"  KEEP  {item['trade_id']:14s} {item['trade_class']:13s} {item['why']}")
    print("\nrule-exercise census (class: was -> plan):")
    for cls in sorted(set(before) | set(after)):
        note = "" if cls in LADDERS else "  (catalog-fixed: no shape to author)"
        print(f"  {cls:14s} {before.get(cls, 0):3d} -> {after.get(cls, 0):3d}{note}")
    print(f"  {'TOTAL':14s} {sum(before.values()):3d} -> {sum(after.values()):3d}")
    print("\nLimitations (disclosed, not hidden):")
    print("  · the families are curation choices — a different declaration moves different")
    print("    numbers; they are frozen in docs/agents/evals.md §2, not implicit in this tool")
    print("  · rungs are walked in roster order and the first admissible, coverage-adding")
    print("    rung wins: no assignment is searched for a maximum, and none is re-rolled for")
    print("    an outcome")
    print("  · the hazard classes are untouched: their measurements come from the frozen")
    print("    catalog, so there is no shape here for them to author")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Spread the geometry classes across their declared shape families.")
    parser.add_argument("--records", default="",
                        help="Comma-separated trade ids to restrict the campaign to.")
    parser.add_argument("--apply", action="store_true",
                        help="Re-record placements, rewrite the dataset and re-freeze.")
    args = parser.parse_args(argv)

    problems = ladder_problems()
    if problems:
        for problem in problems:
            print(f"❌ declared ladder breaks the frozen class spec: {problem}",
                  file=sys.stderr)
        return 2

    data = sc.read_json(GOLDEN)
    # Planning reads the rows' *evidence* (their recorded payloads), which the
    # dataset stores as `$ref`s; `apply` rewrites the raw records so the refs and
    # the single copy of those bytes stay exactly as the proof root declares them.
    materialized, _refs, _derives = dr.materialize(data)
    trades = materialized["trades"]
    already = [r["scenario_id"] for r in
               sc.read_json(PROOF_ROOT / sc.MANIFEST_FILE)["scenarios"]
               if (r.get("authoring") or {}).get("reshape", {}).get("campaign") == CAMPAIGN]
    if already and not args.apply:
        print(f"already reshaped by {CAMPAIGN} (idempotent): {', '.join(sorted(already))}")
    wanted = {s.strip() for s in args.records.split(",") if s.strip()} or None
    plan = build_plan(trades, wanted, set(already))
    report(plan, trades)

    if not args.apply:
        print(f"\nDRY RUN — {len(plan['placed'])} placement(s) planned; nothing written")
        return 0
    manifest = sc.read_json(PROOF_ROOT / sc.MANIFEST_FILE)
    return apply_plan(plan, data, manifest, full_run=wanted is None)


if __name__ == "__main__":
    sys.exit(main())
