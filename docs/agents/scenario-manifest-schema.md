# Scenario Manifest Schema — The Canonical Dataset for the Merged Eval Suite
*Companion spec to [adr-007-eval-harness-consolidation.md](adr-007-eval-harness-consolidation.md). **Status: PROPOSED** — freezes only on owner approval of ADR-007; no runner code may depend on it before then.*

---

## 1. Purpose & Position in the Architecture

One canonical dataset feeds both execution profiles of the merged harness:

| Artifact | Role |
| :--- | :--- |
| `tests/proof/scenario_manifest.json` | The canonical index: one row per scenario, both origins, same schema. |
| `tests/proof/manifest.sha256` | The single source of integrity: `sha256sum`-format hashes of every committed evidence file the manifest references. |
| `tests/proof/baseline_scorecard.json` | Frozen regression reference: gate results, confusion matrix, expectancy, and the two dataset digests (below). |
| `tests/proof/quarantine_register.json` | The exact set of quarantined artifacts (`scenario_id`, `stage`, `artifact`); must equal the manifest's quarantined entries (V11). |
| `tests/proof/fixtures/` (and existing recorded bundles under `tests/fixtures/`) | Immutable point-in-time bars and recorded artifacts — the only data a replay may read. |

The runtime tree in `business_test_cases.md` §2 (`scenarios/{id}/content|outcome`) is **materialized per replay** under `tests/proof/_replay/` (gitignored), not committed: `content/` is assembled from the row's `replay_inputs`, `outcome/` is joined only by the scorer after the verdict process has exited. The committed immutable artifacts are the manifest, the hashes, the baseline, the register, and the fixtures. This keeps the frozen layout exact at runtime while the repo stores only evidence, never volatile sandboxes.

**Naming:** `scenario_id` is the stable primary key. Two ID families coexist during migration — legacy curated rows keep `GB-YYYY-NNNN`; new business-suite rows use `SCEN-NNN`. IDs are never renamed; the merge is at the schema level, not the ID level. Scaffold/pilot rows use `SCEN-9xx`, keeping the frozen catalog's `SCEN-001`–`SCEN-060` slots reserved for the business-suite authoring.

---

## 2. Design Invariants

1. **One row schema, two origins.** `origin: curated_behavioral` and `origin: scan_generated` rows carry identical fields and pass the identical validator. Only the `authoring` block differs.
2. **Evidence rule.** Every graded engine payload is a recorded byte stream; only *inputs* may be authored, and only from point-in-time tape (§3).
3. **Hash-only integrity.** Hashes live in exactly one file (`manifest.sha256`); rows reference files by path and never duplicate a hash. The baseline stores digests of the manifest and the hash file, so any evidence edit invalidates regression comparison by construction.
4. **Blindness by construction.** Row blocks are physically separated: `replay_inputs` may be materialized into `content/`; `scoring` (outcomes) may only be read post-verdict. The validator asserts no outcome path appears in replay inputs (§6, V6).
5. **Absence is a first-class value.** A missing stage artifact is `waived`; it is never defaulted, never fabricated, and never graded as 0. Legacy hand-typed payloads are `quarantined`: counted in the scorecard, excluded from every graded gate, and retired monotonically (§3, E5).

---

## 3. The Evidence Rule (E1–E8)

**E1 — Recorded payloads.** Every `current_trade.json` / `out_*.json` artifact a graded gate reads must be a byte-exact recording of a real stage run, produced by the existing recorder mechanism (`--record <dir>`), with the artifact file written once and identical to that stage's stdout. Rows reference the artifact file; they never inline an extracted subset of its values.

**E2 — Authoring is input-only.** Curation may author the *inputs*: the canonical trade geometry (entry band, stop, target, lot size) and the scenario selection (anchor, symbol, scan rule). Every authored number must derive from ≤T_rec tape (real close, band convention, ATR distance), and the derivation parameters live in `authoring` so they are auditable. Authored values are never engine outputs, ratios, probabilities, or verdicts presented as engine evidence.

**E3 — Re-derivation.** The validator re-derives what it can: (a) recompute any stored derived cache and byte-diff it (E6); (b) for every stage that is offline-runnable against the row's fixtures, re-run it and byte-compare against the recorded artifact. Any mismatch is an integrity failure (`ERR_FIXTURE_INTEGRITY`), never a warning.

**E4 — Absence, not defaults.** `evidence: waived` means the artifact is absent because its engine or data source was unavailable. Tri-state fields stay tri-state on disk (e.g. a tight-stop flag is `true`, `false`, or absent — never defaulted). A waived artifact lowers `max_possible_score` and raises `confidence_deficit` per the frozen waiver rule.

**E5 — Quarantine for legacy hand-typed payloads.** Rows migrated from the current golden dataset whose payloads were hand-typed (not recorded) are tagged `evidence: quarantined` and listed in the quarantine register. Quarantined artifacts may exist on disk for migration continuity, but: (a) no graded gate may read one (the validator fails if it does); (b) the register's row count and its sha256 are frozen into the baseline and may only decrease; (c) the row's gate contribution is `[SKIP]` with the quarantine reason printed. The quarantine count in the scorecard is the migration's completion meter.

**E6 — Derived values are caches.** Stored derived numbers (e.g. forward max/min, realized MAE/MFE) are conveniences: the validator recomputes them from the raw tape and fails on mismatch. If a value cannot be recomputed from a pinned file, it may not be stored.

**E7 — No unlisted evidence.** Every file under the proof root must be either listed in `scenario_manifest.json` (and hashed) or on a short allowlist (manifest, hash file, baseline, quarantine register). Unlisted evidence is smuggled evidence; the validator fails on it.

**E8 — Expectations are point-in-time artifacts.** `expected_verdict`, `gate_focus`, and `quadrant_target` are authored from ≤T_rec evidence only. This cannot be fully verified mechanically, so it is a curation rule enforced socially — and the scorecard deliberately publishes expectation-vs-actual per row, so a systematically outcome-fitted target set surfaces as calibration failure rather than passing silently.

---

## 4. Row Schema

### 4.1 Field Inventory

Top-level envelope: `{"schema": "uvest-scenario-manifest/1", "generated_on": "YYYY-MM-DD", "scenarios": [ … ]}`. Every path below is root-relative to the proof root, and `manifest.sha256` entries use the same namespace (V3).

| Field | Type | Required | Meaning |
| :--- | :--- | :--- | :--- |
| `scenario_id` | string | yes | Stable unique key (`GB-` legacy / `SCEN-` new). |
| `schema_version` | string | yes | `"uvest-scenario-manifest/1"`. |
| `origin` | enum | yes | `curated_behavioral` \| `scan_generated`. |
| `created_on` | date | yes | Row creation date (audit trail). |
| `authoring` | object | yes | Origin-specific inputs only, never outputs. Curated: `{shape:{risk_atr, rr}, source}`. Scan: `{scan_rule, anchor_date, universe_version}`. |
| `classification_tags` | array[string] | no | Canonical tags only, e.g. `TOXIC_SURVIVOR`, `WAIT_TRACKED_SEPARATELY`, `STEWARDSHIP`. Replaces the non-canonical target labels in earlier drafts. |
| `gate_focus` | array[string] | yes | Which gates/breakers this row exists to exercise: `S1`…`S7`, `ZERO_HALLUCINATION`, or a frozen breaker id (e.g. `promoter_pledging_gt_25pct`). |
| `symbol`, `exchange` | string | yes | Instrument identifiers (`NSE`). |
| `instrument` | enum | yes | `CASH` \| `FUT`. Options can never be recorded (ADR-001); the validator rejects them outright. |
| `expiry` | date | FUT only | Contract expiry (FUT rows). |
| `action` | enum | yes | `BUY` (system scope is long-only validation). |
| `recommendation_date` | date | yes | `T_rec` — the point-in-time cutoff for everything in `replay_inputs`. |
| `entry_price`, `entry_range_min`, `entry_range_max` | float | yes | Authored from ≤T_rec tape (entry-band convention). |
| `target_price`, `stop_loss`, `lot_size`, `user_capital_inr` | float/int | yes | Trade geometry (inputs). |
| `replay_inputs.canonical_trade` | path | yes | Materialized to `content/current_trade.json`. |
| `replay_inputs.truncated_fixtures` | array[path] | yes | Materialized to `content/fixtures_truncated.json` (merged OHLCV/bhavcopy/fundamental fixtures). |
| `replay_inputs.truncation` | object | yes | `{cutoff: T_rec, rule: "date <= T_rec", min_bars}` — the harness, not a stage, performs truncation. |
| `recorded_artifacts` | array[object] | yes | One entry per relevant stage: `{stage, artifact, path, evidence}` with `evidence ∈ recorded \| waived \| quarantined`, `reason` required for waived/quarantined. |
| `scoring.forward_tape` | path | yes | Outcome bars strictly `> T_rec`. |
| `scoring.bars` | int | yes | Frozen horizon (22 sessions; a shorter recorded horizon must be declared with a reason). |
| `scoring.resolution_date`, `scoring.resolution_outcome` | date, enum | yes | `WIN` \| `LOSS` \| `TIME`, resolved by the frozen rules (both levels inside the window ⇒ worst case, `LOSS`). |
| `scoring.resolution_basis` | enum | yes | `target_first` \| `stop_first` \| `both_hit_conservative_loss` \| `horizon_end`. |
| `scoring.forward_derived_cache` | object | no | `{min, max}` caches; validator recomputes (E6). |
| `scoring.quadrant_target` | enum | no | `TP` \| `FP` \| `TN` \| `FN`. Rows tagged `WAIT_TRACKED_SEPARATELY`/`STEWARDSHIP` omit it (frozen exclusion rule). |
| `scoring.regime_tags` | array[string] | no | Frozen regime vocabulary (`vix_panic`, `sector_bleed`, …) for S3/S4 bucketing. |
| `expected_verdict` | enum | no | `GO` \| `GO_WEAK` \| `WAIT` \| `AVOID` (pre-entry), or the post-entry lifecycle state for stewardship rows. |

### 4.2 Worked Examples (identical shape, different origin)

```json
{
  "scenario_id": "SCEN-032",
  "schema_version": "uvest-scenario-manifest/1",
  "origin": "scan_generated",
  "created_on": "2026-10-10",
  "authoring": {
    "scan_rule": "breakout_20d_high_delivery_surge",
    "anchor_date": "2021-04-15",
    "universe_version": "fno_liquid_v1"
  },
  "classification_tags": [],
  "gate_focus": ["S1", "S7"],
  "symbol": "TATASTEEL", "exchange": "NSE", "instrument": "FUT",
  "expiry": "2021-04-29", "action": "BUY",
  "recommendation_date": "2021-04-15",
  "entry_price": 1042.0, "entry_range_min": 1041.0, "entry_range_max": 1043.0,
  "target_price": 1120.0, "stop_loss": 1005.0, "lot_size": 550,
  "user_capital_inr": 500000.0,
  "replay_inputs": {
    "canonical_trade": "fixtures/SCEN-032/current_trade.json",
    "truncated_fixtures": ["fixtures/SCEN-032/ohlcv_prefix.json"],
    "truncation": {"cutoff": "2021-04-15", "rule": "date <= T_rec", "min_bars": 200}
  },
  "recorded_artifacts": [
    {"stage": 1, "artifact": "out_1_math.json", "path": "fixtures/SCEN-032/recorded/out_1_math.json", "evidence": "recorded"},
    {"stage": 2, "artifact": "out_2_market.json", "path": "fixtures/SCEN-032/recorded/out_2_market.json", "evidence": "recorded"},
    {"stage": 6, "artifact": "out_6_fundamental.json", "path": null, "evidence": "waived", "reason": "stage 6 unimplemented"}
  ],
  "scoring": {
    "forward_tape": "fixtures/SCEN-032/forward_tape.json",
    "bars": 22,
    "resolution_date": "2021-05-17",
    "resolution_outcome": "WIN",
    "resolution_basis": "target_first",
    "forward_derived_cache": {"min": 1012.0, "max": 1131.5},
    "quadrant_target": "TP",
    "regime_tags": ["commodity_supercycle"]
  },
  "expected_verdict": "GO"
}
```

```json
{
  "scenario_id": "GB-2023-0012",
  "schema_version": "uvest-scenario-manifest/1",
  "origin": "curated_behavioral",
  "created_on": "2026-10-10",
  "authoring": {"shape": {"risk_atr": 1.2, "rr": 0.9}, "source": "curate"},
  "classification_tags": [],
  "gate_focus": ["promoter_pledging_gt_25pct"],
  "symbol": "SBIN", "exchange": "NSE", "instrument": "FUT", "action": "BUY",
  "recommendation_date": "2023-03-15",
  "entry_price": 552.0, "entry_range_min": 551.0, "entry_range_max": 553.0,
  "target_price": 530.0, "stop_loss": 565.0, "lot_size": 750,
  "user_capital_inr": 500000.0,
  "replay_inputs": {
    "canonical_trade": "fixtures/GB-2023-0012/current_trade.json",
    "truncated_fixtures": ["fixtures/GB-2023-0012/ohlcv_prefix.json"],
    "truncation": {"cutoff": "2023-03-15", "rule": "date <= T_rec", "min_bars": 200}
  },
  "recorded_artifacts": [
    {"stage": 1, "artifact": "out_1_math.json", "path": "fixtures/GB-2023-0012/recorded/out_1_math.json", "evidence": "recorded"},
    {"stage": 2, "artifact": "out_2_market.json", "path": "fixtures/GB-2023-0012/recorded/out_2_market.json", "evidence": "recorded"},
    {"stage": 6, "artifact": "out_6_fundamental.json", "path": null, "evidence": "quarantined", "reason": "legacy hand-typed pledge value; rebase on stage 6"}
  ],
  "scoring": {
    "forward_tape": "fixtures/GB-2023-0012/forward_tape.json",
    "bars": 22,
    "resolution_date": "2023-04-17",
    "resolution_outcome": "LOSS",
    "resolution_basis": "both_hit_conservative_loss",
    "quadrant_target": null,
    "regime_tags": ["sector_bleed"]
  },
  "expected_verdict": "AVOID"
}
```

---

## 5. Evidence Status & Gate Interaction

| Status | On disk | Graded? | Effect on scorecard |
| :--- | :--- | :--- | :--- |
| `recorded` | Byte-exact artifact | Yes | Normal gate contribution. |
| `waived` | Absent | No | `[SKIP]` for that gate; `max_possible_score` and `confidence_deficit` adjust per the frozen waiver rule. |
| `quarantined` | Present, hash-pinned | **Never** | `[SKIP]` with reason; counted in the quarantine register; register digest frozen and non-increasing. |

Fast profile (`tests/run_evals.py`): grades only rows whose `gate_focus` evidence is fully `recorded`. Full profile (`tests/run_business_proof.py`): replays all rows via `replay_inputs`; aggregates gates; skips quarantined contributions identically. Neither profile may read a `quarantined` artifact as evidence.

---

## 6. Validator Checks (machine-checkable)

Implemented by `tests/evals/validate_manifest.py`; Layer-1 seam `tests/test_validate_manifest.py` (every check below has a synthetic violation it must catch).

| # | Check |
| :--- | :--- |
| V1 | `scenario_id` unique, non-empty, matches a frozen ID family. |
| V2 | All enums within the frozen vocabularies; `instrument ∈ {CASH, FUT}` (options rejected, ADR-001). |
| V3 | Every path is proof-root-relative — absolute paths, drive letters, and `..` are errors — and exists. |
| V4 | Every referenced evidence file has exactly one entry in `manifest.sha256`; every entry's file exists and matches its hash. |
| V5 | Every file under the proof root is listed in the manifest or on the allowlist (E7); no unlisted evidence. `_replay/` sandboxes are the only excepted prefix. |
| V6 | No path in `replay_inputs`/`recorded_artifacts` appears under `scoring` paths, and vice versa (blindness). |
| V7 | All bars in every `content`-materialized fixture satisfy `date <= T_rec`; prefix count ≥ `min_bars`; no post-T_rec bar anywhere in replay inputs. |
| V8 | Forward tape bars are strictly `> T_rec`; bar count equals the frozen horizon unless a declared reason exists. |
| V9 | Derived caches recompute-equal from raw tape within 1e-6 (E6). |
| V10 | For offline-runnable stages, a fresh re-run byte-matches the recorded artifact (E3), newline-normalized to match the recorder's text-mode contract. The replay runner is supplied to the validator as a callback; when none is supplied the check reports a warning — it never passes silently. |
| V11 | The quarantine register exactly matches the manifest's quarantined artifacts; no graded breaker reads a quarantined artifact; a breaker whose contract-stage artifact is undeclared is an error. The breaker→stage map is frozen in the validator. |
| V12 | Baseline digests (`manifest_sha256`, `scenario_manifest_sha256`) match the current files unless a deliberate, version-bumped re-freeze is declared. |
| V13 | Stewardship/WAIT rows omit `quadrant_target` (frozen exclusion rule). |
| V14 | `replay_inputs.truncation.cutoff == recommendation_date` for every row. |

Failures of V1–V11, V13–V14 are hard errors (`ERR_FIXTURE_INTEGRITY` family); V12 is a regression verdict, not an integrity error.

---

## 7. Scorecard Fields This Schema Feeds

- `dataset_digest`: `{manifest_sha256, scenario_manifest_sha256}` — printed on every scorecard run.
- `rows_total`, `rows_by_origin`, `rows_by_evidence_status`, `quarantined_count` (non-increasing meter).
- Per-gate `[PASS]/[SKIP]/[FAIL]` with the same honesty semantics as today, plus the confusion matrix and ΔE vs baseline from ADR-007.

---

## 8. Migration Mapping (current golden dataset → manifest)

| Current field | Becomes |
| :--- | :--- |
| `trade_id` | `scenario_id` (unchanged value). |
| `trade_class` | `classification_tags` + `gate_focus` (breaker id mapping). |
| instrument/trade geometry fields | Same-named row fields under this schema. |
| `script_outputs.<artifact>` (extracted subset) | `recorded_artifacts` entries pointing at the **artifact file itself**; the extraction step disappears. |
| `forward_prices_22d` | `scoring.forward_tape` + `forward_derived_cache`. |
| `resolution_outcome`, `resolution_date` | `scoring.*` with `resolution_basis` recorded explicitly (today the basis is implicit in the curator). |
| `reported_numbers_match_json`, `html_report_valid` | Retired as dataset fields: the hallucination check becomes a runner-side report-vs-JSON differ; HTML validity moves to the stage-12 unit seam (ADR-007). |
| `meta.class_counts_expected` (50-trade quotas) | Retired in favor of the frozen 60-scenario quotas, frozen in the baseline. |

**Curation change required:** today's curator records stages 1–2 into a temp directory and then extracts a numeric subset into the dataset, discarding the bundle. It must instead record into the scenario's evidence location and emit the row **from the recorded artifacts** (paths + hashes), so no extracted payload can ever drift from the bytes that produced it. The `GB-2025-0005` rebase is the template for retiring quarantined rows: replace the hand-typed payload with a recorded bundle, then move the row out of quarantine.

**Sandbox placement:** materialized replay sandboxes must never be committable (the repo's existing negation `!tests/**/*.json` makes any JSON under `tests/` trackable). Put runtime sandboxes on a dedicated, gitignored path (e.g. `tests/proof/_replay/`) and add the ignore rule in the same change.

---

## 9. Non-Goals

- Gate thresholds and S1–S7 arithmetic (frozen in `business_test_cases.md`; owned by ADR-007's engine).
- Runner CLI details beyond the dataset interface and the two frozen entry points.
- Curation sourcing (scan rules, anchors, universe) — the scan parameters are recorded in `authoring`, but the scan implementation is owned by its own ticket.
- The 60-scenario quotas and regime coverage (frozen in `business_test_cases.md` §3).

---

## 10. Open Questions for the Owner

1. Confirm runtime materialization of `scenarios/{id}/content|outcome` (committed evidence + generated sandbox) as the faithful reading of the frozen tree, versus committing the materialized directories at the cost of repo size.
2. Confirm legacy `GB-` IDs are retained (no renames) with `SCEN-` used only for new rows.
3. Confirm the quarantine register starts at the current hand-typed count (1 pledge-hazard record's stage-6 payload) and may only decrease, with stage 6's ticket owning the rebase.
4. Decide whether `expected_verdict` stays a first-class field or expectations are expressed only through `gate_focus` + `quadrant_target` plus the published expectation-vs-actual report (the anti-overfitting option).
