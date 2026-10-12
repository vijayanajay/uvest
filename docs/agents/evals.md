# Evals Specification & Benchmark Suite
*Configured via /setup-matt-pocock-skills for UVEST*

## 1. Core Purpose of Evals in UVEST
In AI-assisted quantitative development, **Evals (Evaluations)** are the behavioral benchmarks that prevent regressions, hallucinations, and heuristic drift. 

Before any production Python script is implemented, the evaluation criteria and golden test cases are frozen. The system must achieve a 100% pass rate on hard safety gates and meet defined statistical thresholds on empirical performance.

---

## 2. The 50-Trade Golden Benchmark Dataset (`tests/evals/golden_dataset.json`)

The Evals suite evaluates the entire pipeline against a curated benchmark of 50 historical Univest recommendations across Indian equities and futures spanning 2022–2026:
- **15 Clean Bullish Setups:** High delivery %, positive F&O buildup, clean fundamentals, R:R $\ge 1.8$, resulting in forward targets being achieved.
- **10 Inverted Risk-Reward / Slippage Traps:** Reached user after price had moved $>1.5\%$ above entry; live R:R $< 1.0$.
- **10 Institutional Distribution Traps:** Price rising on falling Futures OI and low Delivery % ($<25\%$), resulting in subsequent sharp sell-offs.
- **5 High Promoter Pledging Traps:** Companies with promoter pledge $> 25\%$ (e.g., historical Zee / Adani stress / Vedanta pledge events).
- **5 Impending Earnings Event Traps:** Recommended 2–4 days before quarterly board meeting; resulted in binary earnings gap-down.
- **5 High Volatility / Tight Stop Traps:** Stop Loss placed $< 0.75 \times \text{Daily ATR}$, resulting in intraday noise stopouts.

**Shape families.** The three geometry classes' *shapes* are curation parameters, not catalog facts, and each class declares a **family** of them: its frozen curator shape (`2.0×ATR` & `2.2R`, `1.2×ATR` & `0.9R`, `0.6×ATR`) plus rungs spanning the documented parameter space (`clean_bullish` 1.0–3.0×ATR across 1.8–3.0R, `inverted_rr` 0.9–2.0×ATR across 0.35–0.9R, `tight_stop` 0.35–0.70×ATR). `tests/evals/reshape_records.py` places a class's rows on the rungs their own pinned tape admits, and only there: a rung is authored for a row only if (a) it realizes the class's documented realized outcome on that row's tape — so no row is moved to manufacture an `FP`/`FN` — and (b) it adds at least one rule exercise the class does not already stand for. Everything the rule refuses is printed and frozen: the rungs the tape rejected (`authoring.reshape.refused_rungs`), the shape each moved row came from and the outcome it changed (`authoring.reshape`), and the rows left standing at the frozen shape. Rows whose geometry is their own published claim — `GB-2024-0004` at `2.17R`, `GB-2025-0005` with its legacy target — are not the campaign's to re-author and are kept as published. The two hazard classes have no family: their measurements come from the immutable catalog (pledge levels, the 2–4 session windows), so there is no shape to author. The family is what lets the rule-exercise census measure a class's *parameter coverage* instead of the replication count of one shape.

**Shape-ladder campaign (2026-10-13).** The three families were applied in one deliberate re-freeze: 13 rows re-derived at declared rungs and re-recorded from their own pinned ≤T_rec tape (7 `clean_bullish`, 3 `inverted_rr`, 4 `tight_stop`; 2 more rows were already standing on a rung, and 13 were left standing because no admissible rung added a rule exercise the class did not already have). No class claim moved — every placed row still resolves as its class documents — and no rung was re-rolled for an outcome. The rule-coverage floor went from **11 to 38** distinct exercises (`clean_bullish` 6→18, `inverted_rr` 2→8, `tight_stop` 1→5; `pledge_hazard` 5 and `earnings_hazard` 2 are catalog-fixed). The campaign is disclosed per row in `authoring.reshape` and summarised in the dataset's `meta.notes`.

**Payload references (`$ref`).** A `script_outputs` value may be either an inline field subset or a reference object `{"$ref": "<repo-relative path>"}` naming an artifact under `tests/proof/`. A referenced payload has exactly one copy of its bytes there, where `manifest.sha256` freezes them and `quarantine_register.json` records their provenance, so the dataset and the proof root cannot drift (`tests/test_proof_manifest.py` enforces the coupling). A reference never also carries payload keys. Quarantined provenance is disclosed on the scorecard and **excluded from grading** — legacy hand-typed bytes are tracked debt, never evidence.

**Outcome derivations (`$derive`).** A record whose proof row pins a forward tape resolves `forward_prices_22d`, the resolution pair, `market_mae_pct` and `actual_direction` through `{"$derive": {"tape": "…", "rule": "…"}}` instead of carrying numbers, so what the tape did cannot be typed. Claims whose engines do not exist yet (cone envelope, limit-entry MAE, predicted direction/probability) stay absent — absence, never a hand-typed stand-in (`tests/test_proof_manifest.py` fails such a claim unless the row declares a recorded artifact for the stage that would produce it).

**Legacy migration.** Every golden record is pinned onto a proof-root row. The five pre-proof records (`GB-2023-0003` quarantine migration; `GB-2022-0001/0002`, `GB-2024-0004`, `GB-2025-0005`) and the 26 campaign records (`GB-2022-0006` … `GB-2025-0032`, migrated with `--records unpinned`) via `tests/evals/pin_legacy_records.py` (specified in `scenario-manifest-schema.md`): stage 1–2 payloads are re-recorded evidence, outcome claims are `$derive` references, published geometry is kept when the point-in-time tape corroborates it and re-derived at the record's frozen class shape when it does not (disclosed in `authoring.note`), and retired legacy payloads are `waived`-absent rather than quarantined, so the E5 migration meter never grows. What the tape replaced is accounted for, not assumed: the campaign rows' 76 forward-range and horizon values all proved identical to the tape and their 26 copied payload subsets reproduced by the fresh recordings; superseded values are frozen per row in `authoring.superseded` and printed by the tool. Migrating changed what the tape says about six records — the four legacy rows plus `GB-2022-0006` and `GB-2022-0013`, whose outcomes flip `LOSS → WIN`.

**Resolution conventions.** One rule decides every outcome: `dataset_refs.resolution_of` (order-aware — the first of the 22 forward bars to touch a level decides, and only a bar carrying *both* levels resolves to the worse `LOSS`). The curator's seed scan (`curate.outcome`), the catalog scaffolder (`scaffold_catalog.resolve`), the migration tool (`pin_legacy_records.outcome_of`) and the `$derive` rule all delegate to it, and `tests/test_proof_manifest.py` recomputes every row's stored resolution from its own pinned tape (46/46 agree), so admission, authoring, stored scoring and derived claims cannot disagree. The pre-reconciliation **seed scan** was window-level — any stop touch inside the window ⇒ `LOSS` — which is why `GB-2022-0006` and `GB-2022-0013` are authored as the honest `FN` quadrant (AVOID expected, `WIN` realized) rather than retuned to the seeder's `LOSS`, and why `SCEN-011` was re-frozen `LOSS`/`TN` → `WIN`/`FN` (rev. R2 in `business_test_cases.md` §3; `SCEN-009`'s basis label moved to `stop_first` with its outcome unchanged).

**Derived outcome claims (`$derive`).** A field that describes what the pinned tape did — `forward_prices_22d`, `resolution_date`, `resolution_outcome`, `market_mae_pct`, `actual_direction` — may be written as `{"$derive": {"tape": "<repo-relative path to the proof root's forward tape>", "rule": "<frozen rule>"}}` instead of an inline number. `tests/evals/dataset_refs.py` recomputes the value from the tape's own bars at load (`forward_min_max`, `market_mae_pct`, `resolution_date`, `resolution_outcome`, `realized_direction`), the runner prints every derivation on the scorecard, and `tests/test_proof_manifest.py` asserts that a record whose proof row pins a forward tape never carries an inline outcome claim and that each derived value equals the row's own scoring block. Claims whose engines do not exist yet (cone envelope `[P10, P90]`, limit-entry MAE, predicted direction/probability) stay absent — the gate emits `[SKIP]` rather than grading a hand-typed claim.

---

## 3. Evaluation Dimensions & Pass Criteria

| Dimension | Evaluation Seam | Automated Metric / Assertion | Pass Threshold |
| :--- | :--- | :--- | :--- |
| **1. Zero Hallucination Guarantee** | Agent Master Prompt Output vs. Script Output JSONs | Every number, percentage, and ratio cited in the generated Markdown and HTML reports must have an exact match in the corresponding script output JSON. | **100% Exact Match** (0 hallucinations allowed) |
| **2. Hard Circuit Breaker Enforcement** | Scoring Engine (`scripts/9_decision_engine.py`) | Any trade in the benchmark with Promoter Pledge $>25\%$, Inverted R:R ($<1.0$), Options instrument, or Earnings $<7\text{d}$ on Futures must receive an **🔴 AVOID**. The scorecard also names per-class coverage and each class's distinct rule-exercise census — over the shape families its rows were authored on (§2), so a class with no graded record, or with records that all repeat one exercise, is disclosed rather than averaged away. | **100.0% Detection Rate** (Zero false negatives) |
| **3. Option Rejection Invariance** | Ingestion Parser (`scripts/0_ingest_reco.py`) | Any recommendation containing Call (CE), Put (PE), or Option contracts must be immediately rejected with exit code 2. | **100.0% Rejection Rate** |
| **4. Directional Calibration** | LightGBM & DTW Engine (`3_dtw_pattern.py`, `4_ml_direction.py`) | Calibrated Brier score on 1-month forward direction prediction across the benchmark. | **Brier Score $\le 0.18$** |
| **5. Range Coverage Integrity** | Volatility Cone Engine (`4_ml_direction.py`) | Percentage of actual forward 22-day maximum and minimum price bars that remained within the calculated $[P_{10}, P_{90}]$ price envelope. | **Coverage $\ge 80.0\%$** |
| **6. Optimal Limit Entry Value-Add** | Precision Engine (`8_dhan_precision.py`) | Limit orders placed at Anchored VWAP / Order Block support must show lower realized Max Adverse Excursion (MAE) compared to entering at raw Univest market price. | **MAE Reduction in $\ge 75\%$ of trades** |
| **7. HTML Report Validity** | HTML Visualizer (`12_generate_html_report.py`) | Generated HTML must be self-contained (zero broken images or missing styles), render under 300ms, and contain all required charts and gauges. | **100% Valid HTML5, 0 network dependencies** |

---

## 4. Evals Execution Runner (`tests/run_evals.py`)

The evaluation runner runs headlessly and outputs an evaluation scorecard:

```bash
python tests/run_evals.py --benchmark tests/evals/golden_dataset.json
```

It grades nothing it cannot prove: see §4.1 for the run-record provenance every artifact-graded gate is tied to.

The path a *fresh clone* has to survive — no ledger, no generated report, everything reproduced from the proof root — is proved on every push by `tests/evals/cold_start.py`.

A fresh clone also has to pass the **invariant suite itself**: `tests/evals/pristine_suite.py` runs `tests/run_all_tests.py` on a checkout holding no generated artifact, then requires that run to have produced the stage-12 reports *and* the ledger its own artifact gates grade — a suite that went green while producing nothing is not that proof (it refuses a warm tree too).

CI (`.github/workflows/uvest.yml`) chains the two, and the order is load-bearing, because `reports/` is gitignored state that starts empty and only ever warms: **pristine suite proof → `git clean -Xdf reports` → cold provenance proof**. The clean step — `-X`, so the tracked `reports/sample_report.html` survives — hands the pristine state the first proof requires to the second. Both proofs refuse a tree that is already warm, so a mis-ordered workflow fails loudly instead of proving nothing; `tests/test_pristine_suite.py` freezes that order.

Output scorecard template:
```text
======================================================================
              UVEST AGENT & QUANT EVALUATION SCORECARD
======================================================================
  [PASS] Option Rejection Guardrail:       100.0% (5/5 rejected)
  [PASS] Hard Circuit Breaker Accuracy:   100.0% (25/25 correctly blocked)
         ↳ class coverage: clean_bullish 15/15 graded, 18 distinct rule exercise(s)
           (7 of 15 repeat an exercise); inverted_rr 10/10 graded, 8 distinct rule
           exercise(s) (6 of 10 repeat an exercise); institutional_distribution no
           registered breaker (0 record(s), quota 10); pledge_hazard 5/5 graded,
           5 distinct rule exercise(s); earnings_hazard 5/5 graded, 2 distinct rule
           exercise(s) (3 of 5 repeat an exercise); tight_stop 5/5 graded,
           5 distinct rule exercise(s)
  [PASS] Zero Hallucination Audit:        100.0% (50 graded, 0 with discrepancies)
  [PASS] Directional Brier Score:          0.142  (Threshold <= 0.180)
  [PASS] Price Cone Range Coverage:        86.4%  (Threshold >= 80.0%)
  [PASS] Optimal Limit Slippage Reduction: +1.42% net expectancy improvement
  [PASS] HTML Visualizer Validation:       50/50 standalone files valid
======================================================================
  FINAL EVALUATION VERDICT: SUITE PASSED (Ready for Live Operation)
======================================================================

  ── Evidence provenance (run records) ──
  Records read: 2
    ↳ reports/run_record.json (complete, exit 0) — stages ran: [12] · skipped: none · failed: none
    ↳ tests/proof/scenario_manifest.json (complete, exit 0) — stages ran: [1, 2, 6] · skipped: none · failed: none
  Stage-12 reports proven: 50/50
  Gate payloads proven: 50/50
  Evidence completeness: OK (50/50 record(s) carry proven stage-12 evidence;
    50/50 record(s) carry proven gate payloads)
  Rule coverage: OK (38 distinct rule exercise(s) across the graded classes;
    frozen floor 38)
```

**Gate 2 discloses its class coverage.** Breaker accuracy is an average over the records the gate could grade, so a class whose records all abstained contributes nothing to it and a percentage over a smaller graded set reads exactly like one over the whole benchmark. The coverage line — printed whatever the verdict, including a `[PASS]` — therefore names every class with `graded/total`, the records it had to abstain on (`0/N graded, N abstained`, which is the case a bare 100.0% would hide), a `(quota N)` marker when the dataset is short of that class's frozen quota, and the classes with no breaker in the frozen registry at all (`run_evals.class_coverage`). It discloses, it does not re-weight: an abstention with a recorded reason is registered debt by design (§4.1), and the ones nobody declared already fail the run as unregistered gaps.

**A record is not a rule exercise.** Forty graded records do not mean forty rules probed, so the same line carries the **rule-exercise census**: for each class, how many distinct rules its graded records actually exercised, and how many records added nothing the class did not already stand for (`8 of 10 repeat an exercise` — counted as the runner credits each class, not as `graded − distinct`, because one record can stand for several arms at once). An exercise is `(arm, measured value)` — the arm being one comparison of the breaker (the R:R breaker has two: the recommended-band midpoint and the executable price) and the value rounded to the granularity that rule speaks in (percent to 0.1, R:R to 2 decimals, ATR multiple to 2 decimals, sessions whole), so 0.9000644 and 0.9001122 are one exercise of "R:R ≈ 0.90, below 1.0", not two (`run_evals.rule_exercises`). A trap row exercises the arm that tripped; a **clean** row exercises the same arms from the other side — the boundary check that stayed clear, at the value that cleared it (`raw_rr_below_1:clear` at `2.2`) — because "nothing leaked" is not one rule but one rule per arm, and how far under each threshold the clean rows sit is the only thing a class of clean rows can be measured on. Both sides are `(arm, value)`; neither is a record count.

The census may not shrink. `baseline_scorecard.json` freezes it (`rule_exercises`, banked by every deliberate re-freeze), the runner recomputes it each run, and a class falling below its frozen count fails the run naming the class and the delta — a class can lose the row that carried its only exercise at 54.2% and still look graded, which is exactly the silent regression this floor exists to catch. Growth is free: adding distinct evidence never needs a re-freeze, only lowering the floor does.

### 4.1 Evidence Provenance (Run Records)

A scorecard may only claim what it can point at. Graded bytes are not evidence by themselves — a report left behind by an earlier session, or edited by hand, reads exactly like one a stage produced — so every artifact a gate grades must be traced to a **run record** (`uvest-run-record/1`), the ledger written beside the artifacts by the run that produced them:

| Field | Meaning |
| :--- | :--- |
| `stages[].order` | the row's position in that run's walk |
| `stages[].stage` | the pipeline stage the row is about |
| `stages[].status` | `ran` (executed, artifact verified), `failed` (executed, did not land), `skipped` (disclosed, deliberately not executed) or `not_reached` (the run stopped first) |
| `stages[].artifact`, `stages[].artifact_path` | the artifact the row is about, and where it landed |
| `stages[].artifact_sha256` | the SHA-256 of the bytes that run produced |
| `stages[].exit_code`, `skip_reason`, `seconds` | what the stage returned, why it was skipped, how long it took |

**The grading rule.** A gate reads an artifact only when a record shows the producing stage `ran` *and* the file's SHA-256 still equals the recorded one. Only a `ran` row may claim bytes; every other row names at most the artifact a stage was about — an un-executed stage never gets to assert what it produced. Anything unproven is reproduced first (`tests/evals/report_regen.py` replays it into a sidecar and compares; the graded file is never overwritten), and if it still cannot be proven the record is **excluded and the run fails**. A record that contradicts the disk — a `ran` row whose bytes are gone or changed — fails the run too: a witness that misstates its own output is worse than no witness.

**Payload coverage.** Gate inputs are not only stage-12 reports. **Every payload a graded gate reads** is evidence: the Zero-Hallucination differ backs a report's numbers from the record's whole JSON, so any payload the record carries can back a graded claim, and gate 2 reads named artifacts — a record's class breaker out of the payload the dataset carries, even when the record does not carry that artifact at all (`run_evals.gate_payloads`). Coverage splits the gaps it finds into the unregistered ones that fail the run and the registered debt it discloses and excludes (`run_evals.check_payload_coverage`). The proof root's per-artifact declarations are therefore folded into the same vocabulary (`tests/evals/proof_root.py`) — one row per declared artifact, `recorded` becoming `ran` with the frozen sha256, and `waived`/`quarantined` becoming disclosed `skipped` rows whose reason names the waiver or the quarantine register. The fold is a *read* of the frozen proof root (the hash file is the claim, the disk is the check), validated by the same rules `provenance.load` applies to a ledger, so a folded record is never a weaker witness than a stored one — and a payload edited after the freeze stops being proven.

**Unregistered vs registered gaps.** A payload with no record behind it stops being evidence two ways, and the harness treats them differently (`run_evals.payload_gap_kind`). An **unregistered** gap is one nobody declared: bytes the dataset carries inline rather than by `$ref` (`scenario-manifest-schema.md` §3 E1 — a payload a graded gate reads must be a recorded artifact, never an extracted subset typed into the dataset), an artifact no declaration covers at all, or a declared recording whose bytes are gone or changed. Those are holes in the graded set that no note discloses, so they fail the run. **Registered debt** is declared: the proof root marks the artifact `waived` (E4 — its engine does not exist yet) or `quarantined` (E5 — legacy bytes kept on disk for migration continuity), which folds to a disclosed `skipped` row. The gates abstain on both kinds (`run_evals.unbacked_payloads` for inline bytes) — unproven bytes are never evidence — but registered debt is disclosed and excluded rather than fatal, exactly as the dataset's own mid-curation note requires; making it fatal would keep the suite red until every engine exists. Derived outcome claims (`$derive`) are not payloads: they are recomputed at load from a hash-frozen tape, so typed bytes cannot back them either.

**No silent shrink.** The gates are defined over the whole benchmark, so an excluded record is a hole in the graded set: a scorecard that quietly graded 29 of 31 records would claim more than it checked. `Evidence completeness` is therefore part of the verdict, over **both halves** — stage-12 evidence and gate payloads — and the run exits non-zero naming each record with an *unregistered* gap and its obstacle, while printing the registered exclusions it excludes from grading. A payload the proof root declares `waived` or `quarantined`, or that the dataset carries inline, backs nothing: the gate abstains on it rather than counting it clean. Disclosure alone never turns a smaller graded set into a pass; a typed payload never rides along as one either.

**Where records come from.** The record beside the graded artifacts is always read; `--run-record PATH` (repeatable) folds in others, e.g. a real chain run, whose rows prove which stages of the 0 → 12 walk ran and which were disclosed as skipped; and the proof root is folded in as the record of its own payload declarations (`tests/evals/proof_root.py`), so a payload's provenance is proved the same way a report's is. The scorecard prints the evidence ledger per record, so a reader sees exactly how much of the graded set traces to a recorded execution and which stages were skipped instead of silently missing — followed by the `Evidence completeness` line that carries that coverage into the verdict.
