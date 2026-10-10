# ADR-007 (PROPOSED): Consolidate the Eval Harnesses — One Engine, Layered Datasets
*Decision memo for owner approval. No frozen document is edited by this file; approval means the paste-ready ADR block at the bottom is appended to `docs/agents/domain.md` and the listed re-freeze items are authorized.*

**Status:** PROPOSED — do not implement until approved
**Date:** 2026-10-10
**Supersedes in part (on approval):** `EVAL-001-benchmark-suite.md` acceptance criteria, `docs/agents/evals.md` §2–§3 gate list
**Honors unchanged:** `business_test_cases.md` (frozen & immutable), `BRD.md` §15.1 (command path and `tests/proof/` layout)

---

## 1. The Question

The repository now carries two overlapping evaluation artifacts:

| Artifact | Runner | Dataset | Scope | Grading model |
| :--- | :--- | :--- | :--- | :--- |
| **EVAL-001** (committed, half-seeded) | `tests/run_evals.py` | `tests/evals/golden_dataset.json` (50 expected, 31 seeded) | Behavioral pass/fail on stored per-stage payloads, 2022–2026 | 7 frozen gates, `[PASS]/[SKIP]/[FAIL]`, class quotas |
| **Business proof suite** (frozen, unbuilt) | `tests/run_business_proof.py` (mandated) | `tests/proof/` + `baseline_scorecard.json` (60 scenarios, 2018–2026) | Point-in-time replay of stages 0→9 with filesystem blindness | S1–S7 gates, confusion matrix, Net Edge Multiplier (ΔE) regression |

Both exist to answer the same business question — *does the engine's YES actually make money, and is it honest when data is missing?* — and both grade the same pipeline. Today they share no code, no dataset schema, and no gate definitions. The duplication is already drifting: `docs/agents/evals.md` §3 still lists the old 7 dimensions (Brier, cone coverage, MAE reduction) while frozen `BRD.md` §15.3 mandates S1–S7 plus breaker enforcement; `business_test_cases.md` §2 mandates a runner that does not exist; `docs/tickets/INDEX.md` declares no pending work.

## 2. Options Considered

### Option A — Keep two independent harnesses
Build `tests/run_business_proof.py` from scratch alongside `run_evals.py`.

- **Rejected.** Two scorers means two copies of gate arithmetic, two dataset schemas, two places a breaker threshold can silently change. `docs/understanding.md` §4.5 explicitly forbids the fork: the suite "extends them, never forks them". The existing drift between `evals.md` and `BRD.md` §15 is what this option makes permanent.

### Option B — Full immediate merge (delete the golden dataset now)
Move all 31 records into `tests/proof/scenarios/`, delete `golden_dataset.json` and the old runner gates, ship one runner immediately.

- **Rejected.** The business suite cannot be graded until stages 4–9 (and stage 11's calibration) exist; a full merge today converts the only green Layer-2 signal (breaker encoding, cone coverage, MAE evidence across 31 records) into a wall of `[SKIP]` for weeks. It also forces deletion/replacement of a frozen artifact in one step instead of a verifiable migration, and it collides with the bootstrap floor (`docs/understanding.md` §4.4: no gate is graded until ≥30 replayed trades across ≥5 classes).

### Option C — Merge the machinery and the dataset schema; keep two thin execution profiles
**Chosen.**

1. **One gate engine.** Gate definitions (S1–S7 + zero-hallucination + breaker enforcement), the confusion-matrix builder, the baseline/ΔE comparator, the scorecard renderer, and the 0/1/2 exit-code semantics live in exactly one module shared by both entry points. No gate threshold is ever defined twice.
2. **One dataset schema.** The scenario manifest is the canonical dataset. Golden records migrate into it as rows tagged `origin: curated_behavioral`; scan-generated business scenarios are rows tagged `origin: scan_generated`. One SHA manifest governs both. Physically it lives under `tests/proof/` per the frozen layout; the golden file's remaining lifetime is as a fast subset, not a parallel schema.
3. **Two entry points, both thin front-ends.**
   - `tests/run_evals.py` — fast profile, stays the pre-commit gate. During migration it keeps its current gate names and output lines (the `tests/test_run_evals.py` contract pins them), so CI never goes dark.
   - `tests/run_business_proof.py` — full replay profile. The frozen signature `python tests/run_business_proof.py --baseline tests/proof/baseline_scorecard.json` is honored verbatim; until replay data exists it emits `[SKIP]` per the same honesty rule the current runner already uses.
4. **Layer-1 relocations, not gate deletions.** Per the frozen mapping in `docs/understanding.md` §4.5: option rejection moves to the stage-0 unit seam, HTML validity to the stage-12 unit seam, zero-hallucination stays a graded gate, and the old Brier/cone/MAE dimensions become explicit evidence feeds for S2/S5/S7 rather than parallel gates.

## 3. Decision

**Yes — merge into a single harness (engine + dataset schema + manifest), driven by two execution profiles.** Do not build a second scorer, and do not maintain two dataset schemas.

The frozen `business_test_cases.md` interface survives untouched: its mandated command path and `tests/proof/` layout are adopted as the full profile's home. `run_evals.py` shrinks to (a) the fast profile and (b) the Layer-2 fixture subset of the one manifest.

## 4. Tradeoffs

| | Benefit | Cost / mitigation |
| :--- | :--- | :--- |
| One gate engine | Single place to fix or calibrate gate math; S1–S7 and the old dimensions cannot disagree | One-time extraction refactor. Mitigation: mechanical, behavior-preserving step first — scorecard bytes and 104-test suite stay green. |
| One dataset schema | Golden records stop being a parallel universe; every new scenario (scan-generated or curated) enters identically; one SHA manifest for gate S6 | Migration touches `curate.py` output shape and `test_run_evals.py` expectations. Mitigation: migrate in the last step, under the approved re-freeze, never to make a failure pass. |
| Two profiles | Pre-commit stays fast and offline; full replay can be heavy and bootstrap `[SKIP]` honestly | Two commands to document. Mitigation: one shared `engine.py`; profile is a flag, not a code path fork. |
| Frozen-path compliance | `run_business_proof.py --baseline ...` exists exactly as the immutable doc demands | Bootstrap mode means S1–S7 stay `[SKIP]` until the pipeline and replay fixtures exist — but that is the frozen bootstrap-floor rule, not a workaround. |
| Kailash Nadh discipline | One engine, one manifest, stdlib-first, no second framework | Net *less* code than building the mandated runner independently. |

**Effort estimate:** engine extraction ≈ 0.5 day; S1–S7 module + synthetic-stack unit tests ≈ 0.5 day; manifest/schema migration ≈ 1 day; frozen-doc re-freeze pass and ticket re-scope ≈ 0.5 day. The business profile does not become *gradeable* until stages 4–9 land; the engine, manifest, SHA integrity, and gate arithmetic can all land before them.

## 5. Migration Plan (each step independently verifiable)

1. **Extract, change nothing.** Move gate classes/arithmetic out of `tests/run_evals.py` into the shared engine; `run_evals.py` imports it. Acceptance: identical scorecard output and exit codes, all invariant tests green, no gate value changes.
2. **Add the business profile and S1–S7 arithmetic.** Engine gains confusion-matrix, per-bucket calibration, per-regime honesty, AVOID cost accounting, stewardship counterfactual, SHA integrity, marginal-band checks, and baseline/ΔE comparison — each graded by unit tests against synthetic verdict stacks (the gate logic is testable long before replay data exists). `tests/run_business_proof.py` lands as a thin front-end emitting `[SKIP]` until the ≥30-trades / ≥5-classes bootstrap floor is met.
3. **Migrate the datasets.** Golden records become manifest rows with recorded bundles replacing hand-typed payloads where stages exist; `class_counts_expected`/50-trade meta retires in favor of the frozen 60-scenario quotas; `test_run_evals.py` expectations update only as part of the approved re-freeze, in the same change that moves the relocated Layer-1 checks to their stage seams. The row schema and evidence rule this step consumes are specified in [scenario-manifest-schema.md](scenario-manifest-schema.md).

## 6. Frozen-Document Impact (owner-commanded re-freeze required — nothing here is edited silently)

| Document | Required change on approval |
| :--- | :--- |
| `docs/agents/domain.md` | Append the ADR-007 block below. |
| `docs/tickets/EVAL-001-benchmark-suite.md` | Re-scope: fast profile + Layer-2 subset; cite ADR-007; keep seam `tests/run_evals.py`. |
| `docs/agents/evals.md` | §2–§3 mapped onto S1–S7 (Layer-2 evidence) instead of the old parallel gate list. |
| `AGENTS.md` | §4 and the "50 historical trades" reference re-point to the unified suite (60-scenario manifest + fast profile). |
| `BRD.md` §14 lifecycle bullet | "50-trade benchmark" → unified suite wording. |
| `business_test_cases.md` | **No change.** Command path, layout, quotas, S1–S7 definitions are adopted verbatim. |
| `docs/tickets/INDEX.md` | Add the owning ticket for the business-proof harness (the missing row) once sliced. |

## 7. Open Questions for the Owner

1. Approve ADR-007 and the EVAL-001/evals.md/AGENTS.md re-freeze in one pass, or keep the fast profile's old gate names indefinitely?
2. Should pre-commit run the full profile once gradeable, or keep pre-commit fast and gate the full profile to CI/release only?
3. Confirm the fast profile's 31 golden records are migrated (provenance-tagged) rather than kept as a duplicate file — the merge only pays off if nothing is hand-typed twice.

---

## Paste-Ready ADR Block (for `docs/agents/domain.md` on approval)

### ADR-007: Single Eval Harness — One Engine, Layered Datasets
- **Status:** APPROVED & FROZEN
- **Context:** Two overlapping evaluation artifacts existed — the EVAL-001 behavioral runner over a 50-trade golden dataset, and the frozen business proof suite (60 point-in-time scenarios, S1–S7, confusion matrix, baseline regression) mandated by `business_test_cases.md` and `BRD.md` §15. They shared no gate code or dataset schema, and their gate lists had already drifted.
- **Decision:** Consolidate. One shared gate engine (S1–S7 + zero-hallucination + breaker enforcement + confusion matrix + baseline comparator), one canonical scenario manifest schema under `tests/proof/` with provenance-tagged rows, and two thin execution profiles: the fast behavioral profile (`tests/run_evals.py`, pre-commit) and the full point-in-time replay profile (`tests/run_business_proof.py --baseline tests/proof/baseline_scorecard.json`, signature honored verbatim). The frozen `business_test_cases.md` is adopted unchanged. Layer-1 checks (option rejection, HTML validity) move to their stage unit seams per `docs/understanding.md` §4.5; no gate is weakened or deleted.
