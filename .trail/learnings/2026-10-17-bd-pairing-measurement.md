# 2026-10-17 — B+D pairing measured (envelope-depth proposal §6)

## Decided
- Extended `tests/evals/envelope_depth_sweep.py` with the B+D cross: gate 5 over
  depths P0–P9 × per-class atom rules N ∈ {30, 20, 10, 5, 1} implemented by
  temporarily patching `4_ml_direction.MIN_SECTOR_ATOMS_PER_CLASS` around
  `samples_for` (the one frozen constant `_sector_block_usable` reads).
- Added a **gate-4 table**: the pools also feed `race_probability`, so B+D must
  quote the race Brier per rule (0.410 today → 0.406 best case, ~2.5× the 0.18
  threshold — D rescues nothing on calibration, measured so it can't be oversold).
- Gate-4 anchor: the N=30 recomputed Brier must equal
  `tests/proof/baseline_scorecard.json`'s committed 0.409726949… to 1e-12 or the
  tool raises (same loud-fidelity precedent as the P0 envelope anchor).
- Outcome: **the P4 headline is invariant across every rule (82.8%, 24/29,
  same five residuals)** — D moves median width only (34.7% → 31.2% best case).
  Below N=20, P6 pays: `GB-2022-0002` flips out (93.1% → 89.7%) on its 12-atom
  auto block. So the proposal's recommendation changed from "B, optionally
  paired with D" to **B alone**; D is offered as a width option in Q4 with the
  measurements inline.
- Proposal restructured: new §6 (pairing tables + census + reading), recommendation
  now §7, sections renumbered; §2 row annotated that `^NSEBANK`'s 126-atom block
  serves its 12 graded rows under every rule.

## Follow-up (same day): tail stress ladder + one-off side-by-side
- Added the **tail stress ladder** to `envelope_depth_sweep.py` (Q3 evidence,
  stays in the default output): 2/6 (33.3%) at P0–P3, 3/6 (50.0%) at P4/P5,
  4/6 (66.7%) at P6/P7, 5/6 (83.3%) at P8, 6/6 only at P9; flip chain
  SCEN-004@P4 → SCEN-003@P6 → SCEN-008@P8 → SCEN-001@P9. The tool asserts
  ladder monotonicity (levels nest) and aborts otherwise — tail coverage is
  monotone even though the overall headline is NOT (representative
  ceiling-bound re-open at P2 exists only outside the tail).
- Added `--pairwise`: each graded row's P4 cone under its own sector block vs
  pooled at rule N=20, both envelopes' containment. Findings pinned in the
  seam: exactly 3 switchers (GB-2022-0010 INFY, GB-2022-0011 TCS,
  GB-2022-0014 ITC), all ✅ under BOTH pools (no verdict moves); the only
  own-block-vs-pooled disagreement is GB-2025-0030 (own ✅ / pooled ✗) whose
  ^CNXFINSERV25 block has a 0-atom cell — unusable at every N, so the resolver
  correctly keeps it pooled; its own-block ✅ is precisely the guess the rule
  refuses to take.
- Diagnostic flag: patches `MIN_SECTOR_ATOMS_PER_CLASS` only around
  `samples_for`, restores in `finally`, absent from the default run (verified
  by diff: pairwise output = default 129 lines + appended section).

## Third batch (same day): tail price at P6, N=20 disclosure census, P4 blast radius
- New tool section `### Tail slice only, per rule — the B / B′ tail price`:
  P4 tail **rule-invariant** (3/6=50.0%, same ids ∀ rules); P6 4/6=66.7% at
  every N ≥ 5, 5/6=83.3% only at N=1 (SCEN-008 on its 4-atom infra block).
  ⇒ Q3's tail floor is a pure function of depth — signable independently of Q4.
- N=20 disclosure cost published as proposal §6.5: literals live in
  domain.md:109, evals.md:59, TICK-004:71 (+ actionplan history:307); code has
  the constant once (4_ml:157); BRD/train/calibration/tests reference the
  symbol (auto-true). Real bill = retrained CV figures (99.72% OOF share,
  76.02/77.10) and the seams quoting them.
- P4 blast radius measured from disk and published §9: 31 recorded
  `out_4_ml.json`, 720 manifest.sha256 entries, 59 HTML (58 + sample), 2 model
  files, 10 baseline gates (gate 5 must move 55.2→82.8; gate 4 must NOT move
  0.410), calibration gains Q2's `cone_levels` (key absent today), and the
  sweep seam must be re-cut after implementation (P0==55.172…% is the last
  holder of the old world).

## Ruled out
- Claiming D as coverage-buying at P4: falsified by the cross — it isn't.
- Trusting the first census output: it had inverted polarity (`pool` False means
  the row reads the SECTOR block, not pooled). Fixed before publishing; the
  column header now says "reading POOLED today".
- Quoting a tail floor without its depth: Q3's floor is only signable against
  the ladder (≤50% ⇒ P4+; 100% ⇒ P9 only).

## Watch out
- `row["pool"]` truthiness: True = pooled fallback, False = usable own-sector
  block — easy to invert when reusing the sweep's graded-row shape.
- Gate-4 scope is 31 rows (29 graded + the two October tapes carrying stored race
  claims); the envelope fidelity banner prints "over 31 rows" for the same reason.
- The seam test counts the matrix P4 line's five "82.8%" occurrences — "| 82.8% |"
  cannot be used (shared pipes make the substring overlap and count 3).
- A multi-part (and this session also a **single**-replacement) `str_replace`
  re-indented code while reporting success — the flip loop got `continue` merged
  with `lo_pct`, and the §6.5 insertion dedented bd_tables' detail-row print
  from 12→8 sp, so the table silently printed ONE stale-`rule` row per depth.
  Both survived `py_compile`; only grep-on-output and the seam caught them.
  After ANY multi- or large replacement: `py_compile` AND grep the expected
  row counts of each printed table, then run the seam — never trust success.
- Assigning a new table the same row prefix (`| P6 | N=30 `) as an existing
  one breaks split-based test slices: bound the old slice at the NEW section
  header (`"### Tail slice only"`) rather than the next `##` — tighter, not
  weaker.
- Parsed table cells in tests need `.strip()` after `split("|")` — trailing
  space after `split("(")` silently breaks list equality asserts.
- Nothing here moves any threshold, spec level or pool rule: Q1–Q4 still await
  the owner's signature. Changes uncommitted.
## Staged signature text — Q2 restatements + Q4 D13 amendment (later the same day)

**Decided:** everything the owner needs to sign Q2/Q4 now lives in the proposal
(§8 "Signing Q2" / "Signing Q4") as verbatim find→replace blocks, nothing applied.

**Found while staging (each grep- or git-verified):**
- BRD §4 has **no literal "80%"** — the only `80%` in BRD.md is sample-verdict cell :658.
  The line whose nominality the tokens imply is the Expected Range row, BRD.md:137;
  the proposal's older "BRD §4 '80% range'" shorthand was corrected in the Q2 row.
- **`config/` never existed in git history** — the earlier checklist's
  "config/unvest.json hook" reference was unverifiable and was replaced with
  repo-true constant-change discipline (from→to in commit message + decision text).
- **domain.md:22 (glossary "Price Cone") is a 4th Q2 consumer** — the only place
  still stating "10th/90th percentile" literally; Q2's set and §9's count moved 3→4.
- No hardcoded "80%" label exists in stage 12 (`12_generate_html_report.py:58` is
  depth-neutral), so the 59 regenerated reports follow bytes, not strings.

**Watch out for:**
- If Q4 (N=20) signs, gate 4's stop condition **flips**: it must re-read 0.409
  (the sweep's N=20 recompute) and the baseline re-freezes to it — it no longer
  "must not move". The amendment's execution slot is filled at the re-record,
  never before; the sweep's race anchor re-cuts with the constant.
- The Q2 blocks are drafted at P4; a P6 signature swaps only the end levels
  (0.01/0.99, nominal 98%) in all four blocks.

## The staged text got its own seam, and Q3 got staged (same day, later)

- `TestTheStagedSignatureTextIsLive` (tests/test_envelope_depth_sweep.py, now 9
  tests) pins all eight staged `find` strings two-sided: exactly once in the
  live BRD/TICK-004/evals/domain bytes **and** verbatim in the proposal — it
  caught one real defect on its first run (the Q3 block had dropped the
  threshold cell's leading `|`).
- Escaped-backtick spans (`\``) in the staged blocks were a copy-paste defect;
  replaced with double-backtick spans carrying raw bytes.
- Q3 staged at §8 "Signing Q3": one evals.md §3 #5 threshold-cell edit, tail
  floor drafted at 50.0% (P4 ladder value), execution obligations = runner line
  + 11th banked gate + TestGradedSetSlices re-cut; note the name collision —
  evals.md's existing "signed Q3" is the 2026-10-16 slice split.
