# 2026-10-14 — panic-regime seeding + macro sizing wiring

## What was done

1. **Stage 9 macro sizing (ADR-010 decision 6, frozen in TICK-009 §3 first):**
   `breadth_regime: RISK_OFF` or `fii_dii_flow_bias: NET_SELLING` read from a
   non-waived `out_7_regime.json` caps a would-be `GO` at `GO_WEAK` (the §6.4
   size-cap lever, same 50% cap as the earnings-window rule, `confidence_deficit`
   names each source, never compounds, never a veto). TDD: red seam in
   `tests/test_9_decision_engine.py` first, then the cap branch in
   `scripts/9_decision_engine.py` (30 tests green).
2. **Gate 2 registers `panic_regime` with AND semantics:** `BREAKER_COMBINE`
   dispatches per breaker; the default stays `"any"` (OR) so the four existing
   breakers' census keys are byte-for-byte unchanged. `rule_exercises` keys a
   conjunction by the PAIR's verdict: pair fires ⇒ both arms trap exercises at
   own values; pair holds ⇒ both arms `:clear` (a breadth print under 30 with a
   calm VIX exercised the pair without tripping it — the arm-level trip would
   have misgraded the AND as an OR).
3. **`regime_shock` seeded from catalog `SCEN-002`** (Covid Panic Liquidity
   Freeze): disclosed `uvest-market-context/1` bundle authored at the catalog's
   own stated bounds (VIX 80.5 for ">80", breadth 4.5% for "<5% bullish",
   flow -1400 Cr × 5 = NET_SELLING), recorded through
   `7_sector_macro_regime.py --record` via the scaffolder, golden row #53 with
   `$ref`s + `$derive`s, quota `regime_shock: 1`, floor re-frozen 40→42 AFTER
   the dataset write (V12 — the scaffolder freezes before the seeder writes
   golden, so the seeder must call `re_freeze_baseline()` again).

## Ruled out

- **Per-arm trip as census credit for AND-breakers** (my first implementation):
  fails exactly at the non-firing rows — narrow rows would report
  `panic_breadth_below_30` as tripped while the breaker never fired. The pair
  decides both arms' key; OR-breakers untouched.
- **`disk_bundle_path` as a backing mechanism** — it does not exist. Backing is
  `(trade_id, artifact)` targets from `dataset_payload_targets` / `$ref`
  resolution (`gate_payloads`), plus `proof_root.record()` folding manifest
  declarations into the proof ledger (`load_proof`).
- **Per-scenario custom breakers / probe roster for the panic class** — the
  catalog already carried `breaker_pending_stage_7: [panic_regime]` on
  SCEN-001…008, so the existing pending→declared promotion path
  (`declare_hazard_evidence`'s twin, `declare_regime_evidence`) was the honest
  fit.

## Watch out

- **Interpreter:** run everything on `.venv/Scripts/python.exe` (system Python
  lacks `fastdtw`); capture exit status directly or with pipefail, never a bare
  `| tail`.
- **V7 reads the regime fixture bar-by-bar** (unlike stage-5/6 bundles): every
  `date`-keyed series point must be ≤ `truncation.cutoff` (2020-03-24 for
  SCEN-002). Schema doc row added for `replay_inputs.regime_fixture`.
- **V10 replay** for stage 7 lives in `tests/test_proof_manifest.py`
  (`RERUNNABLE[7]` + `--regime-fixture`); V3/V4/V5/V7 all consume
  `validate_manifest._replay_paths` — one append covers them.
- **EVAL-002 (hazard boundary probes)** still has all its boxes unticked and
  its line-39 criterion says "total 52" / line-40 "floor at 40" — now 53/42.
  Left alone (another ticket's frozen criteria; its substance appears shipped),
  but a future session working that ticket should re-express the counts.
- Gate-2 trap semantics: a recorded trap row whose breaker does NOT fire is a
  **violation** ("breaker value not encoded"); the seeded row must encode both
  arms.

## Verification (2026-10-14)

- Invariant suite 419/419; evals scorecard SUITE PASSED exit 0 —
  `regime_shock 1/1 graded, 2 distinct rule exercise(s)`, 53/53 proven,
  distribution verified, rule floor 42.
- Doc-sensitive seams (contract consistency, prompt integrity, repo hygiene)
  re-run green after the 52→53 doc sync (evals.md, BRD, AGENTS.md, EVAL-001,
  TICK-007/009, actionplan, schema doc).
