# Proposal — Gate 5's Envelope Depth: deepen the pooled tail or re-scope the graded set

**Status:** PROPOSED — awaiting the owner's decision (ADR-004: thresholds and
spec levels are the owner's frozen call; the ≥ 80% floor was itself re-signed
*keep* on 2026-10-16 in [proposal-gate-thresholds.md](proposal-gate-thresholds.md)
Q2, and this document does **not** move it). Nothing below is typed: every
figure is reproduced by one command against committed bytes —
`python tests/evals/envelope_depth_sweep.py` — whose **P0 row equals today's
committed gate-5 reading exactly** (16 of 29 = 55.172…%, slices 14/23 and 2/6,
the same thirteen miss ids), with the recomputed floor/ceiling matching each
record's stored `cone_p10`/`cone_p90` claims to within 0.0105 INR — the stage's
own 2-dp rounding. Thresholds, weights, pools and the graded set are untouched
by the tool; it only re-reads the pools at deeper levels.

---

## 1. The question

Gate 5 (evals.md §3 #5) grades `[P10, P90]` — a nominal ~80% path envelope —
against **29** in-window records and reads **55.2%** (representative 14/23 =
60.9%, tail 2/6 = 33.3%). The thirteen misses are named on every coverage line
with their pool percentile; the four **tail** misses the campaign left standing
are the hardest rows in the corpus:

| record | realised low..high (of basis) | percentile in the pool its cone read |
| :--- | :--- | :--- |
| `SCEN-001` (RELIANCE) | −2.23% .. **+63.17%** | **100.0th** of the pooled favourable pool |
| `SCEN-003` (SBIN) | **−17.05%** .. −3.68% | **1.7th** of ^NSEBANK (3,501 atoms) |
| `SCEN-004` (LT) | **−17.57%** .. −3.43% | **2.0th** pooled (its own ^CNXINFRA block defers — decision 15's per-class thinning) |
| `SCEN-008` (ADANIPORTS) | **−43.48%** .. −6.07% | **0.2nd** pooled (same deferral) |

Two levers can move this reading, and both were measured before being written
down: **deepen the pooled tail** — read the *same* pools at deeper quantile
levels (one frozen constant, `CONE_SPEC`, read identically by the stage and by
the trainer's CV) — or **re-scope the graded set** — change which records the
headline counts. Neither touches the ≥ 80% floor; both are definition-level
moves that need a signature if adopted.

## 2. The P0–P9 envelope table (measured; thresholds untouched)

Ten candidate `(floor level, ceiling level)` pairs on the observed excursion
mixtures, per-row class weights captured from stage 4's own `predict` over each
row's recorded fixtures. *Nominal* is the interval's own coverage on the pool
it reads (ceiling level − floor level); *width* is the median envelope width
across the 29 graded records, as a percentage of the entry basis — the rupee
cost of the depth, since the fan chart and BRD §4's Expected Range are exactly
this width.

| id | floor | ceiling | nominal | headline (of 29) | representative (of 23) | tail (of 6) | still missing | median width |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- | ---: |
| **P0** (shipped) | 0.100 | 0.900 | 80% | **16 → 55.2%** | 14 → 60.9% | 2 → 33.3% | the 13 recorded misses | **23.0%** |
| **P1** | 0.100 | 0.950 | 85% | **17 → 58.6%** | 15 → 65.2% | 2 → 33.3% | 12 (GB-2022-0020 closes) | **25.9%** |
| **P2** | 0.050 | 0.900 | 85% | **18 → 62.1%** | 16 → 69.6% | 2 → 33.3% | 11 (`0008` + `0010` close; ceiling-bound `0020` re-opens) | **25.8%** |
| **P3** | 0.050 | 0.950 | 90% | **19 → 65.5%** | 17 → 73.9% | 2 → 33.3% | 10 (closed vs P0: `0008`, `0010`, `0020`) | **28.8%** |
| **P4** | 0.025 | 0.975 | 95% | **24 → 82.8%** ✅ | 21 → 91.3% | 3 → 50.0% | 5 (`GB-2022-0002`, `GB-2025-0030`, `SCEN-001`, `SCEN-003`, `SCEN-008`) | **34.7%** |
| **P5** | 0.020 | 0.980 | 96% | **24 → 82.8%** ✅ | 21 → 91.3% | 3 → 50.0% | same 5 | **37.0%** |
| **P6** | 0.010 | 0.990 | 98% | **27 → 93.1%** ✅ | 23 → 100.0% | 4 → 66.7% | 2 (`SCEN-001`, `SCEN-008`) | **43.6%** |
| **P7** | 0.005 | 0.995 | 99% | **27 → 93.1%** ✅ | 23 → 100.0% | 4 → 66.7% | same 2 | **53.0%** |
| **P8** | 0.001 | 0.999 | 99.8% | **28 → 96.6%** ✅ | 23 → 100.0% | 5 → 83.3% | `SCEN-001` | **89.5%** |
| **P9** (observed range) | 0.000 | 1.000 | 100% | **29 → 100.0%** | 23 → 100.0% | 6 → 100.0% | — | **120.9%** |

✅ = clears the unchanged ≥ 80% floor. **P4 is the shallowest depth that does**
(24/29 = 82.8%, representative 91.3%, tail 50.0%) at a median width 1.51×
today's (23.0% → 34.7% of the entry). The pairs are deliberately **not
nested** — widening only the floor can re-open a ceiling-bound upside miss
(`GB-2022-0020` covers at P1's 0.95 ceiling but re-misses at P2's 0.90), which
is why the "still missing" count moves 12 → 11 → 10 rather than monotonically
down; every cell is measured, none is inferred. P9 is the degenerate endpoint
that proves the axis, not an option: an envelope spanning the observed
nine-year range (median ±60% of the entry) is no longer an "expected range"
anyone can trade against, and it would make the gate unfalsifiable by
construction.

Context for what depth *means*: the shipped P0 object is honest out-of-fold
(**77.81%** bar-path containment against its nominal 80%, calibration
`provenance`), so the benchmark's 55.2% is severity of the curated set, not a
miscalibrated envelope. Every depth P4+ trades calibration nominality for
containment of curated tail events — a 95–99% object graded at a ≥ 80% floor
measures how adversarial the catalog is, which is still a measurable claim, but
it must be *stated as* a 95% object (see decision point Q2).

### The tail stress ladder — Q3's split, priced at every depth

Below, the tail slice judged at **its own floor** at every candidate depth,
today's pools (the tool asserts this ladder is monotone, since the levels
nest). Columns are the tool's output verbatim; the sentence after it is the
price annotation.

| depth | tail slice (of 6) | clears any own floor ≤ | tail rows still held out | headline (of 29) |
| :--- | ---: | ---: | :--- | ---: |
| P0 | **2/6 → 33.3%** | 33.3% | SCEN-001, SCEN-003, SCEN-004, SCEN-008 | 55.2% |
| P1 | **2/6 → 33.3%** | 33.3% | SCEN-001, SCEN-003, SCEN-004, SCEN-008 | 58.6% |
| P2 | **2/6 → 33.3%** | 33.3% | SCEN-001, SCEN-003, SCEN-004, SCEN-008 | 62.1% |
| P3 | **2/6 → 33.3%** | 33.3% | SCEN-001, SCEN-003, SCEN-004, SCEN-008 | 65.5% |
| P4 | **3/6 → 50.0%** | 50.0% | SCEN-001, SCEN-003, SCEN-008 | 82.8% |
| P5 | **3/6 → 50.0%** | 50.0% | SCEN-001, SCEN-003, SCEN-008 | 82.8% |
| P6 | **4/6 → 66.7%** | 66.7% | SCEN-001, SCEN-008 | 93.1% |
| P7 | **4/6 → 66.7%** | 66.7% | SCEN-001, SCEN-008 | 93.1% |
| P8 | **5/6 → 83.3%** | 83.3% | SCEN-001 | 96.6% |
| P9 | **6/6 → 100.0%** | 100.0% | — | 100.0% |

*Price annotation:* a separately-floored tail section (Q3) can honestly sign
any floor ≤ the ladder's own value at its depth — ≤ 50% needs P4/P5, ≤ 66.7%
P6/P7, ≤ 83.3% P8, and a 100% tail floor exists **only at P9** because
`SCEN-001` closes nowhere else. The rows give up one at a time along
`SCEN-004` (P4) → `SCEN-003` (P6) → `SCEN-008` (P8) → `SCEN-001` (P9).

*Price under B′ (depth P6, 98% nominality — the tool's per-rule tail table,
§6.5):* the tail clears any own floor **≤ 66.7% under every honest rule**
(N ≥ 5) — `SCEN-003` ejects against P4's 50.0%, buying +16.7 points of tail
headroom while `SCEN-001` and `SCEN-008` hold out; the fifth row's **83.3%**
exists only at N=1 (a 1-atom floor the atom rule refuses by docstring —
§6.4). Representative goes 91.3% → 100.0% and median width 34.7% → 43.6%
(×1.51 → ×1.90): **that is what 98% nominality buys the tail**.

## 3. Which record flips at which depth (the thirteen current misses)

| record | slice | realised low..high (of basis) | first depth that covers it (table order) |
| :--- | :--- | :--- | :--- |
| GB-2022-0002 (TATAMOTORS) | representative | +1.12% .. +20.39% | P6 |
| GB-2022-0007 (HDFCBANK) | representative | −12.26% .. −2.27% | P4 |
| GB-2022-0008 (ICICIBANK) | representative | −11.45% .. +2.05% | P2 |
| GB-2022-0009 (SBIN) | representative | −15.23% .. +0.63% | P4 |
| GB-2022-0010 (INFY) | representative | −15.44% .. −5.64% | P2 |
| GB-2022-0012 (LT) | representative | −14.70% .. −0.00% | P4 |
| GB-2022-0013 (TATASTEEL) | representative | −17.24% .. +3.58% | P4 |
| GB-2022-0020 (SUNPHARMA) | representative | −2.00% .. +12.66% | P1 |
| GB-2025-0030 (BAJFINANCE) | representative | −0.29% .. +20.70% | P6 |
| **SCEN-001** (RELIANCE) | **tail** | −2.23% .. **+63.17%** | **P9 only** (observed maximum) |
| **SCEN-003** (SBIN) | **tail** | **−17.05%** .. −3.68% | **P6** |
| **SCEN-004** (LT) | **tail** | **−17.57%** .. −3.43% | **P4** |
| **SCEN-008** (ADANIPORTS) | **tail** | **−43.48%** .. −6.07% | **P8** |

**The four tail misses, against both pools.** `SCEN-004` and `SCEN-008` read
the *pooled* block only because their own ^CNXINFRA block defers after decision
15's race-class thinning (206 `NEITHER` rows across 11 sectors); against the
infra block — whose own `p01` is −49.80% — they would close at **P2** and
**P5** respectively instead of P4/P8, i.e. *restoring sector-block
usability is a third, cheaper lever* for those two rows (same depths, smaller
width, no pooled-tail claim). `SCEN-003` reads its own usable ^NSEBANK block
already and closes at P6. `SCEN-001` is the hard stop: **+63.17% is the maximum
favourable atom of both the pooled and the energy block** — no finite depth
below the observed maximum contains it, so this record closes only at P9, the
degenerate endpoint. A "depth that closes all four tail misses" therefore does
not exist below infinity; any proposal claiming otherwise would be false.

## 4. The re-scope lever, measured (same P0 envelope, same threshold)

| variant | headline reads | clears ≥ 80%? |
| :--- | ---: | :--- |
| keep today's graded set (29 rows: rep 14/23, tail 2/6) | 55.2% | **no** |
| headline = representative slice only (tail becomes a disclosed stress appendix, still printed) | 60.9% (14/23) | **no** |
| grade the **close path** instead of the bars (decision 8's definition reversed) | 62.1% (rep 65.2%, tail 50.0%) | **no** |
| lower the floor to what today's model reads (≤ 55.2%) | 55.2% | yes (anti-pattern — proposal-gate-thresholds §3-B) |

Outcome- or class-based row drops are not listed: the signed Q3 rule grades by
class and never by outcome, so any drop that would flip one of the misses above
is excluded by definition. **Measured verdict: re-scoping the graded set alone
cannot clear the floor at any honest composition** — 60.9% is its ceiling, the
close-path reversal (which decision 8 rejected on principle *and* now on
numbers) still fails, and the only "re-scope" that passes is cutting the
threshold, which signed Q1/Q2 already rejected. The graded set is not the
lever; depth (§2) or pool usability (§3) are.

## 5. Options

| # | Option | Gate 5 reads (today's evidence) | Effort | Tradeoff |
| :--- | :--- | :--- | :--- | :--- |
| **A** | **Keep P0 and the graded set untouched.** The red stays published and banked by policy. | 55.2% (60.9% / 33.3%), red forever. | Zero. | Honest, and wallpaper: the number never changes because neither lever moved. |
| **B** | **Deepen `CONE_SPEC` to P4 [0.025, 0.975]** — one constant, read by the stage and the trainer's CV alike — at the unchanged ≥ 80% floor (recommended; Q1 below). | **82.8% PASS** (representative 91.3%, tail 50.0% disclosed), 5 residual misses named per record, median width 23.0% → 34.7% (×1.51). | Medium: swap the levels, disclose them in `calibration.cone_levels`, re-run the trainer, re-record the 31 stage-4 rows, regenerate reports, deliberate re-bank. | The `p10`/`p90` payload tokens become historical names (the eight keys are frozen by TICK-004 §2, so the level moves and the *name* is disclosed, exactly the decision-7 pattern); BRD §4's "80% range" line must state the signed depth; the object is now nominally 95% and the floor's meaning re-states with it. Not a skill claim — gate 4 stays red on its own evidence. |
| **B′** | **Deepen to P6 [0.01, 0.99]** — the shallowness-vs-tail midpoint. | **93.1% PASS** (representative 100%, tail 4/6), only `SCEN-001`/`SCEN-008` miss; width ×1.90 (43.6%). | Same as B. | Buys the tail slice a majority reading and full representative coverage at ~2× the rupee width; nominality moves to 98%, so the floor grades an ever-flatter object. |
| **C** | **Re-scope the graded set alone** (tail out of the headline, bars→closes, rows dropped). | Cannot clear the floor: 60.9% best case, 62.1% closes; drops are outcome-based and excluded by signed Q3. | — | **Measured dead end (§4)** — listed so the option is closed with numbers, not opinion. |
| **D** | **Restore sector-block usability** for the four tail rows whose cones defer to pooled (per-class atom rule relaxed or re-spec'd under decision 15's thinning). | `SCEN-004`/`SCEN-008` flip at **P2/P5** on their own ^CNXINFRA block instead of P4/P8 pooled; `SCEN-001`/`SCEN-003` unchanged (P9/P6). | Small-to-medium: `_sector_block_usable` is one frozen rule (30 atoms × every class × every measure) — any change reopens ADR-011 decision 13. | Pure pool-selection, no depth change: it cannot close the headline alone (4 of 13 flip at most), and §6 measures the B+D combination — the P4 headline is identical under every per-class rule; what D moves is median width (and, below N=20, a traded P6 miss). |

## 6. The B+D pairing, measured

Depth and the per-class atom rule share one pool selector (`samples_for` →
`_sector_block_usable`), so B cannot be recommended on depth readings alone.
This section runs the same sweep under the frozen rule (**N=30**) and four
relaxed ones. The N=30 column reproduces §2 exactly, and the tool aborts if
the N=30 recomputed race Brier drifts from `tests/proof/baseline_scorecard.json`'s
committed value (0.409726949…) by more than 1e-12 — the same loud-fidelity
precedent as the envelope anchor.

### 6.1 Gate 5 headline, depth × per-class rule

| depth | N=30 (today) | N=20 | N=10 | N=5 | N=1 |
| :--- | --- | --- | --- | --- | --- |
| P0 | 55.2% | 55.2% | 48.3% | 48.3% | 48.3% |
| P1 | 58.6% | 58.6% | 58.6% | 58.6% | 58.6% |
| P2 | 62.1% | 62.1% | 55.2% | 55.2% | 62.1% |
| P3 | 65.5% | 65.5% | 65.5% | 65.5% | 72.4% |
| P4 | 82.8% | 82.8% | 82.8% | 82.8% | 82.8% |
| P5 | 82.8% | 82.8% | 82.8% | 82.8% | 86.2% |
| P6 | 93.1% | 93.1% | 89.7% | 89.7% | 93.1% |
| P7 | 93.1% | 93.1% | 89.7% | 89.7% | 93.1% |
| P8 | 96.6% | 96.6% | 96.6% | 96.6% | 96.6% |
| P9 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

### 6.2 The proposal depths, per rule — full readings

| depth | rule | headline (of 29) | representative (of 23) | tail (of 6) | still missing | median width |
| :--- | :--- | ---: | ---: | ---: | :--- | ---: |
| P4 | N=30 (today) | **24 → 82.8%** | 21 → 91.3% | 3 → 50.0% | GB-2022-0002, GB-2025-0030, SCEN-001, SCEN-003, SCEN-008 | 34.7% |
| P4 | N=20 | **24 → 82.8%** | 21 → 91.3% | 3 → 50.0% | GB-2022-0002, GB-2025-0030, SCEN-001, SCEN-003, SCEN-008 | 33.0% |
| P4 | N=10 | **24 → 82.8%** | 21 → 91.3% | 3 → 50.0% | GB-2022-0002, GB-2025-0030, SCEN-001, SCEN-003, SCEN-008 | 32.3% |
| P4 | N=5 | **24 → 82.8%** | 21 → 91.3% | 3 → 50.0% | GB-2022-0002, GB-2025-0030, SCEN-001, SCEN-003, SCEN-008 | 31.2% |
| P4 | N=1 | **24 → 82.8%** | 21 → 91.3% | 3 → 50.0% | GB-2022-0002, GB-2025-0030, SCEN-001, SCEN-003, SCEN-008 | 31.2% |
| P6 | N=30 (today) | **27 → 93.1%** | 23 → 100.0% | 4 → 66.7% | SCEN-001, SCEN-008 | 43.6% |
| P6 | N=20 | **27 → 93.1%** | 23 → 100.0% | 4 → 66.7% | SCEN-001, SCEN-008 | 39.6% |
| P6 | N=10 | **26 → 89.7%** | 22 → 95.7% | 4 → 66.7% | GB-2022-0002, SCEN-001, SCEN-008 | 39.4% |
| P6 | N=5 | **26 → 89.7%** | 22 → 95.7% | 4 → 66.7% | GB-2022-0002, SCEN-001, SCEN-008 | 39.3% |
| P6 | N=1 | **27 → 93.1%** | 22 → 95.7% | 5 → 83.3% | GB-2022-0002, SCEN-001 | 39.2% |

### 6.3 Gate 4 over the same pools — the race reads the same blocks

| rule N | race Brier over 31 | representative (of 25) | tail (of 6) |
| :--- | ---: | ---: | ---: |
| N=30 (today) | **0.410** — equals the committed baseline | 0.468 | 0.165 |
| N=20 | **0.409** | 0.468 | 0.165 |
| N=10 | **0.408** | 0.465 | 0.170 |
| N=5 | **0.406** | 0.463 | 0.170 |
| N=1 | **0.407** | 0.463 | 0.171 |

### 6.4 What the pairing does and does not buy (the runner's reading)

- **The P4 headline is invariant across every rule: 82.8% (24/29), the same
  five residuals, representative 91.3%, tail 50.0%.** D is not load-bearing
  for the recommendation — B passes with the frozen rule untouched.
- D's measured effect at P4 is **width only**: median 34.7% → 33.0% (N=20) →
  32.3% (N=10) → 31.2% (N≤5), with no coverage change at any level.
- At P6 the relaxed rules are not free: **N=10 and N=5 flip `GB-2022-0002`
  out** (93.1% → 89.7%) — the 12-atom auto block it then reads produces a
  narrower envelope that misses where the pooled pool covered. At N=1 the
  tail slice improves to 5/6 (`SCEN-008` closes on its own 4-atom infra
  block) while `GB-2022-0002` still misses — and a 1-atom floor is exactly
  the "guess about a tail" `_sector_block_usable`'s docstring refuses.
- **Gate 4 does not move materially**: 0.410 → 0.406 best case, still ~2.5×
  the ≤ 0.18 threshold. D rescues nothing on calibration — measured here so
  the pairing cannot be oversold later.
- **The side-by-side (`--pairwise`, one-off) confirms the wiring**: under
  N=20 exactly three graded rows change pools — `GB-2022-0010` (INFY),
  `GB-2022-0011` (TCS), `GB-2022-0014` (ITC) — and all three read ✅ under
  BOTH their own block and the pooled pool at P4, so the switch moves no
  verdict. The only own-block-vs-pooled verdict disagreement in the graded
  set is `GB-2025-0030` (own-block ✅ / pooled ✗): its `^CNXFINSERV25` block
  holds a 0-atom cell and is unusable at **every** N, so the resolver keeps
  it pooled — correctly; the ✅ on its own block is exactly the 0-atom guess
  `_sector_block_usable` exists to refuse.
- **B / B′ tail price, per rule** (the tool's tail-only table): P4's tail is
  **fully rule-invariant** — 3/6 → 50.0% under all five rules, the same three
  ids held out (`SCEN-001`, `SCEN-003`, `SCEN-008`); P6 reads 4/6 → 66.7%
  (`SCEN-001`, `SCEN-008`) under every rule N ≥ 5, lifting to 5/6 → 83.3% at
  N=1 only (`SCEN-008` closes on its own 4-atom `^CNXINFRA` block). The tail
  price is therefore a pure function of depth, not of the pool rule — Q3 can
  be signed against §2's ladder without also signing Q4.

### 6.5 What N=20 costs in disclosure — the guarantee's consumers, enumerated

Q4 can be priced as documentation work only by listing every place that states
the 30-atom guarantee. A repo-wide search (`MIN_SECTOR_ATOMS_PER_CLASS`,
"30 atoms", "decision 13") against the final bytes:

| consumer | how it states the guarantee | cost at N=20 |
| :--- | :--- | :--- |
| `scripts/4_ml_direction.py:157` (+ docstrings `:49`, `:564`, `:595`) | the constant `MIN_SECTOR_ATOMS_PER_CLASS = 30`; prose reads the *symbol* | **one literal moves**; docstrings re-read as written |
| `docs/agents/domain.md:109` (ADR-011 decision 13) | literal "(30) atoms" + the `^CNXBANK` example **and the measured figures** (99.72% OOF share, 76.02/77.10 containment) | restate the literal **and re-measure the figures** after retrain; amend under ADR-011's own amendment style — a decision-text entry, not a comment |
| `docs/agents/evals.md:59` | literal `MIN_SECTOR_ATOMS_PER_CLASS = 30` | restate the literal; the §2 measured block it points at re-measures with the retrain |
| `docs/tickets/TICK-004-lightgbm-cone.md:71` | literal "(30) atoms" inside the cone's frozen definition | restate, per the decision-7/8 definition-correction pattern |
| `BRD.md:174` | the *symbol*, qualified by "measured cost and benefit in evals.md §2" | no literal edit; the pointed-at numbers re-measure |
| `tests/evals/train_direction_model.py:577/:661/:716` | writes `pool_conditioning` prose naming the symbol (also mirrored at `models/lgbm_direction_v1.calibration.json:75326`) | rewrites itself on retrain — no hand edit, but its provenance gains the atom counts at the new floor |
| `actionplan.md:307` | historical log "(30)", frozen 2026-10-15 | append-on-change — history is never rewritten |
| seams: `test_4_ml_direction.py:641/:660`, `test_train_direction_model.py:708/:772/:779/:949` | build fixtures relative to the *symbol* (`N` and `N−1` cells) | parameterized — review, no expected rewrite; `TestSectorConditionedPools`'s published numbers re-measure with the retrain |

**The honest summary:** the *literal* lives in exactly three governed docs plus
the historical log — the prose cost of N=20 is small. The real bill is
measurement: N moves the trainer's CV (its CV measures the shipped selector),
so `provenance`'s OOF block share, containment figures and every published
number quoting them re-measure in the same change — that is disclosure debt,
not diff debt, and it is why option D's Effort stays "small-to-medium" rather
than small.

The tool's *Option D census* names which block thins out at which floor and
which graded rows switch pools: N=20 unlocks `^CNXIT`/`^CNXFMCG`
(`GB-2022-0010`, `GB-2022-0011`, `GB-2022-0014`); N=10 adds energy and auto
(9 rows total); N=5 adds pharma; N=1 adds infra and consumer-durables (15 of
the 29 read their own sector's block); `^NSEBANK` (126 atoms) serves its 12
graded rows under every rule.

## 7. Recommendation (the runner's opinion, not its call)

**B (P4) — alone.** The measurement in §6 retires this proposal's earlier
draft phrase "optionally paired with D": the P4 headline is 82.8% under every
per-class rule, so D buys no coverage at the recommended depth — only a
narrower median (34.7% → 31.2% best case) and, below N=20, a traded P6 miss
on `GB-2022-0002`. B is load-bearing; D is a width knob with a measured cost
below N=20, offered in Q4 rather than recommended. P4 is the shallowest
measured depth that clears the unchanged floor — 82.8% headline with the
tail slice still printed at 50.0% and five residuals named record-by-record —
and its width cost (×1.51) is the smallest that buys the pass. B′ remains
defensible if the owner wants the tail slice at 4/6 and the representative
slice at 100%; P8/P9 are rejected on width (×3.9/×5.3), and the proposal is
explicit that **`SCEN-001` closes at no finite depth** — it stays a named
residual at every honest option, which is what a tail record is for.

## 8. Decision points

| Question | Decision | Signed |
| :--- | :--- | :--- |
| **Q1** — envelope depth for gate 5: keep **P0 (80%)** / **P4 (95%, recommended)** / **P6 (98%)** / other, floor unchanged at ≥ 80% | ___ | ☐ |
| **Q2** — disclosure set for the new depth: `cone_levels` written into the calibration, `p10`/`p90` payload tokens kept frozen as historical names (TICK-004 §2), BRD §4's Expected Range row restated to the signed depth (BRD §4 carries no literal "80%" — grep-verified; its tokens are what imply it), evals.md §3 #5 nominality restated, and domain.md's Price Cone glossary restated (found while staging — the only place still stating "10th/90th percentile" literally). All four edits staged verbatim below | ___ | ☐ |
| **Q3** — graded set: keep the single representative+tail headline (default) / **also** split a separately-floored tail stress section — priced by §2's ladder: at P4 the rep slice alone reads 21 → 91.3% (clears ≥ 80% with room) and the tail 3 → 50.0%, so the tail's own floor must sign at **≤ 50%**; a ≤ 66.7% tail floor forces P6, ≤ 83.3% P8, 100% only at P9 (`SCEN-001`) — text staged below | ___ | ☐ |
| **Q4** — sector-block usability (option D), measured in §6: keep the frozen **N=30** (default — the P4 headline is identical under every rule) / **N=20** (same headline at every depth, medians −4.9% (P4) / −9.2% (P6), Brier −0.001) / **N≤10** (widths to 31.2%/39.3%; `GB-2022-0002` flips out at P6) | ___ | ☐ |

### Signing Q1 — the commit shape (draft; nothing executes until the boxes above are ticked)

**Two commits, in this order:**

1. **The signature (docs only):** `docs(adr-004): owner signs Q1 — cone depth [0.025, 0.975] at the unchanged ≥ 80% floor` — this file's Q1–Q4 cells filled and ticked, the actionplan card checked. No code, no recorded byte, no threshold moves in this commit.
2. **The implementation, in §9's order:** `feat(stage4,evals): ship the signed cone depth — re-record, reseal, re-bank` — one constant (two, if Q4 signs) → the disclosure set (Q2's `cone_levels` declared, NOT smuggled; Q2's four staged restatements and, if signed, Q4's ADR-011 amendment applied verbatim in the same change) → retrain → §9's blast radius → seal → seam re-cut.

**The P4 checklist** — every pre-signing box below was measured this session and
pinned by `tests/test_envelope_depth_sweep.py` (9 test methods); the
implementation chain stops the moment either stop-condition trips:

- ☐ **Q1–Q4 cells** filled and ticked above (Q4 is priced by §6/§6.5 and need not be signed to sign Q1 — the tail price is a pure function of depth, §6.4).
- ☐ **Pre-implementation green:** sweep exits 0 with P0 reproducing every stored claim ≤ 0.011 INR and the N=30 race equal to the committed 0.409726949… (1e-12); the seam runs 9/9; full suite green.
- ☐ **Constant-change discipline:** every frozen constant that moves (the levels; the atom floor, if Q4 signs) is stated from→to with its measured effect in the signature commit message and the applied restatements — this repo has **no `config/` change ledger** (grep- and `git log --all -- config/` verified), so the honest record is the commit message plus the decision text, never a bare literal swap.
- ☐ **Implementation order:** constant → disclosures → retrain (2 model files) → `record_stage4_evidence.py --apply` (31 rows) → regenerate 58 reports + prototype → republish `manifest.sha256` (720 entries), `validate_manifest.sha256 && validate_manifest.asymptotic` → `run_evals.py` + `--re-freeze-gates` → re-cut `test_envelope_depth_sweep` (today's P4 row becomes the new P0; the P0 == 55.172…% assertion is retired in the same commit) → full suite → `graft build`.
- ☐ **Stop conditions (measured, not assumed):** gate 4 must re-read **0.410** (the race reads pools and geometry, never cone levels — any move means the re-record drifted and the chain stops); gate 5 must read **24 → 82.8%** against the committed 16 → 55.172…%; the other **eight** gates re-read and are never tuned; the pre-commit scorecard passes on banked reds, not by weakened thresholds.
- ☐ **Post-state:** suite green, `graft check` in sync, actionplan card ticked with the signing date and both commit hashes.

### Signing Q2 — the staged disclosure text (four edits, verbatim; nothing below has been applied)

Every `find` string is grep-verified against today's bytes; commit 2 applies
all four in the same change as the constant (the decision-7/8 pattern —
definition text moves with the definition). All four are drafted for the
recommended P4: a P6 signature swaps the two end levels for **0.01 / 0.99
(nominal 98%)** in each block and changes nothing else.

**Edit 1 — `BRD.md` §4, the Expected Range row (line 137).** BRD §4 carries
no literal "80%" (grep-verified; the only `80%` in the BRD is an unrelated
sample-verdict cell), so the row's `[P10 … P90]` tokens are what imply the
~80% object — this is the line that states the signed depth.

- Find: `| **2. Expected Range** | GARCH(1,1) / Volatility Jump-Diffusion Cones + Empirical 30-Day Historical Volatility (HV) + ATR Multiples. | Quantified 22-trading-day price cone: $[P_{10}, P_{25}, P_{50}, P_{75}, P_{90}]$. Projected **Max Adverse Excursion (MAE)** and **Max Favorable Excursion (MFE)**. |`
- Replace with: `| **2. Expected Range** | GARCH(1,1) / Volatility Jump-Diffusion Cones + Empirical 30-Day Historical Volatility (HV) + ATR Multiples. | Quantified 22-trading-day price cone: $[P_{10}, P_{25}, P_{50}, P_{75}, P_{90}]$ — token names frozen as historical names (TICK-004 §2); at the signed P4 depth the end levels are **0.025 / 0.975 — a nominal 95% envelope**, disclosed machine-readably as `calibration.cone_levels` and graded at the unchanged ≥ 80% coverage floor (evals.md §3 #5). Projected **Max Adverse Excursion (MAE)** and **Max Favorable Excursion (MFE)**. |`

**Edit 2 — `docs/tickets/TICK-004-lightgbm-cone.md` §2, the canonical
output-contract row.**

- Find (double-backtick span — the row itself contains backticks): ``| `price_cone_p10_p50_p90` | dict \| null | Percentile price envelope of the 22-session **path** — the sessions' lows and highs, the realization evals dimension 5 grades: `p10`/`p25` from the excursion mixture of adverse excursions (worst low vs the entry), `p50` from terminal returns, `p75`/`p90` from favourable excursions (best high) |``
- Replace with: the same row, its last cell ending ``… from favourable excursions (best high); the five key **names** are frozen historical names (TICK-004 §2) — at the signed P4 depth `p10`/`p90` carry levels **0.025 / 0.975** (nominal 95%, disclosed as `calibration.cone_levels`), `p25`/`p50`/`p75` at 0.25 / 0.50 / 0.75 unchanged |``

**Edit 3 — `docs/agents/evals.md` §3 #5 (the Range Coverage row's metric
cell).** The threshold cell (`**Coverage $\ge 80.0\%$**`) does **not** move.

- Find: `remained within the calculated $[P_{10}, P_{90}]$ price envelope — **graded only over records whose forward tape lies inside the trained corpus window**`
- Replace with: ``remained within the calculated $[P_{10}, P_{90}]$ price envelope — at the signed P4 depth the bracket's end levels are **0.025 / 0.975** (a nominal 95% object: the `p10`/`p90` names are frozen historical names, `calibration.cone_levels` is the machine-readable disclosure, and the **≥ 80.0% floor printed in this row's threshold cell is unchanged**) — **graded only over records whose forward tape lies inside the trained corpus window**``

**Edit 4 — `docs/agents/domain.md` glossary (line 22, Price Cone) — the
fourth consumer, found while staging this text: the only place that still
states the percentile meanings literally.**

- Find: `- **Price Cone ([P10, P50, P90]):** Volatility-projected price distribution over 22 trading days (1 month). P10 represents the 10th percentile bearish boundary; P90 represents the 90th percentile bullish expansion.`
- Replace with: ``- **Price Cone ([P10, P50, P90]):** Volatility-projected price distribution over 22 trading days (1 month). The five tokens are frozen historical names (TICK-004 §2): at the shipped signed depth P4 the P10 token denotes the **2.5th percentile** of the adverse-excursion mixture (bearish boundary) and P90 the **97.5th percentile** of the favourable one (bullish expansion) — a nominal 95% envelope, disclosed as `calibration.cone_levels` and graded at the unchanged ≥ 80% coverage floor.``

**Deliberately not edited (each a grep-verified no-op at P4):** every ≥ 80%
*floor* statement — evals.md §3 #5's threshold cell, TICK-004 §2:28,
AGENTS.md:96, `skills/evals/SKILL.md:14` and `.agents/skills/evals/SKILL.md:14`
(the floor does not move); BRD §5.2's cone-definition bullet, BRD §9's
inventory key and TICK-004 §1 (token names, no levels); evals.md §2's measured
narrative (:52/:55/:63/:72/:138) and actionplan.md:307/:310 (dated
measurements of the P0 object — the retrain *appends* a new dated entry under
them, history never rewritten); the eight payload keys themselves (frozen by
TICK-004 §2 — `cone_levels` is a declared addition to the calibration, not a
payload change); and stage-12's report labels (`12_generate_html_report.py:58`
titles the band depth-neutrally — no string moves, and the 59 regenerated
artifacts follow the re-recorded bytes, §9).

### Signing Q3 — the staged tail-stress text (one edit, verbatim; nothing below has been applied)

Q3's split is a *contract* change, and gate 5's contract site is evals.md
§3 #5 — the same rule that put the graded-set scope there (ADR-011 decision
9). The floor number is priced by §2's ladder, so the block below is drafted
for the recommended pair **Q1 = P4 → tail floor 50.0%**: the ladder's P4
value *and the tail's measured reading*, so the new section passes exactly
and any tail regression trips it; any number ≤ 50.0% is also honest, the
ladder prices any other depth (P0 ≤ 33.3%, P6 ≤ 66.7%, P8 ≤ 83.3%, P9
100%), and the implementation chain stops if the committed tail reading is
not 3/6. One name collision to keep straight: the cell's existing "— signed
Q3" is the *2026-10-16* slice split (proposal-gate-thresholds); this
proposal's Q3 adds its own separately-floored line below.

- Find: `| **Coverage $\ge 80.0\%$** (over the in-window records; ungraded records named on the coverage line, and the representative/tail slice counts and scores printed beside the number — signed Q3)`
- Replace with: `| **Coverage $\ge 80.0\%$** (over the in-window records; ungraded records named on the coverage line, and the representative/tail slice counts and scores printed beside the number — signed Q3), **plus a separately-floored tail stress section** (signed Q3 of proposal-cone-envelope-depth.md): the tail slice's own floor **≥ 50.0%** at the signed depth, PASS/FAILed on the verdict line and banked beside the headline in `baseline_scorecard.json` — the floor must sit ≤ the §2 ladder's tail value at the signed depth (50.0% at P4, 66.7% at P6, 83.3% at P8, 100% only at P9)`

**Execution obligations (same commit as the constant; no new artifact
classes):**

1. `scripts/run_evals.py` gains the tail-stress line and its floor constant —
   the prose floor and the code floor move together (a prose-only floor is
   the unbacked claim evals.md §2 forbids); the line prints the slice's count
   and reading beside the headline (3 → 50.0% on today's corpus at P4).
2. `--re-freeze-gates` banks the new named gate beside the other ten (the
   gates block goes 10 → 11); at the drafted floor it banks a PASS at exactly
   the measured value — tight by design — and the baseline regression policy
   applies unchanged (a pass that stops passing blocks; a banked red that
   worsens blocks).
3. Seams: `test_run_evals.TestGradedSetSlices` gains the tail-stress
   assertions (the verdict line, the banked block, the slice count), and
   `test_envelope_depth_sweep`'s ladder test already pins the floor's ceiling
   per depth — this section's `find` string joins the staged-text pins
   (`TestTheStagedSignatureTextIsLive`) so it cannot go stale pre-signing.
4. Nothing else moves: §3 #5's metric cell (composition lives in the
   threshold cell), the 59 stage-12 reports (the scorecard is not rendered
   into them), the adjudication report, AGENTS.md:96 and both evals SKILL
   mirrors (headline-floor claims) — each a deliberate no-op, same as the Q2
   census above.

### Signing Q4 — the staged ADR-011 decision-13 amendment (verbatim; applied in commit 2, the same commit as the constant)

ADR-011 amends decision texts inline (decision 13 already carries its own
*Re-measured* note; decision 15 superseded figures in place). Two paste-ins
into `docs/agents/domain.md`, both in commit 2 — commit 1 only ticks Q4.
Written for the recommended pair (Q1 = P4, Q4 = N=20); a Q4-alone signature
stands too, since the headline-invariance evidence covers every depth and
obligation (ii) is depth-independent.

**(a)** In decision 13's body, the literal: find
``at least `MIN_SECTOR_ATOMS_PER_CLASS` (30) atoms`` → replace
``at least `MIN_SECTOR_ATOMS_PER_CLASS` (20) atoms``. The `^CNXBANK` parenthetical
re-checks at execution: §6.5's Option-D census measures N=20 unlocking
`^CNXIT`/`^CNXFMCG`, so if that block's weakest class holds ≥ 20 atoms the
example moves with the retrain's counts — else it stands.

**(b)** Append at the end of decision 13's text (before decision 14), verbatim:

> *Amended (Q4 signed, [signing date]) — **the floor is 20, not 30**:
> `MIN_SECTOR_ATOMS_PER_CLASS = 20`
> (proposal-cone-envelope-depth.md §6/§6.5, measured before signing). Nothing
> else in this decision moves: the rule is still "every class of every
> measure", a thin or missing block still defers to pooled with its stderr
> disclosure (never a waiver), the payload's eight frozen keys still do not
> grow, and the 1-atom floor stays refused — that is exactly the tail-guess
> the selector's docstring names. Measured on the frozen 29-row graded set at
> today's pools: the gate-5 headline is **invariant under the rule at the
> proposal depths** — P4 82.8% and P6 93.1% under every rule (same residual
> ids; P6 holds for N ≥ 5, and below the proposal depths the relaxed rules are
> not free either: P0/P2 dip under N ≤ 10) — the tail slice is fully
> rule-invariant at P4 (3/6, same three ids under all five rules), median
> width narrows 34.7% → 33.0% (P4) and 43.6% → 39.6% (P6), exactly three
> graded rows change pools (`GB-2022-0010`, `GB-2022-0011`, `GB-2022-0014` —
> each ✅ under both its own block and pooled), and the race Brier recomputes
> **0.409** against the committed **0.410** (still ~2.5× the ≤ 0.18 ceiling).
> Below 20 the price stops being disclosure-only — at N=10 / N=5
> `GB-2022-0002` flips out of P6 (93.1% → 89.7%) — which is why 20 is the
> offered relaxation and N ≤ 10 is rejected. Two obligations travel with the
> signed Q1 re-record chain (no new artifact classes): (i) the trainer's CV
> measures the shipped selector, so the retrain re-measures block share and
> containment — **71.88%** own-sector block share and **77.81%** bar-path
> containment under the 30-atom rule as of decision 16's retrain (decision 13's
> own 10,869-row reading stands unchanged as its measurement: 99.72%, 76.02%
> vs 77.10% pooled) — the new readings publishing in evals.md §2 and
> `provenance`, exactly how decision 15's 32.21% superseded 99.72% in place; (ii) gate 4's re-recorded
> predictions re-read the N=20 value the sweep measures (**0.409** at 3-dp,
> pinned exactly by re-cutting the sweep's race anchor under the signed
> constant) instead of holding 0.410 — `baseline_scorecard.json` re-freezes to
> it deliberately in the same change, and the sweep's rule-anchor asserts the
> shipped constant's own Brier. The literal's other governed consumers move in
> the same commit: evals.md:59 (`= 30` → `= 20`) and TICK-004:71 (`(30)` →
> `(20)`); actionplan.md:307 is append-only history and is not rewritten; the
> parameterized seams (`test_4_ml_direction.py:641/:660`,
> `test_train_direction_model.py:772/:779`) read the symbol and need review,
> not expected rewrites.*
>
> *[Execution slot — completed at the re-record, never before: corpus rows
> ___; own-sector block share ___% (vs 71.88% at 30); bar-path containment
> ___% vs pooled ___%; race Brier OOF ___; gates 4 ___ / 5 ___ (P4, N=20);
> suite ___; `^CNXBANK` deferral re-check: ___.]*

## 9. Implementation plan on approval (not started — spec first)

**Measured blast radius at Q1 = P4** (counts read from disk this session —
the plan below is ordered so each layer is sealed before the next):

- **31** recorded `out_4_ml.json` bytes re-recorded (`tests/proof/fixtures/*/recorded/`,
declaration `evidence: recorded`, gate 4's raced rows); their `cone_p10`/`cone_p90`claims re-promoted from the new bytes via `record_stage4_evidence.py` — 63
  `out_4_ml.json` references in the dataset today.
- **720** entries in `tests/proof/manifest.sha256` republished, then
  `validate_manifest.sha256` + `.asymptotic` green (the proof seal).
- **59** HTML artifacts regenerated — 58 `reports/report_*.html` +
  `reports/sample_report.html` — each byte-compared by the stage-12 seam.
- **2** model files rewritten by the retrain (`models/lgbm_direction_v1.txt`
  + `.calibration.json`): `provenance` restates CV containment at the new
  nominality, gains Q2's `cone_levels` (a key that does **not** exist in the
  calibration today — schema addition, declared not smuggled), and the
  `pool_conditioning` prose follows automatically.
- **10** baseline gates re-graded and deliberately re-frozen via
  `--re-freeze-gates`: gate 5's red **must** move 55.2% → 82.8%; gate 4's
  0.410 must **not** move (the race reads pools and geometry, never cone
  levels — a move there means the re-record drifted and the change stops);
  the other eight are re-read, not tuned.
- **4 governed restatements** (Q2: `BRD.md:137`'s Expected Range row, TICK-004 §2,
  evals.md §3 #5, domain.md's Price Cone glossary — all four staged verbatim in
  §8; a fifth only if Q3 signs — evals.md §3 #5's tail-stress clause, §8's
  Signing Q3, whose execution also banks an 11th gate) and the sweep itself: once the signed levels *are* `CONE_SPEC`, **the proposal's
  tables become history** — `test_envelope_depth_sweep` must then re-cut P0 to
  the shipped levels and treat today's P4 as the new baseline (its P0 ==
  55.172…% assertion is the last thing that holds the old world in place).

1. **One constant:** `CONE_SPEC`'s levels move (stage and trainer CV already
   share it — ADR-011 decision 2: one cone implementation, so nothing can drift
   between the shipped envelope and its validation).
2. **Disclose, don't rename:** write the signed levels into
   `calibration.cone_levels` (+ nominality), keep the eight payload keys
   frozen, and amend the consuming specs (BRD §4, TICK-004 §2, evals.md §3 #5)
   in the same change — the decision-7/8 pattern for definition corrections.
3. **Re-measure and re-bank deliberately:** re-run `train_direction_model.py`
   (CV containment restated at the new nominality), re-record the 31 stage-4
   rows, regenerate the 58 reports + prototype, re-freeze the baseline with
   `run_evals.py --re-freeze-gates` — thresholds and floor untouched, the red
   moves only because the object moved, and both numbers are published.
4. **Seams:** `test_4_ml_direction` (monotonicity and barrier containment at the
   new levels), `test_train_direction_model` (CV containment counts),
   `test_run_evals` (scope/slices unchanged — the gate itself does not change),
   and `test_envelope_depth_sweep` (below: this proposal's table against the
   committed reading).

## 10. Reproducing every number here

```
python tests/evals/envelope_depth_sweep.py
```

The tool rebuilds gate 5's exact graded set (calibration `row_window`, each
row's `$derive` tape span, the frozen class slices — 29 graded, the two October
tapes named ungraded), routes every row through **stage 4's own `compute`**
over its recorded canonical trade, ≤T_rec prefix and context bundle — the class
mixture is *captured* from stage 4's `predict`, never re-derived — verifies that
candidate **P0 reproduces each record's stored `cone_p10`/`cone_p90` to
≤ 0.0105 INR** (and aborts loudly if not, so a fidelity break cannot quietly
produce a proposal), then re-reads `samples_for`'s pools at the ten candidate
levels. It also re-runs the race over each rule's pools and aborts if the
N=30 Brier does not equal the committed 0.409726949… to 1e-12. Every table
above is its output verbatim (the *nominal* column and the shorthand "still
missing" notes are annotations added here on top of the tool's ids — counts
and ids are its output).
`tests/test_envelope_depth_sweep.py`
locks the P0 row to `baseline_scorecard.json`'s committed 55.172…%, the P4
row to 82.8%, the P4 invariance across all five rules (headline *and* tail
ids), the P6 N=10 regression (89.7% with `GB-2022-0002` in the miss set), the
gate-4 anchor row, the tail stress ladder's counts and held-out ids at every
depth, the per-rule tail price at P4/P6 (§6.5's B/B′ readings), the
`--pairwise` side-by-side's three N=20 switchers plus `GB-2025-0030`'s lone
verdict disagreement, and every staged `find` string under §8's Signing
Q2/Q3/Q4 pinned two-sided — exactly once in its live target file *and*
verbatim in this proposal's own text — so neither the staged text nor the
files it will edit can drift silently before signing; the proposal cannot
go stale under later corpus work either.

```
python tests/evals/envelope_depth_sweep.py --pairwise
```
appends the one-off side-by-side (each graded row's P4 cone under its own
sector block vs pooled, both envelopes' containment of the realisation, and
which pool rule N=20 would pick). It patches the frozen atom constant only
for that call, restores it before returning, is absent from the default run,
and touches none of the tables above.