# 2026-10-16 — Stage 8 implemented (TICK-008); gate 6 armed and graded at 71.4% (banked red)

## What was decided / built

- `scripts/8_dhan_precision.py` (stage 8) exists per TICK-008/BRD §4 Core
  Prediction 3: daily-tier zone = AVWAP from the swing-low pivot + demand-block
  support; `optimal_limit_buy` is the band midpoint. The Dhan Tier-2 half is
  **operator-gated behind `--live`** (never passed by the validation chain), so
  every recorded artifact is byte-deterministic and stage 8 joined
  `test_proof_manifest.RERUNNABLE` — a fresh offline run is byte-compared to
  the committed `out_8_dhan.json`.
- `tests/evals/record_stage8_evidence.py` mirrors the stage-4 tool: records the
  artifact on all 62 rows (58 promoted; the 4 non-dataset rows listed),
  declares it on the proof row, `$ref`s it from `script_outputs`, reads
  `limit_entry_price` out of the recorded bytes, and writes `limit_mae_pct` as
  a `$derive` on the row's own pinned tape.
- Gate 6 (Optimal Limit Slippage Reduction) now **grades**: 71.4% (30/42
  filled limits beat market MAE; 16 limits never traded to the zone → derived
  null → excluded from the denominator, never free wins). Below the ≥75%
  criterion → red, **banked deliberately** via `--re-freeze-gates` (9 gates)
  under the baseline-aware pre-commit policy. Net expectancy −0.8%.
- The threshold proposal for the Brier 0.18 / coverage 80% gates shipped as
  `docs/agents/proposal-gate-thresholds.md` (Q1–Q3, options A–D, recommendation
  C→D→A, B last), linked from actionplan.

## What was ruled out / bugs caught

- **Promote-gate bug (mine):** initially nulled `limit_entry_price` whenever
  `waived` was true — but `waived` reports the *Dhan half* (`dhan:not-requested`
  offline), while the daily zone still ships. Gating the claim on
  `optimal_limit_buy is None` (whole-payload waiver) instead. Lesson: a flag's
  meaning must match what the claim depends on.
- The `dhan_cache.db` write path had lost its `commit()` when `with connect`
  was replaced; the depth parser only understood one schema (both fixed, seam
  caught them).
- AVWAP must stay **unrounded** (stage-2 convention: sl_atr_ratio derives from
  the unrounded ATR internally).
- Five test files carried stale invariants from the "stage 8 unimplemented"
  era (recorder's unregistered-stage example → 13; pipeline chain order + skip
  disclosure; run_evals scorecard/provenance strings; distribution provenance
  asserting `limit_mae_pct is None`; chain-contract comment). All updated to
  the new contract, not weakened.

## What to watch

- Gate 6's red at 71.4% with **net expectancy −0.8%**: the zone midpoint is a
  coin on filled trades. The obvious next lever is the limit-band policy
  (AVWAP side of the band vs midpoint) — but any rule change must go through a
  spec freeze + evidence re-record, and the 16 unfilled rows mean the
  denominator will move with it.
- A *worse* gate-6 reading (or a re-grade) blocks the baseline-aware hook until
  deliberately re-banked — that's the policy working.
- `.env.example` (BRD §8.1) is the one remaining unchecked stage-8 actionplan
  item; the Dhan half stays unexercised live until credentials exist.

## Follow-up (same day) — the unfilled-limit investigation

Measured all 58 records gate-exact (harness reproduces the banked 71.4% / −0.8%
before scoring any alternative). Three findings, formalized in
`docs/agents/proposal-limit-band-policy.md`:

- **12/58 zone midpoints sit at/above the market entry** — exactly the 12
  worse fills. Lemma: a *filled* limit beats market MAE iff `limit < entry`
  (pure algebra), so an above-entry "limit" is a market order that can only
  grade worse — a rule defect, not an engine failure. A discount clamp makes
  every clamped policy 100%-better **by construction**, so the gate's
  falsifiable content moves to the fill rate (floor ≥ 60% proposed).
- **The net-expectancy line is outlier-dominated**: −0.8% mean vs +10.0%
  median; SCEN-016 contributes −530% alone (1.17% market-MAE denominator).
- **The 16 unfilled**: 4 are within +1% near-misses (2 within 10 bp), 12 are
  deep crash gaps (max ADANIENT +13.68%); 9/16 would fill at the band's high
  side, **0/16** at the low side — going deeper never rescues an unfilled
  limit. AVWAP-side clamped is the deepest policy (+43.5% net / +42.9% med)
  but fills only 56.8%; midpoint clamped fills 65.2% at +31.1%.
- Nothing implemented at investigation time: spec-freeze first (Iron Rule).
  Q1–Q3 on the owner's desk; recommendation B (clamp + midpoint + criterion
  amendment).

## Follow-up 2 (same day) — proposal B signed and shipped

The owner signed Q1–Q3 as B; implemented end-to-end the same day:

- Stage 8's `_apply_discount_clamp`: midpoint ships only when strictly below
  `entry_price`; otherwise `optimal_limit_buy: null` + `zone_above_market:
  true` (levels still ship — a clamp is a disclosure, not a waiver), `null`
  flag when the entry is unevaluable (tri-state). The clamp rides on the
  *final* zone (post-Dhan-refinement).
- Gate 6: `Gate.floor` — a second criterion that can only FAIL a gate and
  names itself in the display string; `MAE_FILL_RATE_MIN = 60.0`; mean AND
  median net on a `coverage` line (the renderer hides `detail` on PASS —
  numbers that must survive a green gate belong in coverage).
- Evidence re-recorded (12 rows clamped), gates re-banked deliberately (9).
  Gate 6: PASS 100.0%, 30/46 filled (65.2%), +31.1% mean / +24.7% median —
  exactly the proposal's measured P5 row. Suite 621/621.
- Watch: the 16 unfilled (4 near-misses) are the next lever; the AVWAP-side
  anchor (P8) is the signed-off alternative if fill rate ever drops under 60%.
