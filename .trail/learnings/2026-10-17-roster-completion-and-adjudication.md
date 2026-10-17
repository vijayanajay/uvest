# 2026-10-17 — Roster completion (stage-4 everywhere) + gate-8 adjudication

## What was decided / shipped
- **ADR-011 decision 16** (domain.md): three closures around one retrain —
  1. `SECTOR_INDEX_MAP` pins `TITAN → ^CNXCONSUM`, `BAJFINANCE → ^CNXFINANCE`, each read out of
     NSE's own index-constituent CSVs (named in `_sector_map.py`'s note; tests read them back).
     A third/fourth *source-backed* pin, never a guess.
  2. `build_index_corpus` detects the TITAN-sector **2025-06-30 → 2025-09-02** archive hole itself
     (no hardcoded anchor) and straddles it with an anchor vintage, so rows behind the hole drop
     under decision 14's gap-free feature rule instead of training across it. `series_served 14/0/0`.
  3. `GB-2025-0031`/`0032` (tape 2025-10-16..2025-11-18) join the roster anyway: their tapes'
     relation to `row_window` is **gate 5's §3 #5 scope rule** (named with spans on the coverage
     line), not a bundle defect. One rule, three consumers (validator/gate/seeder).

## Measured after the retrain (every number traces to an artifact)
- Corpus 12,969 → **12,451** gap-free trained rows; classes **6,783 / 5,462 / 206**; 11 sector
  block sets; panel **14 served / 0 absent**. OOF race Brier **0.3232** vs base **0.2480**
  (still no skill — published as measured). Cone OOF bar path **77.81%** (close 83.23, terminal
  89.72); pooled pool share **28.12%** (71.88 own-sector).
- Stage-4 evidence on **31 records** (25 sourced campaign bundles + 6 authored regime);
  context fixtures `origin: 25 recorded + 6 authored`.
- Gates re-banked at unchanged thresholds: gate 4 **0.376 → 0.410** over 31 (rep 25 = 0.468,
  tail 6 = 0.165); gate 5 **51.9% → 55.2%** over 29 of 31 (rep 23 = 60.9%, tail 6 = 33.3% —
  SCEN-002/SCEN-006 cones widened into coverage); alignment unchanged **65.5%** (same 20
  divergences: GO→GO_WEAK ×15, AVOID→WAIT ×5).
- **Floor test is now real:** 15 GO rows score the full Quant & ML block (15/15 recorded) against
  a **45.0-point** in-play ceiling — 14 below 55%, `GB-2023-0024` clears at **56.3%** and stays
  GO_WEAK on its effective score (32.5 of the 40-point line).
- Tail-slice diagnosis (from the scorecard itself): SCEN-008 −43.48% = 0.2nd pct pooled adverse;
  SCEN-003 −17.05% = 1.7th ^NSEBANK; SCEN-004 −17.57% = 2.0th pooled; SCEN-001 +63.17% =
  100.0th pooled favourable. Close-path grading covers 3/6 (SCEN-004's miss is an intrabar dip —
  decision 8's lows/highs basis is exactly why bars are the grade).

## Adjudication tool
- `tests/evals/adjudicate_divergences.py` → `reports/adjudication_go_divergences.md`,
  four sections: Summary (binding-constraint tally 14 floor + 1 waiver, closest/furthest pp),
  Sources census (scored/waived/absent with payload words), floor table (effective 49.99),
  per-record blocks (signed pp distance, 45.0 ceiling with §10.4 removals itemised, engine's
  own deficit sentence, "what would flip it", per-source states). `SCEN-901` abstention named.
- Seam: `tests/test_adjudicate_divergences.py` (7) — byte-compare of the stored report vs a
  rebuild from committed payloads; the 15 record asserts fire together; a **stale report fails
  the suite anywhere**, so evidence changes cannot out-run the adjudication.

## Watch out for
- **CRLF vs LF when editing docs**: `evals.md`/`actionplan.md` are CRLF (`oldString` must carry
  `\r\n` when crossing lines); `domain.md`/`scenario-manifest-schema.md` are LF. Mid-line fragment
  edits are safe in both.
- Citing seam/test names in prose without grepping them first silently invents tests — one made
  it into a draft actionplan entry (`TestTheTrainWindowGradesEveryBenchmarkTape`) and was caught
  only by a grep. Always grep the identifier before citing it in docs.
- A truncated `str_replace` newString once spliced a partial entry into `actionplan.md` and ate the
  next entry's headline; the repair anchor is `grep -n "brouh"`-style: inspect the region with
  `sed -n '<a>,<b>p'` before writing the fix, and re-grep both neighbouring entry heads after.
- Scorecard output files (`tests/_reg.txt` etc.) are scratch — delete them; only
  `tests/proof/baseline_scorecard.json` and `reports/*` are artifacts.
- `run_evals.py` has no `--skip-report-regen` flag (argparse exits 2 with usage); the flags are
  `--benchmark`, `--run-record`, `--regression-exit`, `--re-freeze-gates`.