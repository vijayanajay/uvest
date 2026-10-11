# Scenario Manifest Schema — The Canonical Dataset for the Merged Eval Suite
*Companion spec to [adr-007-eval-harness-consolidation.md](adr-007-eval-harness-consolidation.md). **Status: PROPOSED** — freezes only on owner approval of ADR-007; no runner code may depend on it before then.*

---

## 1. Purpose & Position in the Architecture

One canonical dataset feeds both execution profiles of the merged harness:

| Artifact | Role |
| :--- | :--- |
| `tests/proof/scenario_manifest.json` | The canonical index: one row per scenario, both origins, same schema. |
| `tests/proof/manifest.sha256` | The single source of integrity: `sha256sum`-format hashes of every committed evidence file the manifest references. |
| `tests/proof/baseline_scorecard.json` | Frozen regression reference: gate results, confusion matrix, expectancy, the two dataset digests (below), and the quarantine meter (`quarantined_count`, `quarantine_register_sha256`). |
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

**E5 — Quarantine for legacy hand-typed payloads.** Rows migrated from the current golden dataset whose payloads were hand-typed (not recorded) are tagged `evidence: quarantined` and listed in the quarantine register. Quarantined artifacts may exist on disk for migration continuity, but: (a) no graded gate may read one (the validator fails if it does); (b) the baseline freezes the register's row count and sha256 — growth is a hard error, an equal-count digest change is a hard error (silent swap), and a decrease is reported as a shrink so the baseline is deliberately re-frozen; (c) the row's gate contribution is `[SKIP]` with the quarantine reason printed. The quarantine count in the scorecard is the migration's completion meter.

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
| `scoring.resolution_date`, `scoring.resolution_outcome` | date, enum | yes | `WIN` \| `LOSS` \| `TIME`, resolved by the frozen order-aware scan (`dataset_refs.resolution_of`: first touch wins; only a bar carrying both levels resolves to the worse `LOSS`). |
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

Fast profile (`tests/run_evals.py`): grades only rows whose `gate_focus` evidence is fully `recorded`; it prints every resolved reference and excludes quarantined ones from grading (a breaker that reads a quarantined artifact abstains rather than counting as clean). Full profile (`tests/run_business_proof.py`): replays all rows via `replay_inputs`; aggregates gates; skips quarantined contributions identically. Neither profile may read a `quarantined` artifact as evidence.

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
| V10 | For offline-runnable stages, a fresh re-run byte-matches the recorded artifact (E3), newline-normalized to match the recorder's text-mode contract. The replay runner is supplied to the validator as a callback; when none is supplied the check reports a warning — it never passes silently. `tests/test_proof_manifest.py` supplies one (stages 1–2 re-run against each row's fixtures), so the invariant suite — and therefore pre-commit — byte-verifies every recorded artifact. Note the boundary: an artifact edited *and* re-hashed is invisible to the bare CLI (warning only) and to V4; only a runner-supplied V10 catches it, which is why the seam, not the CLI, owns this check. |
| V11 | The quarantine register exactly matches the manifest's quarantined artifacts; no graded breaker reads a quarantined artifact; a breaker whose contract-stage artifact is undeclared is an error. The register's frozen count may only decrease (`V11_QUARANTINE_REGRESSION` on growth, `V11_REGISTER_DIGEST_MISMATCH` on an equal-count swap, `V11_QUARANTINE_SHRUNK` warning on a decrease), and a non-empty register requires the baseline meter. The breaker→stage map is frozen in the validator. |
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
| `script_outputs.<artifact>` (extracted subset) | `recorded_artifacts` entries pointing at the **artifact file itself**; the extraction step disappears. *(Bridging step implemented: a row whose artifacts the proof root declares carries `{"$ref": "<repo-relative path>"}` instead of an extracted subset — `GB-2023-0003` does this for stages 1, 2 and 6. References are verified against the row's declared path and the frozen hash in `tests/test_proof_manifest.py`, and a declared artifact may never also be inlined.)* |
| `forward_prices_22d` | A `$derive` reference to the row's pinned tape (resolved at load, cross-checked against the row's V9-verified `forward_derived_cache`). |
| `resolution_outcome`, `resolution_date` | A `$derive` reference to the same tape (`resolution_basis` stays in `scoring.*`; the dataset's date is the frozen horizon end). |
| `reported_numbers_match_json` | **Retired (2026-10-11):** the hallucination check is the runner-side report-vs-JSON differ — `tests/evals/report_diff.py` grading `reports/report_{SYMBOL}_{DATE}.html` against the record's own JSON inside `gate_hallucination` (ADR-007: the gate stays graded, the curator's boolean is gone). |
| `html_report_valid` | Stays a null placeholder until the stage-12 unit seam owns HTML validity: `null` means "no report generated yet", never a pass. |
| `meta.class_counts_expected` (50-trade quotas) | Retired in favor of the frozen 60-scenario quotas, frozen in the baseline. |

**Curation change required:** today's curator records stages 1–2 into a temp directory and then extracts a numeric subset into the dataset, discarding the bundle. It must instead record into the scenario's evidence location and emit the row **from the recorded artifacts** (paths + hashes), so no extracted payload can ever drift from the bytes that produced it. The `GB-2025-0005` rebase is the template for retiring quarantined rows: replace the hand-typed payload with a recorded bundle, then move the row out of quarantine. The rule now extends to outcome claims: a record whose row pins a forward tape resolves `forward_prices_22d`, the resolution pair, direction and MAE through `$derive` (frozen rules in `tests/evals/dataset_refs.py`) — never inline numbers — and claims whose engines do not exist yet (cone envelope, limit-entry MAE, predicted direction) stay absent so their gates `[SKIP]` instead of grading a hand-typed claim.

**Migrated (2026-10-11): every golden record is pinned.** `GB-2023-0003` was the template (quarantine migration); `GB-2022-0001`, `GB-2022-0002`, `GB-2024-0004` and `GB-2025-0005` followed; the 26 campaign records (`GB-2022-0006` … `GB-2025-0032`) were migrated by `scenario-manifest-schema.md`'s §8.2 tool with `--records unpinned`. No record in the dataset carries a claim its committed evidence cannot support. Accounting of what the tape replaced (every entry frozen in its row's `authoring.superseded`): the four legacy rows superseded 13 claim values, their engine-less claims were retired, and their pre-migration values were recovered from `git HEAD:tests/evals/golden_dataset.json` with that provenance recorded on the entry; of the campaign rows all 76 forward-range/horizon values proved **identical** to the tape (the curator's copies were honest) and their 26 copied payload subsets were reproduced exactly by the fresh recordings, while two resolutions were superseded — `GB-2022-0006` and `GB-2022-0013` are `LOSS → WIN` under the frozen bar-scan, because the target was touched before the stop even though both levels sat inside the window (the curator's *seed scan* **was** window-level when they were seeded — any stop touch inside the window — and called that LOSS; at the reconciliation every scan was folded into the one order-aware rule, which remains the single source of the resolution). Both are authored as the honest `FN` of `business_test_cases.md` §4.1. Exactly one quarantined artifact remains (0003's stage-6 payload) and the meter is unchanged — see §10.3.

### 8.2 Dataset migration pipeline (`tests/evals/pin_legacy_records.py`)

One tool migrates the pre-proof golden records onto proof-root rows
(`python tests/evals/pin_legacy_records.py [--apply|--force]`), so the record
transformation is reproducible and reviewable rather than hand-edited:1. **Stage 1–2 payloads become recorded evidence.** The record's canonical trade is rebuilt from its own published levels and re-run through `--record` into `fixtures/<id>/recorded/`; the dataset holds `$ref`s, and V10 byte-compares each artifact against a fresh offline run. A re-record must also *reproduce the values the record claimed* (the copied `raw_rr`/`live_rr`/`atr_14`/`sl_atr_ratio`/`is_tight_sl` subsets): all 26 campaign payloads did, byte for byte at the curator's rounding; any drift would be reported and frozen as a supersession rather than silently accepted.
2. **Geometry is preserved when the tape corroborates it, and re-derived at the
   record's frozen class shape when it does not.** Corroboration is structural
   and lookahead-free (only the `T_rec` close from the ≤`T_rec` tape): a long
   advisory is actionable iff `stop_loss < T_rec close < target_price`. A
   published stop already above the market means the levels belong to a price
   scale the point-in-time tape cannot corroborate, so the geometry is re-derived
   from ≤`T_rec` tape at the class shape the curator itself uses
   (`2.0×ATR` & `2.2R`, `1.2×ATR` & `0.9R`, `0.6×ATR`) — chosen per class, never
   per record — and the superseded levels are recorded in `authoring.note`. The
   tape decides the outcome; a migration that flip-flops a class claim is
   disclosed as the honest quadrant, never tuned (0001 and 0002 both resolved
   against their legacy hand-typed outcome, 0002 as an `FN`).
3. **Engine-less claims are retired to absence, never quarantined.** A legacy
   stage-6 payload is dropped and declared `evidence: waived` with a reason (E4);
   the ex-ante `predicted_direction` / cone / limit-entry MAE fields are set
   absent. Quarantining those bytes would *grow* the E5 meter to park debt that
   is no longer evidence, so the tool asserts the register is untouched —
   and `tests/test_proof_manifest.py` fails any proof-row record that carries a
   cone/direction/limit-MAE number without a recorded artifact for the stage
   that would produce it.
4. **Contract specifics are not asserted.** Rows migrate as `CASH` with
   `lot_size 1` (the `GB-2023-0003` precedent): the legacy FUT expiry/lot are not
   verifiable from committed point-in-time sources, so V2's expiry rule is not
   satisfied with an invented contract.
5. **Successor tapes are disclosed.** A delisted issuer has no live ticker; the
   tape then comes from the successor entity's series and the override is
   recorded in `authoring.tape_source` with the corporate-action caveat
   (`GB-2022-0002`: Tata Motors demerged, `TATAMOTORS.NS` delisted, tape from
   `TMPV.NS`) — the same pattern as SCEN-011's rename.Re-running the tool is idempotent by design: a migrated record's geometry decision
cannot be re-derived from its own migrated levels, so already-migrated records are
skipped unless `--force` re-records them. `--records unpinned` selects every
dataset record with no proof row, so a later curation batch is one command.

**Verification gates before writing:** the prefix must satisfy the ≥200-session
floor with 22 forward sessions, the derivation rules in `dataset_refs.py` must
reproduce the tool's own scan (outcome, basis, cache, horizon end), and the
*recorded* artifacts must satisfy the record's frozen class invariant
(`live_rr > 1.0`/`is_tight_sl false` for clean, `raw_rr`/`live_rr < 1.0` for
inverted, `sl_atr_ratio < 0.75` for tight-stop). A record the tape cannot support
is BLOCKED and left unauthored — the tape is never negotiated.

### 8.1 Catalog authoring pipeline (`tests/evals/scaffold_catalog.py`)

Frozen-catalog rows are authored by one tool, so every row is produced the same way and no field is hand-typed:

1. **Expectations come from the immutable catalog** (`business_test_cases.md` §3): anchor date, symbol, instrument, target verdict, realized outcome and quadrant are parsed, never re-entered. A seam (`tests/test_proof_manifest.py`) re-checks the published rows against that table, so a later hand-edit cannot drift from the frozen source.
2. **Tape is real and point-in-time:** daily bars are fetched once per symbol-month through the curator's cached path, truncated at `T_rec` (≥200 sessions), with the next 22 sessions kept as the forward tape.
3. **Geometry is authored under a frozen policy, per catalog class — never per scenario.** An `AVOID`-class row uses the T_rec session **open** (the advisory is issued intraday, before the collapse) with the tight stop the catalog itself documents for advisories (`0.32×`–`0.55×` ATR); a `GO`-class row uses the close with a `2×` ATR noise buffer and a `2.2R` target. The chosen policy and its rationale are recorded in `authoring`.
4. **Stages 1–2 are re-run through `--record`,** so each graded artifact is real stage stdout; V10 byte-compares them against a fresh offline run.
5. **A row whose tape-derived outcome disagrees with the catalog is BLOCKED, not tuned.** The slot stays unauthored and the evidence is published for the owner; the only resolution is an explicit owner-commanded catalog re-freeze — as SCEN-001/005 (rev. R1) — never a geometry chosen to fit the claim. A re-frozen `AVOID` row the tape then wins is authored as the honest `FN` of `business_test_cases.md` §4.1.

Disclosed authoring approximations: `expiry` follows the real NSE monthly last-Thursday convention (rolled when the anchor is within 7 days); `lot_size` cannot be sourced offline, so it is derived from a frozen ₹5L notional band and the derivation is recorded per row. Breakers whose engines do not exist yet (stage 6 pledge/earnings, stage 7 panic) are recorded as `authoring.breaker_pending_stage_*` rather than claimed as gate evidence — a `gate_focus` breaker id whose stage artifact is undeclared is a V11 error by design.

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
3. **RESOLVED (2026-10-10): the quarantine register is at its floor and may only decrease.** The five pre-proof records are pinned; the four that carried hand-typed stage-6 payloads (`GB-2022-0001/0002`, `GB-2024-0004`) had them retired to absence and declared `evidence: waived`, *not* quarantined — parking retired bytes in the register would have grown the E5 completion meter, and a mutation proof (`V11_QUARANTINE_REGRESSION: quarantine register grew: 2 > frozen 1`) confirms growth is a hard error. The single remaining row is `GB-2023-0003`'s stage-6 payload, still tracked debt and excluded from grading; stage 6's ticket owns its rebase. The same migration re-froze the baseline digests (V12) and reduced the dataset's mobile-claim surface: five records now resolve every forward claim through `$derive` from a hash-frozen tape.
4. Decide whether `expected_verdict` stays a first-class field or expectations are expressed only through `gate_focus` + `quadrant_target` plus the published expectation-vs-actual report (the anti-overfitting option).
5. **RESOLVED (owner-commanded, 2026-10-10): SCEN-001 and SCEN-005 re-frozen to `AVOID`/`WIN`/`FN`.** Their recorded point-in-time tape, under the frozen resolution rule (outcome bars strictly after `T_rec`), reaches the `2R` target without ever touching even the tightest documented advisory stop (`0.32×ATR`): RELIANCE 2020-03-23 bottomed that day (forward `[409.53, 683.44]`, target `446.50` at the tight stop) and TATASTEEL 2022-02-24 gapped down but never traded back to the tight stop (forward `[109.56, 136.60]`). Both advisories rejected realized winners — the honest false-negative accounting of `business_test_cases.md` §4.1. The catalog carries the re-freeze as rev. R1 (§3 footnote); both rows are authored as `FN` with the tape evidence, and no geometry was tuned to manufacture a `LOSS`.
6. **RESOLVED (owner-commanded, 2026-10-10): SCEN-011 authored on the renamed issuer's tape.** `IBULHSGFIN.NS` no longer resolves (Indiabulls Housing Finance → Sammaan Capital, 2024); the surviving ticker `SAMMAANCAP.NS` serves the full point-in-time history (246 cached sessions back to 2018-11), and the crisis policy resolved `LOSS`/`TN` as cataloged until item 7 re-froze the row. The row keeps `symbol: IBULHSGFIN` as the point-in-time identity and records `authoring.tape_source` (`requested`/`fetched`/`reason`).

7. **RESOLVED (reconciliation, 2026-10-11): one resolution rule — SCEN-011 re-frozen to `AVOID`/`WIN`/`FN`, SCEN-009's basis corrected to `stop_first`.** The curator's seed scan was window-level (any stop touch inside the window ⇒ `LOSS`) while the frozen `$derive` rule is order-aware (first touch wins); they disagreed exactly when both levels sat inside the window and the target printed first. Every implementation now delegates to the single `dataset_refs.resolution_of` — `curate.outcome`, `scaffold_catalog.resolve`, `pin_legacy_records.outcome_of` — and the seam `tests/test_proof_manifest.py::test_stored_scoring_matches_the_frozen_rule` recomputes each row's stored resolution from its own pinned tape: 46/46 agree. SCEN-011's tape prints target `259.14` on 2019-10-03, the first bar after `T_rec`, and stop `208.71` only on 2019-10-07, so the honest row is the `FN` of `business_test_cases.md` §4.1; SCEN-009 stopped first (2019-01-28), so its outcome never moved — only the stale `both_hit_conservative_loss` label did. The catalog carries rev. R2 (§3 footnote), which supersedes the `LOSS`/`TN` reading quoted in item 6.
