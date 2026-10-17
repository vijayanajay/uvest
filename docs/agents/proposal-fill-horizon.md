# Proposal — Gate 6's Fill Horizon: Advisory Validity vs the 22-Session Resting Order

**Status:** PROPOSED — awaiting the owner's decision (ADR-004: thresholds and
horizons are the owner's frozen call). Nothing below moves a committed number;
every figure is reproducible from committed bytes (each record's recorded
`out_8_dhan.json` midpoint that survived the discount clamp, its pinned forward
tape, and the frozen MAE arithmetic of `dataset_refs` — the same harness that
reproduced the committed gate-6 reading of 30/46 = 65.2% and +31.1% mean
before scoring any alternative horizon).

---

## 1. The question

Gate 6 (evals.md §3 #6) grades the engine's limit entry over the record's
**22-session** forward tape. But every benchmark row is an *intraday advisory*
(`authoring.entry_basis: open` — the catalog issues these as same-day
recommendations), and a limit an advisory places is realistically valid for
days, not a month. A limit that first trades in session 15 filled an advisory
nobody is still tracking. The horizon is therefore not a neutral
implementation detail: it is the difference between "the engine's zone is
reached by its own advisory" and "the engine's zone is eventually touched by a
market that moved on".

## 2. The evidence (measured over the 46 discount-clamped-on records)

Sessions-to-first-touch on each record's own pinned tape, then the two gate-6
readings under each candidate validity window. Per-advisory expectancy =
mean over **all 46 claimed records** of `100·(market_mae − limit_mae)/market_mae`
for fills inside the window, and **0** for records whose limit never fills
inside it — because an unfilled limit means you entered at market anyway, so
the policy added nothing that advisory.

| Validity window | Fills | Fill rate | Better fraction | Per-advisory expectancy |
| :--- | --- | --- | --- | --- |
| **1 session** | 14 | 30.4% | 100% | **+9.81%** |
| **3 sessions** | 21 | 45.7% | 100% | **+13.10%** |
| **5 sessions** | 21 | 45.7% | 100% | **+13.10%** |
| **10 sessions** | 26 | 56.5% | 100% | **+16.55%** |
| **22 sessions (current, frozen)** | 30 | 65.2% | 100% | **+20.31%** |

*No fill arrives between sessions 3 and 5* — the touch distribution's natural
breakpoints are 1, 3, 10 and 22; 16 of 46 never touch at all.

**The finding this table forces:** the fill-rate floor frozen in signed
proposal B (≥ 60%) passes **only** because the 22-session window is granted.
At any advisory-realistic window the fill rate is 30.4–56.5% — the floor's
5.2pp of headroom was horizon, not quality. The better-fraction column is
100% everywhere by the clamp's lemma, so it carries no information about the
horizon at all.

## 3. The comparison-window asymmetry (must be settled with the horizon)

The current rule pairs a limit MAE measured at the touch with a market MAE
measured over the **full 22 sessions**. Inside a shorter validity window that
pairs a 3-day limit against a 22-day baseline — the market side gets four
extra weeks to print a deeper low. Whichever window is frozen, it must apply
to **both sides** (`market_mae_pct` recomputed over the same first-*h*-session
window) or the asymmetry must be disclosed on the scorecard; the clean
variant is the symmetric one, and it is what the on-approval plan below
assumes.

## 4. Options

| # | Option | Gate 6 reads (today's evidence) | Tradeoff |
| :--- | :--- | :--- | :--- |
| **A** | **Keep the 22-session window** — interpret every limit as a resting good-till-cancelled order. | Unchanged: PASS 100%, 65.2% fill, +20.31% per-advisory. | Defensible only if the product genuinely places GTC limits; it credits fills no advisory's author would still be waiting for, and the floor stays horizon-inflated. |
| **B** | **Freeze a 3-session advisory validity** + per-advisory expectancy ≥ **+10%** as the headline (recommended). | Fill rate 45.7%, expectancy **+13.10%** → PASS with honest headroom; the floor and better-fraction become disclosures. | Gate 6 stops crediting month-late fills; the derive rule and the gate's pairing change (below), and the bank is re-frozen. |
| **C** | **Freeze a 5-session validity.** | Identical to B on this benchmark (no fills in sessions 4–5) — the extra two days are worth nothing today. | Free today, but it buys headroom the evidence does not currently demonstrate; a later record could exploit it. |
| **D** | **Freeze a 1-session validity** (same-day advisory only). | Fill rate 30.4%, expectancy **+9.81%** → red at a +10% floor. | Honest but harsh: a limit the next morning's open gaps through is arguably the advisory's success, not its failure. |

## 5. Recommendation (the runner's opinion, not its call)

**B.** Three sessions is the shortest window that survives the obvious
objection (an overnight gap through the zone still counts as the plan
working), and +13.10% per-advisory at a ≥ +10% floor is a number that can go
red for real reasons — which is the point of a gate. Under B the criterion
becomes one falsifiable metric that already subsumes the better-fraction
(always-100 under the clamp) and the fill rate (a miss is a literal zero in
the mean); the two old components stay printed as disclosures.

## 6. Decision points

| Question | Decision | Signed |
| :--- | :--- | :--- |
| **Q1** — validity window for a limit fill: 1 / **3** / 5 / 22 (GTC) sessions | ___ | ☐ |
| **Q2** — headline metric: per-advisory expectancy ≥ ___ % (proposed **+10%**; readings +9.81 / +13.10 / +13.10 / +16.55 / +20.31 across the windows), better-fraction + fill rate demoted to disclosures | ___ | ☐ |
| **Q3** — symmetric window: `market_mae_pct` recomputed over the same first-*h* sessions for the comparison (the clean variant), or the asymmetry disclosed on the scorecard | ___ | ☐ |

## 7. Implementation plan on approval (not started — spec first)

1. **The derive rule carries the horizon** (`dataset_refs._rule_limit_mae_pct`
   and, symmetrically, `_rule_market_mae_pct`): a first-touch-within-*h*
   condition and a windowed market MAE, frozen as the signed horizon — the
   tape stays the single source, so the window cannot be typed.
2. `run_evals.gate_mae_reduction` re-heads on per-advisory expectancy with the
   signed threshold; fill rate and better-fraction become coverage lines.
3. Re-materialize the dataset (artifacts untouched — only the tape rules
   move), re-run, and re-bank deliberately (`--re-freeze-gates`) as a policy
   change, exactly as proposal B was banked.
4. Seams: the window rule both sides (touch inside/outside the window, the
   symmetric market MAE), the gate's new headline on synthetic records, and
   the disclosure lines.

## 8. Reproducing every number here

Per claimed record: `limit = recorded out_8_dhan.json.optimal_limit_buy`
(post-clamp), first touch = first forward-tape bar with `low <= limit`
(1-indexed), `market_mae = (entry − min low)/entry`, `limit_mae = (limit −
min low)/limit`; a record fills inside window *h* iff its first touch ≤ *h*;
per-advisory expectancy = mean of `100·(mkt − lim)/mkt` over in-window fills
and `0` over everything else. The 22-session row reproduces the committed
gate-6 evidence (30 fills, 65.2%, +20.31%) exactly.
