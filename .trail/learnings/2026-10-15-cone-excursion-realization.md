# Stage-4 cone: from close-path to bar-path atoms (2026-10-15)

## What the owner asked for
Rebuild `price_cone_p10_p50_p90` from the excursion mixtures so the envelope *is*
the forward path gate 5 grades, then re-record the evidence and re-measure.

## What was actually wrong (two layers, not one)
1. **Decision 7 (2026-10-14)** had already fixed *which mixture* each level
   inverts (terminal interval → path envelope). That change was real but
   incomplete: it never stated the **realization** of the excursion atoms.
2. The shipped implementation measured the window's **closes**
   (`min(0, min(close/entry-1))`), while gate 5 (`evals.md` §3 #5) grades the
   **`forward_tape.json` bars** — `min(low)`/`max(high)`. A close path sits
   strictly inside its bar path, so the envelope was built from a milder object
   than the one its grader measures. The corpus vintages
   (`tests/fixtures/history/<SYMBOL>_<YYYYMM>.json`) *do* carry
   `open/high/low/close`; `load_stock_corpus` was throwing the extremes away.

## Decisions
- **ADR-011 decision 8 / BRD §5.2**: atoms are the 22-session window's worst
  **low** and best **high** against the entry, terminal from the closing session.
  `forward_outcome` takes the session series; the invariant
  `mae ≤ terminal ≤ mfe` still holds, so decision 7's monotonicity argument is
  unchanged. Features still read closes (the index panel is close-only).
- **`close_path_outcome`** exists beside it solely so the *cost of the choice* is
  measured inside the artifact: `provenance.cv` now reports three readings of one
  envelope — **75.96% bar path / 82.18% close path / 88.93% terminal**.
- The corpus loader **drops** a session without usable `high`/`low` and counts it
  (`provenance.corpus.incomplete_sessions`) — never coerced to a close-based
  excursion, which is the exact regression being fixed.

## Measured
- Out-of-fold over the same 6,261 rows: Brier **0.265303 unchanged** (the
  direction model reads closes/classes), bar-path coverage **49.5% → 75.96%**
  (per fold 44.85 / 67.57 / 89.61 / 77.22 / 78.73%), close path 82.18%,
  terminal 88.93%.
- **Gate 5 still 0/6 (0.0%)**, gate 4 still 0.636, exit 1 — published, not tuned.
  Every graded row's `live_price` equals its T_rec session **open** (±0.00%
  against the row's own sourced prefix; `authoring.entry_basis: open`), so the ₹
  comparison is sound. Grading the same envelopes on the **close path is also
  0/6**, so the miss is the model's on adversarial 2020–2024 tapes, not the
  realization's.

## Ruled out (don't re-derive these)
- **"The anchor is off the tape's price scale."** Checked row by row: it is not.
  `live_price` == the T_rec session's open exactly for all six; the T_rec *close*
  is 3–17% lower (these are intraday advisories priced at the open). An earlier
  pass that used the close as the tape's reference produced a bogus "2/6 neutral,
  4/6 close" decomposition — wrong base, not a real seam.
- **"Widen the envelope to cover the shocks."** That is fabrication, not a
  threshold call: the pools are sourced from a 2021–2026 window that contains no
  such shock.

## Later the same day: gate 5's *scope* (owner command)
- The gate now grades only the cone-labeled records whose **pinned forward tape
  lies entirely inside `provenance.corpus.row_window`** (evals.md §3 #5); a row
  outside is named on `gate.coverage` with its span and excluded from the
  denominator — never a miss. Window comes from the shipped calibration
  (`run_evals.trained_corpus_window`), spans from `dataset_refs.forward_tape_span`
  (the same tape file the `$derive` rules read — one file, one set of bars).
- Split on this benchmark: **4 graded, 2 ungraded** (`SCEN-001` 2020-03-24..,
  `SCEN-002` 2020-03-25.. — both open before the window opens 2020-04-09).
  **All four graded rows still miss → verdict unchanged at 0.0% (FAIL).** The
  scoping made the denominator honest, not kinder; say that first whenever this
  change is described, or it reads as threshold-shopping.
- **Correction found while writing it**: the docs claimed the pools were
  "2021–2026". The artifact says `row_window = [2020-04-09, 2025-10-20]`.
  Fixed in evals.md §2 and actionplan — always read the window off the artifact,
  never off prose.
- Gate 4 (Brier, 0.636 over six predictions) was **not** scoped: the owner asked
  for gate 5. The two out-of-window tapes are still graded there — disclosed
  asymmetry, and the obvious next question.

## Watch out for
- The same artifact straddles the 80% floor depending on the reading
  (82.18% close vs 75.96% bar). If the owner ever re-scopes gate 5's realization,
  that is a spec change and must be visible as one.
- **Wall-clock leakage in seams**: `10_daily_triage`'s `audited_on` defaults to
  `date.today()`, and `TestTriageRerunsStageSeven.setUp` tracked positions under
  the machine clock while the test asserted an explicit `--as-of` day. It passed
  all of 2026-10-14 and broke at the date rollover (4 rows where 2 were asserted).
  Fixed by pinning `AUDIT_DAY` and adding a seam that moves the day on purpose
  (`test_a_later_audit_day_appends_instead_of_replacing`). Any seam that asserts a
  row count while a default supplies the date has this bug latent.
- `tests/proof/manifest.sha256` covers `tests/proof/**` only: retraining the
  booster does not require a re-record, but re-recording the stage-4 payloads
  does require a baseline re-freeze (`record_stage4_evidence.py --apply`).
