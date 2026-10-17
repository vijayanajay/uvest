#!/usr/bin/env python3
"""Envelope-depth sweep for gate 5 (Price Cone Range Coverage) — measured only.

    python tests/evals/envelope_depth_sweep.py            # prints the tables
    python tests/evals/envelope_depth_sweep.py --pairwise  # plus a one-off
        # side-by-side: each graded trade's P4 cone under its own sector block
        # vs pooled, both envelopes' containment, and which pool rule N=20
        # would pick (diagnostic — moves no rule and no threshold)

Why this tool exists. proposal-cone-envelope-depth.md puts two levers in front
of the owner — deepen the pooled tail (deeper mixture levels on the SAME pools
and the SAME per-row class mixture the stage already uses) versus re-scope the
graded set — and both answers must be *measured* over exactly the records gate 5
grades today, at unchanged thresholds, before any option is even listed.

Fidelity, and how it is proved on every run:

* the graded set is rebuilt with `run_evals`' own inputs — the trained corpus
  window from the calibration, each row's tape span from the same `$derive` the
  gate reads, the frozen representative/tail class split — so the baseline row
  of the table must equal the committed gate reading (printed as a check);
* each row's cone is recomputed through **stage 4's own `compute` path** over
  the row's own recorded fixtures (canonical trade, ≤T_rec OHLCV prefix, its
  `uvest-market-context/1` bundle, the committed booster) — the class mixture
  is captured from stage 4's own `predict`, never reimplemented — with the
  candidate levels read off `stage4.samples_for` pools via `stage4._pooled` /
  `stage4.mixture_quantile`, i.e. CONE_SPEC with its levels swapped and nothing
  else changed;
* the sweep asserts per row that the candidate `[0.10, 0.90]` levels reproduce
  the row's STORED `cone_p10` / `cone_p90` claims to the rupee, and prints the
  worst deviation — a non-zero baseline means the sweep is measuring something
  else and the table must be discarded.

Nothing here writes to the dataset, the calibration or the scorecard; it is a
proposals evidence tool in the same class as the fill-horizon session
measurements. Stdlib plus the committed stage/evals modules only.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent
EVALS_DIR = TESTS_DIR / "evals"
REPO_ROOT = TESTS_DIR.parent
PROOF_ROOT = TESTS_DIR / "proof"
for splice in (str(EVALS_DIR), str(TESTS_DIR), str(REPO_ROOT / "scripts")):
    if splice not in sys.path:
        sys.path.insert(0, splice)

import dataset_refs as dr          # noqa: E402
import run_evals                   # noqa: E402

# scripts/4_ml_direction.py — the same import path the eval runner stages use.
stage4 = run_evals.load_stage4()

# Candidate depth pairs, oldest convention first: an envelope is a
# (floor level, ceiling level) pair on the observed excursion mixtures.
# P0 is the shipped CONE_SPEC (0.10 / 0.90); P9 is the observed range.
DEPTH_CANDIDATES: tuple[tuple[str, float, float], ...] = (
    ("P0", 0.10, 0.90),
    ("P1", 0.10, 0.95),
    ("P2", 0.05, 0.90),
    ("P3", 0.05, 0.95),
    ("P4", 0.025, 0.975),
    ("P5", 0.02, 0.98),
    ("P6", 0.01, 0.99),
    ("P7", 0.005, 0.995),
    ("P8", 0.001, 0.999),
    ("P9", 0.0, 1.0),
)

# Option D's axis: the frozen per-class atom floor
# (`stage4.MIN_SECTOR_ATOMS_PER_CLASS`, ADR-011 decision 13) re-read at a
# different N. The D-world is measured by moving the ONE constant the
# amendment would move and re-calling `samples_for` — never by a second
# implementation of the usability rule. N=30 is today's frozen value and
# therefore the D-off control column of every table below.
D_RULES: tuple[int, ...] = (30, 20, 10, 5, 1)


def grade_scope() -> tuple[list[dict], dict, str]:
    """Every cone-labeled row re-run through stage 4's own `compute` path.

    Returns (race_rows, calibration, window0). `race_rows` holds all **31**
    records that carry stage-4 evidence (gate 4's graded set) with the class
    mixture, the N=30 pool, the row's race geometry and its frozen outcome —
    plus an `in_window` flag; the 29 in-window rows are gate 5's graded set.
    Every row's recomputed N=30 race probability must equal its stored
    `prob_target_hit_22d`, and the N=30 Brier must equal the committed gate-4
    baseline, or the sweep aborts: a fidelity break cannot produce a proposal.
    """
    raw = run_evals.load_benchmark(EVALS_DIR / "golden_dataset.json")
    spans = {str(t.get("trade_id")): dr.forward_tape_span(t) for t in raw["trades"]}
    data, _refs, _derives = dr.materialize(raw)
    # The recorded inputs (canonical tape, prefix, context bundle) live on the
    # proof rows — the same manifest the recording tools iterate.
    manifest = json.loads(
        (PROOF_ROOT / "scenario_manifest.json").read_text(encoding="utf-8"))
    scenarios = {str(r.get("scenario_id")): r for r in manifest["scenarios"]}
    window = run_evals.trained_corpus_window()
    assert window is not None, "calibration window unreadable — nothing to sweep"

    booster, calibration, model_reason = stage4.load_model(
        str(stage4.DEFAULT_MODEL), str(stage4.DEFAULT_CALIBRATION))
    assert not model_reason, f"model unavailable: {model_reason}"
    model = (booster, calibration, model_reason)

    captured: dict[str, float] = {}
    real_predict = stage4.predict

    def capture_predict(booster_, features):      # type: ignore[no-untyped-def]
        result = real_predict(booster_, features)
        captured.clear()
        captured.update({k: float(v) for k, v in dict(result[0]).items()})
        return result

    graded: list[dict] = []          # gate 5's 29, in-window rows only
    race_rows: list[dict] = []       # gate 4's 31 — every cone-labeled row
    baseline_checked = 0
    worst_p10 = worst_p90 = 0.0
    ungraded: list[str] = []

    stage4.predict = capture_predict              # one stage run's own mixture
    try:
        for t in data["trades"]:
            tid = str(t.get("trade_id"))
            cone_p10, cone_p90 = t.get("cone_p10"), t.get("cone_p90")
            fwd = t.get("forward_prices_22d") or {}
            lo, hi = fwd.get("min"), fwd.get("max")
            if cone_p10 is None or cone_p90 is None or lo is None or hi is None:
                continue
            if tid not in spans or spans[tid] is None:
                ungraded.append(f"{tid} (no tape)")
                continue
            first, last = spans[tid]
            in_window = window[0] <= first and last <= window[1]
            if not in_window:
                ungraded.append(f"{tid} (tape {first}..{last})")
            inputs = (scenarios.get(tid) or {}).get("replay_inputs") or {}
            canonical = PROOF_ROOT / (inputs.get("canonical_trade") or "")
            prefixes = inputs.get("truncated_fixtures") or []
            bundle_rel = inputs.get("regime_fixture")
            if not canonical.exists() or not prefixes or not bundle_rel:
                raise SystemExit(f"{tid}: graded row missing its recorded inputs")
            payload = stage4.load_payload(str(canonical))
            sessions = stage4.load_ohlcv(str(PROOF_ROOT / prefixes[0]))
            bundle, bundle_reason = stage4.load_context(str(PROOF_ROOT / bundle_rel))
            captured.clear()
            result, reasons = stage4.compute(payload, sessions, bundle,
                                             bundle_reason, model)
            if result.get("waived"):
                raise SystemExit(f"{tid}: recorded row re-waives today "
                                 f"({', '.join(result.get('reasons') or reasons)})")
            probabilities = dict(captured)
            if not probabilities:
                raise SystemExit(f"{tid}: class mixture not captured from stage 4")
            symbol = str(t.get("symbol") or "").strip().upper()
            pool = stage4.samples_for(calibration,
                                      stage4.SECTOR_INDEX_MAP.get(symbol))
            # Basis: the price the stored cone was built around (stage's
            # live_price). Derived from the stored p10 claim itself so the
            # sweep's price space is bit-for-bit the gate's.
            p10_now = (stage4.mixture_quantile(
                stage4._pooled(pool["mae_pct"], probabilities), 0.10))
            basis = cone_p10 / (1.0 + p10_now / 100.0)
            p90_now = (stage4.mixture_quantile(
                stage4._pooled(pool["mfe_pct"], probabilities), 0.90))
            d10 = abs(basis * (1.0 + p10_now / 100.0) - cone_p10)
            d90 = abs(basis * (1.0 + p90_now / 100.0) - cone_p90)
            if max(d10, d90) > 0.011:
                raise SystemExit(f"{tid}: baseline P0 does not reproduce the "
                                 f"stored claims (p10 {d10:.4f} / "
                                 f"p90 {d90:.4f} INR) — sweep would be fiction")
            worst_p10 = max(worst_p10, d10)
            worst_p90 = max(worst_p90, d90)
            baseline_checked += 1
            # Gate 4's race geometry, from the stage's own inputs: the same
            # `race_probability` the payload's `prob_target_hit_22d` came from.
            live = float(payload["live_price"])
            target_pct = (float(payload["target"]) / live - 1.0) * 100.0
            stop_pct = (live - float(payload["stoploss"])) / live * 100.0
            race30 = stage4.race_probability(probabilities, pool,
                                             target_pct, stop_pct)
            stored_race = result.get("prob_target_hit_22d")
            if round(race30, 4) != float(stored_race):
                raise SystemExit(f"{tid}: recomputed race {race30:.6f} != "
                                 f"stored {stored_race} — pools or geometry "
                                 "drifted from the stage")
            row = {
                "tid": tid,
                "trade_class": t.get("trade_class"),
                "slice": run_evals.slice_of(t.get("trade_class")),
                "symbol": symbol,
                "sector": stage4.SECTOR_INDEX_MAP.get(symbol),
                "probabilities": probabilities,
                "pool": pool is calibration["samples"],
                "pool_name": ("pooled" if pool is calibration["samples"]
                              else "sector"),
                "basis": basis,
                "stored": (cone_p10, cone_p90),
                "lo": lo, "hi": hi,
                "tape": (first, last),
                "in_window": in_window,
                "race30": race30,
                "resolution": t.get("resolution_outcome"),
                "target_pct": target_pct,
                "stop_pct": stop_pct,
            }
            race_rows.append(row)
            if in_window:
                graded.append(row)
    finally:
        stage4.predict = real_predict

    print(f"# sweep scope: {len(graded)} graded rows "
          f"(window {window[0]}..{window[1]})", file=sys.stderr)
    print(f"# gate-4 race rows: {len(race_rows)} "
          "(each recomputed N=30 race equals its stored prob_target_hit_22d)",
          file=sys.stderr)
    print(f"# ungraded: {'; '.join(ungraded)}", file=sys.stderr)
    print(f"# baseline P0 reproduction over {baseline_checked} rows: "
          f"worst |Δp10| {worst_p10:.4f} INR, |Δp90| {worst_p90:.4f} INR",
          file=sys.stderr)
    return race_rows, calibration, window[0]


def levels_pct(row: dict, ql: float, qu: float) -> tuple[float, float]:
    pool = row["pool_block"]
    mae = stage4._pooled(pool["mae_pct"], row["probabilities"])
    mfe = stage4._pooled(pool["mfe_pct"], row["probabilities"])
    return (stage4.mixture_quantile(mae, ql), stage4.mixture_quantile(mfe, qu))


def attach_pools(graded: list[dict], calibration: dict) -> None:
    blocks = calibration.get("samples_by_sector") or {}
    for row in graded:
        row["pool_block"] = (calibration["samples"] if row["pool"]
                             else blocks[row["sector"]])


def covered(row: dict, ql: float, qu: float) -> tuple[bool, float, float]:
    floor_pct, ceil_pct = levels_pct(row, ql, qu)
    floor_inr = row["basis"] * (1.0 + floor_pct / 100.0)
    ceil_inr = row["basis"] * (1.0 + ceil_pct / 100.0)
    return floor_inr <= row["lo"] and row["hi"] <= ceil_inr, floor_pct, ceil_pct


def pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.1f}%"


def depth_table(graded: list[dict]) -> None:
    misses_by_id: dict[str, list[str]] = {}
    ladder: list[tuple[str, int, int, list[str], int, int]] = []
    print("\n## P0–P9 envelope table (gate 5's graded set, thresholds untouched)\n")
    print("| id | floor level | ceiling level | headline (of 29) | "
          "representative (of 23) | tail (of 6) | still missing | "
          "median width |")
    print("| :--- | ---: | ---: | ---: | ---: | ---: | :--- | ---: |")
    for name, ql, qu in DEPTH_CANDIDATES:
        hits = 0
        miss_ids: list[str] = []
        widths: list[float] = []
        for row in graded:
            ok, floor_pct, ceil_pct = covered(row, ql, qu)
            if ok:
                hits += 1
            else:
                miss_ids.append(row["tid"])
                misses_by_id.setdefault(row["tid"], []).append(name)
            widths.append(ceil_pct - floor_pct)
        rep = [r for r in graded if r["slice"] == "representative"]
        tail = [r for r in graded if r["slice"] == "tail"]
        rep_hits = sum(1 for r in rep if covered(r, ql, qu)[0])
        tail_hits = sum(1 for r in tail if covered(r, ql, qu)[0])
        tail_miss = sorted(r["tid"] for r in tail if not covered(r, ql, qu)[0])
        ladder.append((name, tail_hits, len(tail), tail_miss, hits,
                       len(graded)))
        misses_by_id.setdefault("__provisional", [])
        print(f"| **{name}** | {ql or 0:.3f} | {qu:.3f} | "
              f"**{hits} → {pct(hits, len(graded))}** | "
              f"{rep_hits} → {pct(rep_hits, len(rep))} | "
              f"{tail_hits} → {pct(tail_hits, len(tail))} | "
              f"{', '.join(sorted(miss_ids)) if miss_ids else '—'} | "
              f"**{statistics.median(widths):.1f}%** |")

    print("\n### Which record flips at which depth (the 13 current misses)\n")
    print("| record | slice | realised low..high (of basis) | first depth that covers it |")
    print("| :--- | :--- | :--- | :--- |")
    for row in graded:
        first = next((name for name, ql, qu in DEPTH_CANDIDATES
                      if covered(row, ql, qu)[0]), None)
        if first is None or first == "P0":
            if first is None:
                print(f"| {row['tid']} ({row['symbol']}) | {row['slice']} | "
                      f"no candidate covers it | **never** |")
            continue
        lo_pct = (row["lo"] / row["basis"] - 1.0) * 100.0
        hi_pct = (row["hi"] / row["basis"] - 1.0) * 100.0
        print(f"| {row['tid']} ({row['symbol']}) | {row['slice']} | "
              f"{lo_pct:+.2f}% .. {hi_pct:+.2f}% | {first} |")

    # Q3's lever, measured: the tail slice judged at its own floor, per depth.
    # Deeper candidates nest the shallower ones (floor falls, ceiling rises),
    # so coverage must be monotone — a violation would mean the ladder is
    # measuring something other than envelope depth.
    counts = [tail_hits for _nm, tail_hits, _tn, _miss, _h, _n in ladder]
    if any(a > b for a, b in zip(counts, counts[1:])):
        raise SystemExit(f"tail coverage not monotone across depths: {counts} "
                         "— envelope nesting broke; discard the ladder")
    print("\n### Tail stress ladder (Q3) — the tail slice judged at its own "
          "floor, every depth, today's pools\n")
    print("| depth | tail slice (of 6) | clears any own floor ≤ | "
          "tail rows still held out | headline (of 29) |")
    print("| :--- | ---: | ---: | :--- | ---: |")
    for name, tail_hits, tail_n, tail_miss, hits, n in ladder:
        value = pct(tail_hits, tail_n)
        print(f"| {name} | **{tail_hits}/{tail_n} → {value}** | {value} | "
              f"{', '.join(tail_miss) if tail_miss else '—'} | "
              f"{pct(hits, n)} |")


def rescope_table(graded: list[dict], calibration: dict) -> None:
    n = len(graded)
    rep = [r for r in graded if r["slice"] == "representative"]
    tail = [r for r in graded if r["slice"] == "tail"]
    rep_hits0 = sum(1 for r in rep if covered(r, 0.10, 0.90)[0])
    head0 = sum(1 for r in graded if covered(r, 0.10, 0.90)[0])

    print("\n## The re-scope lever, measured (same P0 envelope, same threshold)\n")
    print("| variant | headline reads | clears ≥ 80%? |")
    print("| :--- | ---: | :--- |")
    print(f"| keep today's graded set (29 rows; rep {rep_hits0}/{len(rep)}, "
          f"tail {sum(1 for r in tail if covered(r, 0.10, 0.90)[0])}/{len(tail)}) "
          f"| {pct(head0, n)} | **no** |")
    print(f"| headline = representative slice only (tail becomes a disclosed "
          f"stress appendix, still printed) | {pct(rep_hits0, len(rep))} "
          f"({rep_hits0}/{len(rep)}) | **no** |")

    # Close path: all 22 forward closes inside the SAME envelope.
    close_hits = 0
    close_rep = close_tail = rep_n = tail_n = 0
    for row in graded:
        tape = json.loads((PROOF_ROOT / "fixtures" / row["tid"]
                           / "forward_tape.json").read_text(encoding="utf-8"))
        bars = tape.get("bars") or tape.get("candles") or []
        floor_pct, ceil_pct = levels_pct(row, 0.10, 0.90)
        ok = all((row["basis"] * (1.0 + floor_pct / 100.0)
                  <= b["close"]
                  <= row["basis"] * (1.0 + ceil_pct / 100.0)) for b in bars)
        close_hits += ok
        if row["slice"] == "representative":
            rep_n += 1
            close_rep += ok
        else:
            tail_n += 1
            close_tail += ok
    print(f"| grade the **close path** instead of the bars (decision 8's "
          f"definition reversed) | {pct(close_hits, n)} "
          f"(rep {pct(close_rep, rep_n)}, tail {pct(close_tail, tail_n)}) | "
          f"{'yes' if close_hits * 100 >= 80 * n else 'no'} |")
    print("| lower the floor to what today's model reads (≤ 55.2%) | 55.2% | yes |")
    print("\n*Outcome- or class-based row drops are not listed: the signed Q3 "
          "rule grades by class and never by outcome, so a drop that would flip "
          "one of the misses above is excluded by definition — representative's "
          "14/23 is the ceiling of any honest re-scope that keeps thresholds.*")


def tail_pool_detail(graded: list[dict], calibration: dict) -> None:
    blocks = calibration.get("samples_by_sector") or {}
    rows = [r for r in graded if r["slice"] == "tail"
            and not covered(r, 0.10, 0.90)[0]]
    print("\n## The four remaining tail misses, against BOTH pools\n")
    print("| record | realised extreme | pool the cone actually read: "
          "first covering depth | own sector block (today deferred): "
          "first covering depth |")
    print("| :--- | ---: | :--- | :--- |")
    for row in rows:
        lo_pct = (row["lo"] / row["basis"] - 1.0) * 100.0
        hi_pct = (row["hi"] / row["basis"] - 1.0) * 100.0
        extreme_is_low = abs(lo_pct) > hi_pct

        def first_depth(block) -> str:
            saved = row.get("pool_block")
            row["pool_block"] = block
            try:
                for name, ql, qu in DEPTH_CANDIDATES:
                    if covered(row, ql, qu)[0]:
                        return name
                return "never"
            finally:
                row["pool_block"] = saved

        block = blocks.get(row["sector"])
        own = first_depth(block) if block else "no block"
        other = "—" if block is row["pool_block"] else first_depth(row["pool_block"])
        print(f"| {row['tid']} ({row['symbol']}) | "
              f"{lo_pct:+.2f}% .. {hi_pct:+.2f}% | {other} | {own} |")
        _ = extreme_is_low  # table reports both sides of the envelope


def bd_pools_at(race_rows: list[dict], calibration: dict,
                rule: int) -> dict[str, dict]:
    """Per-row pools under a D-world rule: the ONE frozen constant moves."""
    saved = stage4.MIN_SECTOR_ATOMS_PER_CLASS
    stage4.MIN_SECTOR_ATOMS_PER_CLASS = rule
    try:
        return {r["tid"]: stage4.samples_for(calibration, r["sector"])
                for r in race_rows}
    finally:
        stage4.MIN_SECTOR_ATOMS_PER_CLASS = saved


def bd_census(calibration: dict, graded: list[dict]) -> None:
    """Which sector blocks exist, how thin their thinnest cell is, who switches."""
    blocks = calibration.get("samples_by_sector") or {}
    min_counts = {
        ticker: min(len((block.get(measure) or {}).get(cls) or [])
                    for measure in stage4.CONE_MEASURES.values()
                    for cls in stage4.CLASSES)
        for ticker, block in sorted(blocks.items())}
    sector_by_ticker: dict[str, list[str]] = {}
    for row in graded:
        # `row["pool"]` is True when the row reads the POOLED block today.
        if row["sector"] and row["pool"]:
            sector_by_ticker.setdefault(row["sector"], []).append(row["tid"])
    print("\n## Option D census — the frozen per-class atom floor, re-read\n")
    print("| sector block | thinnest cell (atoms, measure × class) | "
          "usable at N ≤ | graded rows in that sector reading POOLED today |")
    print("| :--- | ---: | ---: | :--- |")
    for ticker, count in min_counts.items():
        rows = sector_by_ticker.get(ticker)
        print(f"| `{ticker}` | {count} | {count if count else 'never'} | "
              f"{', '.join(rows) if rows else '—'} |")
    for rule in D_RULES:
        usable = {t for t, count in min_counts.items() if count >= rule}
        switched = sorted(r["tid"] for r in graded
                          if r["sector"] in usable and r["pool"])
        print(f"- rule **N={rule}**{' (today — the frozen D-off control)' if rule == 30 else ''}: "
              f"{len(usable)} of {len(min_counts)} block(s) usable; "
              f"{len(switched)} graded row(s) pooled → sector: "
              f"{', '.join(switched) if switched else '—'}")


def bd_tables(race_rows: list[dict], calibration: dict,
              graded: list[dict]) -> None:
    """Depth × rule matrices for gate 5, and the race's move for gate 4."""
    pools_by_rule = {rule: bd_pools_at(race_rows, calibration, rule)
                     for rule in D_RULES}

    def levels(row: dict, pool: dict, ql: float, qu: float) -> tuple[float, float]:
        mae = stage4._pooled(pool["mae_pct"], row["probabilities"])
        mfe = stage4._pooled(pool["mfe_pct"], row["probabilities"])
        return (stage4.mixture_quantile(mae, ql),
                stage4.mixture_quantile(mfe, qu))

    print("\n## B+D combined — gate 5 headline over the 29 "
          "(rows = depth, columns = per-class rule N)\n")
    print("| depth | " + " | ".join(
        f"N={rule}{' (today)' if rule == 30 else ''}" for rule in D_RULES)
        + " |")
    print("| :--- | " + " | ".join("---" for _ in D_RULES) + " |")
    for name, ql, qu in DEPTH_CANDIDATES:
        cells = []
        for rule in D_RULES:
            pools = pools_by_rule[rule]
            hits = 0
            for row in graded:
                floor_pct, ceil_pct = levels(row, pools[row["tid"]], ql, qu)
                floor_inr = row["basis"] * (1.0 + floor_pct / 100.0)
                ceil_inr = row["basis"] * (1.0 + ceil_pct / 100.0)
                hits += floor_inr <= row["lo"] and row["hi"] <= ceil_inr
            cells.append(pct(hits, len(graded)))
        print(f"| {name} | " + " | ".join(cells) + " |")

    print("\n### At the proposal depths — full readings per rule\n")
    print("| depth | rule | headline (of 29) | representative (of 23) | "
          "tail (of 6) | still missing | median width |")
    print("| :--- | :--- | ---: | ---: | ---: | :--- | ---: |")
    for name, ql, qu in DEPTH_CANDIDATES:
        if name not in ("P4", "P6"):
            continue
        for rule in D_RULES:
            pools = pools_by_rule[rule]
            hits = 0
            misses: list[str] = []
            widths: list[float] = []
            rep_hits = rep_n = tail_hits = tail_n = 0
            for row in graded:
                floor_pct, ceil_pct = levels(row, pools[row["tid"]], ql, qu)
                floor_inr = row["basis"] * (1.0 + floor_pct / 100.0)
                ceil_inr = row["basis"] * (1.0 + ceil_pct / 100.0)
                ok = floor_inr <= row["lo"] and row["hi"] <= ceil_inr
                hits += ok
                widths.append(ceil_pct - floor_pct)
                if row["slice"] == "representative":
                    rep_n += 1
                    rep_hits += ok
                else:
                    tail_n += 1
                    tail_hits += ok
                if not ok:
                    misses.append(row["tid"])
            print(f"| {name} | N={rule}{' (today)' if rule == 30 else ''} | "
                  f"**{hits} → {pct(hits, len(graded))}** | "
                  f"{rep_hits} → {pct(rep_hits, rep_n)} | "
                  f"{tail_hits} → {pct(tail_hits, tail_n)} | "
                  f"{', '.join(misses) if misses else '—'} | "
                  f"{statistics.median(widths):.1f}% |")

    # The tail slice alone, per rule, at both proposal depths: this is Q3's
    # B/B′ price — what the tail can honestly claim at a signed depth.
    print("\n### Tail slice only, per rule — the B / B′ tail price\n")
    print("| depth | rule | tail (of 6) | clears any own floor ≤ | "
          "tail still held out |")
    print("| :--- | :--- | ---: | ---: | :--- |")
    tail_rows = [r for r in graded if r["slice"] == "tail"]
    for name, ql, qu in DEPTH_CANDIDATES:
        if name not in ("P4", "P6"):
            continue
        for rule in D_RULES:
            pools = pools_by_rule[rule]
            held: list[str] = []
            for row in tail_rows:
                floor_pct, ceil_pct = levels(row, pools[row["tid"]], ql, qu)
                covers = (row["basis"] * (1.0 + floor_pct / 100.0)
                          <= row["lo"]
                          and row["hi"]
                          <= row["basis"] * (1.0 + ceil_pct / 100.0))
                if not covers:
                    held.append(row["tid"])
            hits = len(tail_rows) - len(held)
            value = pct(hits, len(tail_rows))
            print(f"| {name} | N={rule}{' (today)' if rule == 30 else ''} | "
                  f"**{hits}/{len(tail_rows)} → {value}** | {value} | "
                  f"{', '.join(sorted(held)) if held else '—'} |")

    # Gate 4: the pools feed `race_probability`, so a D-world moves the race.
    committed = json.loads(
        (TESTS_DIR / "proof" / "baseline_scorecard.json").read_text(
            encoding="utf-8"))["gates"]["Race Calibration Brier Score"]["value"]
    print("\n## B+D on gate 4 — the pools also feed the race (measured)\n")
    print("| rule N | race Brier over 31 | representative (of 25) | tail (of 6) |")
    print("| :--- | ---: | ---: | ---: |")
    for rule in D_RULES:
        pools = pools_by_rule[rule]
        squared: list[tuple[str, float]] = []
        for row in race_rows:
            race = round(stage4.race_probability(
                row["probabilities"], pools[row["tid"]],
                row["target_pct"], row["stop_pct"]), 4)
            outcome = 1.0 if row["resolution"] == "WIN" else 0.0
            squared.append((row["slice"], (race - outcome) ** 2))
        value = sum(sq for _sl, sq in squared) / len(squared)
        rep = [sq for sl, sq in squared if sl == "representative"]
        tail = [sq for sl, sq in squared if sl == "tail"]
        note = ""
        if rule == 30:
            if abs(value - committed) > 1e-12:
                raise SystemExit(
                    f"N=30 race Brier {value!r} != committed {committed!r} "
                    "— the gate-4 anchor drifted; discard the table")
            note = " — equals the committed baseline"
        print(f"| N={rule}{' (today)' if rule == 30 else ''} | "
              f"**{value:.3f}**{note} | {sum(rep) / len(rep):.3f} | "
              f"{sum(tail) / len(tail):.3f} |")


def pairwise_table(graded: list[dict], calibration: dict, rule: int = 20) -> None:
    """One-off side-by-side: the SAME trade's P4 cone under its own sector
    block vs under the pooled pools, both envelopes' containment of the
    realised forward range, and which pool rule N would actually pick.

    Purely diagnostic: the rule is patched only to call `samples_for` — the
    resolver the proposal's option D would re-spec — and restored before the
    call returns. Nothing here reaches the tables the seam pins.
    """
    ql, qu = 0.025, 0.975          # P4, the recommended depth
    blocks = calibration.get("samples_by_sector") or {}

    def env_inr(row: dict, pool: dict) -> tuple[str, str]:
        """Envelope in INR at [ql, qu] and whether it covers the realisation."""
        if pool is None:
            return "—", "—"
        mae = stage4._pooled(pool["mae_pct"], row["probabilities"])
        mfe = stage4._pooled(pool["mfe_pct"], row["probabilities"])
        floor_pct = stage4.mixture_quantile(mae, ql)
        ceil_pct = stage4.mixture_quantile(mfe, qu)
        floor_inr = row["basis"] * (1.0 + floor_pct / 100.0)
        ceil_inr = row["basis"] * (1.0 + ceil_pct / 100.0)
        covers = floor_inr <= row["lo"] and row["hi"] <= ceil_inr
        return (f"{floor_inr:.2f}..{ceil_inr:.2f}", "✅" if covers else "✗")

    saved = stage4.MIN_SECTOR_ATOMS_PER_CLASS
    stage4.MIN_SECTOR_ATOMS_PER_CLASS = rule
    try:
        picks = {r["tid"]: stage4.samples_for(calibration, r["sector"])
                 for r in graded}
    finally:
        stage4.MIN_SECTOR_ATOMS_PER_CLASS = saved

    print(f"\n## One-off side-by-side — P4 cone under the row's OWN sector "
          f"block vs pooled, rule N={rule} \n")
    print("| trade (symbol) | sector | realised low..high | "
          "own-block envelope covers? | pooled envelope covers? | "
          "rule N picks | switched vs N=30? |")
    print("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
    for row in sorted(graded, key=lambda r: r["tid"]):
        block = blocks.get(row["sector"])
        sector_txt, sector_cov = env_inr(row, block)
        pooled_txt, pooled_cov = env_inr(row, calibration["samples"])
        pick = ("pooled" if picks[row["tid"]] is calibration["samples"]
                else "sector")
        switched = (pick == "sector") != (row["pool_name"] == "sector")
        print(f"| {row['tid']} ({row['symbol']}) | {row['sector'] or '—'} | "
              f"{row['lo']:.2f}..{row['hi']:.2f} | {sector_txt} {sector_cov} | "
              f"{pooled_txt} {pooled_cov} | {pick} | {'**yes**' if switched else 'no'} |")
    off = [r["tid"] for r in sorted(graded, key=lambda r: r["tid"])
           if ("pooled" if picks[r["tid"]] is calibration["samples"]
               else "sector") == "pooled" and not r["pool"]]
    print(f"\n*Diagnostic only — rule N={rule} is patched for this call and "
          f"restored immediately; no threshold, level or pool rule moves.*"
          + (f" Rows reading sector today that N={rule} would send back to "
             f"pooled: {', '.join(off) if off else 'none'} — the resolver is "
             "monotone in N, so this is expected to be an empty list."
             if True else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairwise", action="store_true",
                        help="append the one-off sector-vs-pooled side-by-side "
                             "at P4 under rule N=20 (see module docstring)")
    args = parser.parse_args(argv)
    race_rows, calibration, window0 = grade_scope()
    graded = [r for r in race_rows if r["in_window"]]
    attach_pools(graded, calibration)
    depth_table(graded)
    rescope_table(graded, calibration)
    tail_pool_detail(graded, calibration)
    bd_census(calibration, graded)
    bd_tables(race_rows, calibration, graded)
    if args.pairwise:
        pairwise_table(graded, calibration)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())