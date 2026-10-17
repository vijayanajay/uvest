# Learning: Option-C representative-set campaign (gates 4/5 re-scoped, thresholds untouched)

- **Date:** 2026-10-16
- **Scope / Files:** `tests/evals/seed_context_records.py` (new), `tests/test_seed_context_records.py` (new), `tests/run_evals.py` (gates 4/5 slice disclosure), `tests/test_run_evals.py` (`TestGradedSetSlices`), `tests/evals/record_stage4_evidence.py` (reused), `tests/evals/golden_dataset.json`, `tests/proof/*` (21 bundles + 27 recorded `out_4_ml.json`), `docs/agents/proposal-gate-thresholds.md` §6, `docs/agents/evals.md`, `docs/agents/scenario-manifest-schema.md`, `actionplan.md`.

## Decided

- **Q1–Q3 signed as owner command**: keep Brier ≤ 0.18, keep coverage ≥ 80%, re-scope the *graded set* to representative + tail slice (option C). The signature authorizes the campaign, not a threshold edit.
- **Bundles are sourced, not authored.** The representative set's `uvest-market-context/1` bundles are built from the committed tier-1 index corpus (`tests/fixtures/history/indices_daily.json`), `origin: recorded`, truncated to `[first session of the row's own OHLCV prefix, T_rec]`, `close → value` as the only transformation. Authored trajectories were ruled out because gate 4's 20-session beta/correlation features would have fed fabricated market context into the very number the gate grades. Breadth and FII/DII are absent from that corpus and named absent — a stage-7 run over such a bundle waives *naming* them (BRD §7.4), never invents one.
- **Two honest exclusions, both printed and both in `meta.notes`.** `TITAN` and `BAJFINANCE` get no bundle (no pinned sector index — ADR-010 decision 5); `GB-2025-0031`/`GB-2025-0032` were **retired** mid-campaign (bundle deleted, declarations and `$ref` dropped, claims back to `null`) because their tapes end 2025-11-18, past the trained corpus window — Q3 signed an *in-window* set and `TestConeCoverageScope.test_the_covid_tapes_are_inside_the_scope_and_all_six_grade` asserts the corpus reaches every cone-labeled tape. Final: **21 bundled, 27 records with recorded stage-4 claims** (21 representative + 6 tail).
- **Slice disclosure is frozen by class, never by outcome.** `run_evals.REPRESENTATIVE_CLASSES` (geometry advisories) vs `TAIL_CLASSES` (hazard/distribution/crisis), printed on both gate lines via `Gate.coverage` so it survives a PASS.

## Ruled out

- Keeping the two out-of-window rows and weakening the corpus-reach seam into a "named but graded" check — that would trade an absolute for a disclosure.
- Authoring synthetic macro trajectories for the clean rows (the `seed_regime_records` pattern) — right for crisis rows whose trap *states* VIX/breadth levels, wrong for rows where the number feeds a graded feature.
- Adding `tight_stop` rows to the campaign: the signature names clean/inverted-rr only; `tight_stop` is in the representative *slice definition* but was not bundled (0 claims, so it cannot move any number).

## Watch Out For

- **`record_stage4_evidence.py` re-records every bundled row on each run** — including the six regime rows — so its promotion note's count is the *total* (27), not the delta. `seed_context_records` purges stale `stage 4 (BRD §5.2) promotion:` notes before calling it, otherwise runs stack contradictory totals in `meta.notes`.
- The per-bundle `recorded/manifest.json` is **append-only by contract** (`_recorder._append_manifest`: "nothing is deleted"), so a retired row keeps its stage-4 ledger line while every claim drawn from it is withdrawn. That is the frozen convention, not a leak.
- A bundle without a pinned sector index (unmapped symbol) waives stage 4 entirely — authoring one anyway only produces `symbol:unmapped` waivers and a guessed ticker risk.
- Measured post-campaign: gate 4 **0.214** (rep 0.137 / tail 0.483), gate 5 **55.6%** (rep 71.4% / tail 0.0%). The representative Brier reading *under* 0.18 is **not** a skill claim — 21 curated BUY advisories with an up-skewed label mix; the model's measured skill stays its out-of-fold **0.2630 vs 0.2447** base rate. The next decision under the signed sequencing is **D** (grade dimension 4 against `prob_target_hit_22d`).
- Re-bank is mandatory after any campaign: `python tests/run_evals.py --re-freeze-gates`, then `--regression-exit` must read `0 regression(s), 0 improvement(s)`.
