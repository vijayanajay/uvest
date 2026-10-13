# EVAL-002: Hazard Boundary-Probe Rows (Pinned Evidence at the Breaker Thresholds)

**Status:** PROPOSED (pending owner approval of `docs/agents/adr-009-hazard-boundary-probes.md`; flips to FROZEN (sliced) in the same change that pastes its `BRD.md` §9.1 and `docs/tickets/INDEX.md` rows)
**Priority:** P1
**Test Seam:** `tests/test_seed_hazard_records.py`
**Dataset:** `tests/evals/golden_dataset.json`
**Catalog:** `business_test_cases.md` §3

### 1. Goal & Context

Gate 2's rule-exercise census counts distinct tripped `(arm, value)` pairs, so the two stage-6 hazard classes are capped by the distinct hazard values their rows carry, not by row counts: `pledge_hazard` 5 and `earnings_hazard` 2, against quota 5 each. Unlike the geometry classes' shapes, a pledge level and a session window are authored from the catalog and never re-derived from tape — catalog growth is the only honest lever, under ADR-008's sourcing rules. This ticket adds three **boundary probes**: rows whose hazard value sits at the breaker's decision boundary, so the census tests whether each breaker fires *at* its threshold and not only comfortably past it.

### 2. Frozen Specification References

- `docs/agents/adr-009-hazard-boundary-probes.md` (the decision memo this ticket implements)
- ADR-008 and ADR-009 in `docs/agents/domain.md` (level sourcing, window pinning, boundary scope)
- `business_test_cases.md` §3 (row identities, read by the scaffolder's parser — E8) and §5 (roll-up denominators)
- `docs/agents/evals.md` §2 (the benchmark dataset and its quota discipline)
- `docs/agents/scenario-manifest-schema.md` §8.2 (hazard-class seeding: the evidence contract)

### 3. The Three Rows

| ID | Class | Probe | Pinned evidence | Outcome |
| :--- | :--- | :--- | :--- | :--- |
| `SCEN-061` | `pledge_hazard` | marginal pledge just above the 25% kill switch (band 25.1–28.0%) | NSE shareholding-pattern disclosure, level as of a date ≤ anchor | `LOSS`/`TN` |
| `SCEN-062` | `earnings_hazard` | results **1 trading session** after T_rec (knife-edge) | NSE results-date announcement; window = trading sessions T_rec → announced date | `LOSS`/`TN` |
| `SCEN-063` | `earnings_hazard` | results **6 trading sessions** after T_rec (far edge of the <7 band) | same, at the far edge | `LOSS`/`TN` |

Quota bumps in the same change: `meta.class_counts_expected` `pledge_hazard` 5→6, `earnings_hazard` 5→7; `total_trades_expected` 50→53; catalog §5 roll-ups gain the probes (pledge 6/6 → 7/7, earnings 5/5 → 7/7); census floor 38→41 (V12 re-freeze).

### 4. TDD Acceptance Criteria

- [ ] **Sourcing is real and cited.** Each row's `fixtures/<ID>/financials.json` `authoring.disclosure` names the pinned source (disclosure snapshot / results announcement) and its date; `hazard_value` equals it. A hand-edited value with no cited source fails the seam (`test_seed_hazard_records.py`'s bundle-equality check, extended to the three rows).
- [ ] **The graded bytes are a recording.** Each row's `out_6_fundamental.json` is a `$ref` to `fixtures/<ID>/recorded/out_6_fundamental.json`, a real `scripts/6_fundamental_audit.py --record` stdout over the row's own bundle; recorded `promoter_pledge_pct` / `days_to_earnings` round to the pinned value at the census granule (E1).
- [ ] **Breaker encoding.** `SCEN-061`'s recorded pledge is `> 25.0` and inside the marginal band; `SCEN-062` records `days_to_earnings == 1`, `SCEN-063` `== 6`; each carries its breaker flags (`pledge_circuit_breaker` / `earnings_hazard_flag`).
- [ ] **Statement blocks stay empty.** The bundles state no FY statement fields; stage 6 emits null ratio metrics with `missing_inputs` naming every gap (E4 — absence, never an invented filing).
- [ ] **The tape realizes the documented outcome.** Each row's pinned forward tape resolves `LOSS` under the frozen order-aware resolution (`dataset_refs.resolution_of`); a row the tape blocks stays unauthored and the shortfall is printed, never tuned (E8).
- [ ] **Quotas hold.** `meta.class_counts_expected` reads pledge 6 / earnings 7 / total 53, no class over quota (`check_class_distribution` green).
- [ ] **The census rises honestly.** `run_evals.dataset_rule_census()` reads `pledge_hazard` 6, `earnings_hazard` 4; the coverage line reports no new repeats beyond the disclosed ones; baseline floor re-frozen at 41.
- [ ] **Nothing else moves.** Geometry-class rows, the quarantine register (E5 meter 0), and the other four classes' bytes are untouched; `manifest.sha256` regenerated and the manifest validator passes with 0 errors.
- [ ] **Reports reproduce.** `reports/` cleared and reproduced from the proof root; hallucination and HTML-validity gates stay green (a warm ledger proves stale bytes).

### 5. Refusal Conditions (the tool must refuse, not work around)

1. A probe whose evidence cannot be sourced → the row is dropped and disclosed; the census does not get a fabricated cell.
2. A placeholder row (`‹sourcing›` fields) reaching the catalog or the dataset → the scaffolder refuses an unreadable anchor/symbol; this ticket's seam fails any row whose `documented` citation is empty.
3. Quota bumped without the rows, or rows without the quota → `check_class_distribution` fails the run; both land in one change.
4. An authored default window or an authored pledge level on any new row → violates ADR-008; the seam compares each bundle against its cited source.

### 6. Definition of Done

Sourcing proof printed for all three rows (or the refusal disclosed for any that fails); rows authored and recorded; quotas bumped; `python tests/run_evals.py` exits 0 with the census line at 41 and `Rule coverage: OK (41 …; frozen floor 41)`; full suite green warm and pristine; validator PASSED; ADR-009's paste-ready `BRD.md` §9.1 harness row, `INDEX.md` row, and `domain.md` ADR block applied in the same change; catalog §3/§5/§6 amendments committed under the owner's command.
