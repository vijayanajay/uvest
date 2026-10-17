# 2026-10-17 — Recording stage-9 decision evidence so the report's badge is real

## What was asked

Record stage-9 decision evidence for the golden records so the report's 7-state
verdict badge, composite score, calibrated P(win) and expectancy are real
instead of `verdict pending stage-9 evidence`.

## What was landed

1. **`tests/evals/record_stage9_evidence.py`** — the third of the evidence
   recorders (after `record_stage4_evidence.py` and `record_stage8_evidence.py`),
   same four moves: run the real stage with `--record`, declare the artifact on
   the proof row, `$ref` it from `script_outputs`, republish `manifest.sha256`
   and re-freeze the baseline (V12). Its inputs are the row's **own already
   recorded payloads in stage order**, so the verdict is a function of declared
   evidence only. Eligibility is decided **before** the stage runs: four proof
   rows (`SCEN-005`, `SCEN-010`, `SCEN-901`, `SCEN-902`) have no golden record,
   and running first would have left an artifact nothing declares, references or
   grades — that ordering bug was caught in dry run, not after `--apply`.
   Result: **58 rows recorded**, 596 hash entries, baseline re-frozen.
2. **Stage 9 joined `test_proof_manifest.RERUNNABLE`** — `fresh_stage_stdout`
   now supplies the row's recorded payloads (minus stage 9's own entry) as
   `--payload` flags, so V10 byte-compares every recorded `out_9_decision.json`
   against a fresh run. That check passed across all 58 artifacts (306 s run —
   it re-runs every recorded stage, so expect it to dominate the suite's time).
3. **The badge defect was in the report, not only in the evidence.**
   `verdict_block` read `decision.get("score")`, a key stage 9 has never
   emitted (it emits `final_score`, `max_possible_score`, `effective_score_pct`),
   so even with a payload the composite-score row silently vanished. Fixed to
   transcribe the artifact: `final/max` as §13.2 draws it, plus the effective %,
   `p_win` and `expectancy`, all through `fmt`. A denominator the artifact does
   not carry is never invented (the score is still shown alone if `max` is
   absent; the row disappears only when there is no score at all).
4. **Regenerated** the 58 reports and the tracked prototype
   (`report_regen.py --prototype`) — `git clean -Xdf reports` first, because the
   regenerator replays into a sidecar and records a `failed` row when the bytes
   on disk differ instead of overwriting them.

## Findings published (not tuned)

* **Badge census: 38 `AVOID` / 5 `WAIT` / 15 `GO_WEAK` / 0 `GO`.**
* **`GO` is structurally unreachable in this corpus**: `out_3_dtw.json` (12.5
  weight) is recorded on **zero** rows, so every row carries a waived source and
  BRD §10.4's cap makes `GO_WEAK` the ceiling. The 15 rows authored `GO`
  therefore compute `GO_WEAK` (two additionally cite a P(win) just under the 55%
  floor). Expectation-vs-computed is published, never tuned — there is no gate
  that requires `expected_verdict` to equal stage 9's verdict, and inventing one
  would be a tuning hook, not a check.
* **Five `AVOID` expectations compute `WAIT`** (`GB-2022-0008/0010/0011/0012/0013`)
  because the inverted-RR ladder (§10.3 #2) sees a limit order that restores
  R:R ≥ 1.5 — the ladder's own exception, working as frozen.
* Arithmetic spot-checks tie out: e.g. SCEN-013's `14.0/35.0` = weights of
  out_1 (20) + out_6 (15) with out_8 waived → 40% effective →
  `p_win = 0.55 + (40−50)×0.004 = 0.51` ✓, and `E = 0.51×2.0 − 0.49 = 0.53` ✓
  (2.0 is the frozen class-shape R:R of the hazard rows).

## Ruled out (and why)

* **Promoting a verdict field into the golden dataset.** `ENGINE_CLAIM_STAGES`
  is frozen (cone, race probability, limit MAE), and the badge reads the
  `$ref`'d payload through `dataset_refs.materialize` — a copied field would be
  a second copy of the same bytes, exactly the drift `$ref` exists to prevent.
* **Skipping the four non-golden rows silently.** They are named in the tool's
  skip list and in `evals.md`, so the 62-vs-58 row difference is disclosed.
* **Changing `expected_verdict` to match the engine.** That field is authored
  point-in-time evidence (scenario-manifest-schema E8) and `test_proof_manifest`
  pins it to the immutable catalog; rewriting it to fit stage 9 would be the
  outcome-fitting the scorecard exists to surface.

## Watch out for

* **A badge test graded against a record needs that record's own canonical
  trade.** The first version rendered the synthetic `INFY 2026-01-05` trade and
  then diffed the header against `GB-2022-0001` — two `2026` claims, correctly
  flagged as unbacked. The harness, not the product, was wrong.
* **Dry run before `--apply`, and make eligibility mean it.** The `--apply`
  path writes artifacts; any check that only exists in the dry run is a check
  that runs too late.
* **`docs/agents/evals.md` is one long line per paragraph** — an anchor built
  from "the sentence after next" silently fails; find the *codepoints* after
  the anchor instead of trusting an earlier grep's interleaved output.
