# Proposal — Stage-8 Limit-Band Policy: the Discount Clamp and the Anchor Side (gate 6)

**Status:** APPROVED — **signed 2026-10-16 by the owner's command** ("Sign
Q1-Q3 as recommendation B and implement the discount clamp end-to-end"). The
frozen decision is **B**: Q1 yes (the discount clamp), Q2 midpoint (the anchor
side stays as frozen), Q3 yes (gate 6 gains a fill-rate floor ≥ 60% and prints
the median net beside the mean). This page is now the spec-freeze source for
the amendment: TICK-008 §2's contract table (`zone_above_market`), stage 8's
`_zone`/payload contract, and `run_evals.gate_mae_reduction`'s criterion move
with it; every number below was reproducible from committed bytes before the
change (each record's recorded `out_8_dhan.json`, its pinned forward tape, and
`dataset_refs`'s frozen MAE rules — the harness reproduced the banked gate-6
reading of 71.4% / −0.8% exactly before scoring any alternative,
Zero-Hallucination eval).

---

## 1. Why this is on the desk

Gate 6's first graded reading (71.4% of filled limits beat market MAE, banked
red against ≥ 75%, net expectancy −0.8%) decomposes into three findings, two of
which are *rule* findings, not engine findings.

## 2. Finding 1 — 12 of 58 zone midpoints sit at or above the market entry

All 12 filled-but-worse records have `limit_entry_price ≥ entry_price`. The
reason is algebraic, and it is the whole story:

> **Lemma.** For a filled limit (`22d low ≤ limit < entry`), limit-MAE <
> market-MAE **⟺ limit < entry** — because `(limit−low)/limit < (entry−low)/entry`
> reduces to `low/limit > low/entry`, i.e. `limit < entry`.

So a limit at or above the market entry carries **zero discount**: it fills
instantly, its adverse excursion is measured from a *higher* anchor than the
market entry's, and it can only grade worse. Grading it as a limit execution is
a category error — the record is describing a market order. On this benchmark
those 12 rows are exactly the 12 worse fills, and they drag the net expectancy
from +10.0% (median) to −0.8% (mean).

## 3. Finding 2 — the gate's mean-of-ratios net line is outlier-dominated

The −0.8% "net expectancy" is a mean of per-record percentage reductions over
rows with `market MAE > 0`. A row with a near-zero market MAE contributes an
unbounded ratio: `SCEN-016` alone contributes **−530.5%** (market MAE 1.168%
vs limit MAE 7.365%), then `SCEN-013` −154.2% and `GB-2023-0021` −123.7%.
The median of the same 40 rows is **+10.0%**. The detail line should carry the
median beside the mean (a disclosure change in `run_evals.gate_mae_reduction`,
not a criterion change).

## 4. Finding 3 — the 16 unfilled limits are near-misses and crash gaps

The unfilled split cleanly (unfilled = the tape never traded at or below the
limit ⇒ no position ⇒ derived `null`, excluded from the denominator — correct,
but it is opportunity cost the current gate does not price):

* **4 near-misses within +1% of the limit**, two within 10 bp:
  `GB-2025-0005` +0.05%, `GB-2024-0028` +0.09%, `GB-2023-0023` +0.63%,
  `GB-2023-0041` +0.73%.
* **12 deep-gap rows** ≥ +1.15%, deepest `GB-2023-0040` (ADANIENT) at
  **+13.68%** below the limit.
* **9 of the 16 would fill at the band's high side**; **0 of 16** at the band's
  low side — a deeper limit never rescues an unfilled one.

Fill rate today: 42/58 = **72.4%**.

## 5. The policy table (all numbers gate-exact: filled pairs only, net filtered `market MAE > 0` as the runner filters)

| # | Policy | on-limit | fills | fill rate | unfilled | no-limit | better % | gate-net | median |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | --- | --- | --- |
| **P0** | **midpoint (current)** | 58 | 42 | 72.4% | 16 | 0 | **71.4%** | **−0.8%** | +10.0% |
| P1 | band high | 58 | 51 | 87.9% | 7 | 0 | 47.1% | −29.8% | +0.8% |
| P2 | band low | 58 | 34 | 58.6% | 24 | 0 | 82.4% | +26.8% | +22.3% |
| P3 | AVWAP-side (unclamped) | 58 | 39 | 67.2% | 19 | 0 | 64.1% | +18.1% | +20.8% |
| P4 | order-block-side (unclamped) | 58 | 46 | 79.3% | 12 | 0 | 58.7% | −26.7% | +5.8% |
| **P5** | **midpoint + discount clamp** | 46 | 30 | 65.2% | 16 | 12 | 100.0% | +31.1% | +24.7% |
| P6 | band high + clamp | 31 | 24 | 77.4% | 7 | 27 | 100.0% | +33.2% | +21.0% |
| P7 | band low + clamp | 52 | 28 | 53.8% | 24 | 6 | 100.0% | +36.9% | +30.7% |
| P8 | **AVWAP-side + clamp** | 44 | 25 | 56.8% | 19 | 14 | 100.0% | **+43.5%** | **+42.9%** |
| P9 | order-block-side + clamp | 39 | 27 | 69.2% | 12 | 19 | 100.0% | +27.6% | +12.3% |

*Discount clamp* = the limit claim exists only when the anchor sits **strictly
below** the record's market entry; at/above entry the record carries no limit
claim and is disclosed (`no-limit` column), never silently dropped. Coverage of
the anchor side: the midpoint sits at/above entry on **12/58** rows, the
AVWAP side on **14/58**, the order-block side on **19/58** — the midpoint keeps
the most records on-limit.

**The lemma's consequence, stated loudly:** under *any* clamped policy the
better-fraction among fills is **100% by construction** — P5–P9's perfect
scores are algebra, not skill. What stays falsifiable is the **fill rate** (how
often the zone is reached at all) and the discount depth (net/median). A
clamped gate whose criterion stayed "≥75% of fills beat market" would be
unfalsifiable wallpaper; the criterion has to move with the policy (Q3 below).

## 6. Options

| # | Option | Effect on gate 6 | Tradeoff |
| :--- | :--- | :--- | :--- |
| **A** | **Keep P0.** Banked red stands; nothing moves. | 71.4% (FAIL), −0.8%, forever — including the 12 category-error rows. | Honest but grades market orders as limit executions. |
| **B** | **Clamp only, keep the midpoint anchor** (recommended). | 100% better (tautological), 65.2% fill rate, +31.1% net — passes ≥ 75% and a proposed ≥ 60% fill floor. | 12 records carry no limit claim (disclosed); the better-fraction loses falsifiable content, so Q3's floor must ship with it. |
| **C** | **Clamp + AVWAP-side anchor** (the "AVWAP-side entry" variant). | 100% better, **+43.5% net / +42.9% median** (deepest discount) but only **56.8%** fill rate — misses a ≥ 60% floor. | Depth over fill rate: the AVWAP side is the shallower retest and is reached less often; 14 rows go no-limit. |
| **D** | **Clamp + band-low anchor.** | 100% better, +36.9% net, **53.8%** fill rate — the worst fill rate of the clamped set. | A deep limit is a limit that rarely trades; 0 of the 16 unfilled are rescued by going deeper. |

## 7. Recommendation (the runner's opinion, not its call)

**B now, with Q3's criterion amendment in the same freeze; C only if the owner
weights discount depth over fill rate.** B is the minimal correct change: it
keeps the frozen midpoint rule and adds the one condition (strict discount)
under which the gate's comparison means what its name says. C buys ~+12 points
of median discount at ~8 points of fill rate and 2 more no-limit rows. D is not
competitive on any axis a fill-rate floor would tolerate.

## 8. Decision points

| Question | Decision | Signed |
| :--- | :--- | :--- |
| **Q1** — adopt the discount clamp (no limit claim at/above entry)? | yes | ☑ 2026-10-16 |
| **Q2** — anchor side under the clamp: midpoint (B) / AVWAP-side (C) / band-low (D) | **midpoint (B)** | ☑ 2026-10-16 |
| **Q3** — criterion amendment with the freeze: add a **fill-rate floor ≥ 60%** (B: 65.2% passes, C: 56.8% and D: 53.8% fail) and print the **median** net beside the mean? | yes | ☑ 2026-10-16 |

## 9. Implementation plan on approval (not started — spec first, per the Iron Rule)

1. **Stage 8 owns the clamp** (`8_dhan_precision.py:_zone`): when the chosen
   anchor is `>=` the record's entry, emit `optimal_limit_buy: null` with a
   disclosed `zone_above_market: true` key — the engine never recommends a
   non-discount limit. TICK-008 §2's contract table gains the key (amendment).
2. `tests/evals/record_stage8_evidence.py` promotes `limit_entry_price: null`
   and no `$derive` for those rows (its zone-gated promote rule already
   behaves this way once the artifact reads null).
3. `run_evals.gate_mae_reduction` detail line gains the fill-rate disclosure
   and the median; Q3's floor becomes a graded criterion if signed.
4. Re-record stage-8 evidence (62 rows, hashes + `manifest.sha256`), then
   `--re-freeze-gates`: gate 6 flips red → green as a **deliberate improvement
   publication** (the baseline policy's ▲ path), not a quiet re-bank.
5. Seams: extend `tests/test_8_dhan_precision.py` (clamp both sides: anchor
   above entry ⇒ null + disclosure; anchor below ⇒ unchanged) and the runner
   seam for the fill-rate line.

## 10. Reproducing every number here

Per record: `limit = recorded out_8_dhan.json.optimal_limit_buy` (and
`anchored_vwap` / `orderblock_support` / `optimal_limit_range` for the other
policies), `low = min(bar.low)` over the record's pinned forward tape,
`entry = trade.entry_price`; `market MAE = (entry − low)/entry`,
`limit MAE = (limit − low)/limit` with `low > limit ⇒ unfilled`; better iff
`limit MAE < market MAE`; gate-net = mean of `100·(mkt − lim)/mkt` over filled
pairs with `mkt > 0` — `dataset_refs`'s frozen rules verbatim. The current
policy reproduces the committed bank (71.4% / −0.8%) exactly before any
alternative is scored.
