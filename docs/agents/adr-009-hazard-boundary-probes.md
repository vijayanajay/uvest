# ADR-009 (PROPOSED): Hazard Boundary Probes — Pinned Evidence at the Breaker Thresholds
*Decision memo for owner approval. No frozen document is edited by this file; approval means the paste-ready edits at the bottom are applied together, in one command.*

**Status:** PROPOSED — do not implement until approved
**Date:** 2026-10-13
**Extends (does not amend):** ADR-008 (`docs/agents/domain.md`) — the level-sourcing, window-pinning and boundary-probe rules frozen there govern every row this memo proposes
**Honors unchanged:** `business_test_cases.md` (frozen & immutable; this memo asks the owner to command its §3/§5 amendment), ADR-007 (pending; interaction noted in §7)

---

## 1. The Question

Gate 2's rule-exercise census counts distinct tripped `(arm, value)` pairs (`tests/run_evals.py` `rule_exercises`), so the two stage-6 hazard classes are capped by the distinct hazard *values* their rows carry, not by their row counts: `pledge_hazard` stands at 5 (`promoter_pledge_pct` = 32.0, 34.7, 54.2, 62.5, 99.0) and `earnings_hazard` at 2 (`days_to_earnings` = 2, 3). Both classes are quota-frozen at 5 rows (`meta.class_counts_expected`; `check_class_distribution` fails the run on an over-quota class), and — unlike the geometry classes, whose shapes are functions of the pinned ≤T_rec tape — a pledge level and a session window are authored from the catalog, never re-derived. Catalog growth is therefore the only honest lever, and ADR-008 already froze *how*: levels pinned to real point-in-time disclosures, windows pinned to real announced results dates, and scope restricted to rows that probe each breaker **at its threshold**.

This memo proposes the concrete three rows and the complete change set their addition requires.

## 2. Options Considered

### Option A — Authored levels above the floor (the existing SCEN-007/014/017 rule)
Extend the seeder's disclosed-authored-input rule to new cells: state any value above 25% / any window under 7 sessions, disclosed as an authored input.

- **Rejected.** Forbidden by ADR-008 for *new* rows, and rightly so: it would raise the census with numbers nobody measured. The existing authored defaults (SCEN-014, SCEN-017's 2-session windows) are grandfathered as committed history, not a template.

### Option B — Full package: +3 pledge, +4 earnings (census 38→45)
The original pre-grill proposal: bracket both bands broadly (e.g. pledge 26–30 / 60–90, earnings 1/4/5/6).

- **Rejected in the grill.** Three of the four earnings windows and two of the three pledge levels would sit mid-band — cells that add census without probing a decision boundary. Seven new full historical scenarios (each with pinned tape, forward tape, recorded stage-6 bundle) is also the largest frozen-spec churn available. Scope was pinned by the owner to boundary probes only.

### Option C — Boundary probes only: 3 rows
**Chosen.** One *marginal pledge* row just over the kill switch (does the breaker fire at 26%?), and two *earnings windows* at the band's edges (1 session — knife-edge; 6 sessions — does it hold at the far boundary?). Each new cell is a threshold test the class cannot currently make.

## 3. Decision

Add exactly three hazard rows, each a full point-in-time scenario under ADR-008's evidence rules:

| New ID | Class | Probe | Census cell it adds | Required outcome |
| :--- | :--- | :--- | :--- | :--- |
| `SCEN-061` | `pledge_hazard` | marginal pledge just above the 25% kill switch (target band 25.1–28.0%) | `promoter_pledge_pct_above_25` at the pinned level | `LOSS` (hazard realized → TN) |
| `SCEN-062` | `earnings_hazard` | results **1 trading session** after T_rec (knife-edge) | `days_to_earnings_below_7_futures` at 1 | `LOSS` (hazard realized → TN) |
| `SCEN-063` | `earnings_hazard` | results **6 trading sessions** after T_rec (far edge of the <7 band) | `days_to_earnings_below_7_futures` at 6 | `LOSS` (hazard realized → TN) |

**Quota bumps:** `pledge_hazard` 5→6, `earnings_hazard` 5→7, `total_trades_expected` 50→53 (`meta.class_counts_expected` and the catalog's §5 roll-up, in the same change — `check_class_distribution` fails on rows-before-quota).

**Measured effect:** census `pledge_hazard` 5→6, `earnings_hazard` 2→4, total **38→41**; `baseline_scorecard.json` re-frozen at the new floor (V12). The three rows are `TN` outcomes, so the S4 false-negative accounting is untouched except its denominator (§6, step 7).

**Sourcing is step one and may refuse.** Each row's issuer, anchor date and hazard value must be pinned to a real artifact — an NSE shareholding-pattern disclosure (pledge % as of a date ≤ anchor) or an NSE results-date announcement (window = trading sessions from T_rec to the announced date) — and each row's pinned ≤T_rec tape plus forward tape must realize the documented `LOSS`. If no sourceable issuer lands a real marginal pledge, `SCEN-061` is dropped and disclosed rather than authored; the two earnings probes stand alone. Nothing here invents a level to complete a table (E4/E8).

## 4. Tradeoffs

| | Benefit | Cost / mitigation |
| :--- | :--- | :--- |
| Boundary scope (3 rows) | Every new census cell is a threshold test; smallest frozen-spec churn | Census rises 3, not 7. Mitigation: the remaining band is honest headroom for a later command, not a debt. |
| Pinned evidence (ADR-008) | New cells are disclosed facts, not authored numbers | Sourcing per row before authoring. Mitigation: sourcing is a refusal-capable first step; a blocked probe is disclosed, never forced. |
| Atomic quota bump | `check_class_distribution` and `test_seed_hazard_records.py` stay green | One large commit. Mitigation: every artifact it moves is enumerated in §6; the validator and pristine-suite runs gate it. |
| TN outcomes for all three | Zero movement in FN rate, Brier, and the breaker-accuracy arithmetic | The probes do not stress S4/S1 directly. Mitigation: they were never meant to; their job is the census's threshold coverage. |

## 5. Evidence Contract (per row — identical to every existing hazard row)

1. **A catalog §3 row** — anchor, symbol, instrument, trap text, `AVOID` verdict, `LOSS`/`TN` — read by the scaffolder's parser, never re-typed (E8).
2. **`tests/proof/fixtures/<ID>/financials.json`** — `governance.promoter_pledged_pct` (pledge) or `earnings.sessions_to_earnings` (earnings) carrying the pinned value; **statement blocks deliberately `{}`** (E4: absence, never an invented filing); a full `authoring` block naming the §3 trap line, the breaker and stage 6, `hazard_field`/`hazard_value`, the `documented` citation, the `rule`, and the human-readable `disclosure` naming the pinned source.
3. **Recorded stage-6 bytes** — `fixtures/<ID>/recorded/out_6_fundamental.json` from a real `scripts/6_fundamental_audit.py --record` run over the bundle (E1: graded bytes are a recording). The dataset row's `out_6_fundamental.json` is a `$ref` to it, never inline.
4. **The full pinned-tape footprint** — `ohlcv_prefix.json` (≤T_rec), `forward_tape.json` realizing `LOSS` under the frozen order-aware resolution, `current_trade.json` — because a hazard row is a full historical scenario, not a bare breaker probe.
5. **Re-freeze** — `manifest.sha256` regenerated, `baseline_scorecard.json` re-frozen at the new census floor (V12), `reports/` cleared and reproduced (a warm ledger proves stale bytes), manifest validator PASSED.

## 6. Migration Plan (each step independently verifiable)

1. **Sourcing proof (refusal-capable).** For each probe, pin the disclosure / results date and the issuer's ≤T_rec tape; print the sourced evidence. A probe with no source is reported and dropped.
2. **Catalog command.** Append `SCEN-061/062/063` to `business_test_cases.md` §3 (owner-commanded) and update the §5 roll-up denominators.
3. **Seeder roster.** Add the three entries to `tests/evals/seed_hazard_records.py` `ROSTER` with their pinned `documented`/`rule`/`disclosure` values.
4. **Author + record.** Run the seeder `--apply`: fixtures, `6_fundamental_audit.py --record`, dataset rows with `$ref` payloads.
5. **Quota bump.** `meta.class_counts_expected` 5→6 / 5→7 and `total_trades_expected` 50→53 in the same change as the rows.
6. **Note-regex seams.** `tests/evals/curate.py` and `tests/evals/seed_hazard_records.py` match the roster note as `^\d+ of 50 records`; generalize to `^\d+ of \d+ records` so the note survives the new total.
7. **Re-freeze + gates.** Hashes, baseline (floor 38→41), `reports/` reproduction, harness exit 0, full suite warm and pristine, validator PASSED. Re-express S4's frozen bound at the new roster (20% of 63 = 12.6 ⇒ `FN ≤ 12` unchanged; only the denominator text moves).
8. **Docs.** Append the ADR-009 block to `docs/agents/domain.md`; add the `EVAL-002` rows to `docs/tickets/INDEX.md` and `BRD.md` §9.1 (harness row, mirroring `EVAL-001`'s) in the same change.

## 7. Frozen-Document Impact (owner command; nothing here is edited silently)

| Document | Required change on approval |
| :--- | :--- |
| `business_test_cases.md` §3 | Three new rows (structure paste-ready below; sourced values filled by step 1). |
| `business_test_cases.md` §5 | Roll-up denominators gain the probes: pledge (6/6) → (7/7), earnings (5/5) → (7/7). |
| `business_test_cases.md` §6 (S4) | Bound re-expressed at 63 scenarios; threshold unchanged. |
| `tests/evals/golden_dataset.json` | `meta.class_counts_expected` pledge 5→6, earnings 5→7; `total_trades_expected` 50→53; three new records; roster note re-issued. |
| `tests/evals/curate.py`, `tests/evals/seed_hazard_records.py` | Roster-note regex generalized (`of 50` → `of \d+`); seeder `ROSTER` gains three entries. |
| `tests/proof/baseline_scorecard.json` + `manifest.sha256` | Re-frozen at the new floor and hashes (V12). |
| `BRD.md` §9.1 | New harness row for `EVAL-002` (paste-ready below). |
| `docs/tickets/INDEX.md` | New `EVAL-002` row (paste-ready below). |
| `docs/agents/domain.md` | Append the ADR-009 block below. |
| ADR-007 / the 60-scenario migration | **Open question 3** — sequencing only; this memo changes nothing ADR-007 owns. |

## 8. Open Questions for the Owner

1. Approve the three-row boundary scope (census 38→41), or add mid-band probes under the same pinned-evidence rule (which would amend ADR-008's boundary restriction)?
2. If the marginal-pledge probe cannot be sourced (no real disclosure in the 25.1–28.0% band with a pinnable tape), drop `SCEN-061` and proceed with the two earnings probes — or hold all three until a pledge source exists?
3. If ADR-007 (60-scenario migration) is approved first, should this change land before it (53-record dataset, then migrate) or be folded into the migration?

---

## Addendum — Sourcing Proof Outcome (2026-10-13)

Step 1 of §6 executed as a refusal-capable proof. Results recorded here so the decision rests on measured evidence, not intent.

### Earnings probes: both pinned and tape-verified

| Probe | Pinned announcement | Session count (real tape) | Frozen-rule resolution |
| :--- | :--- | :--- | :--- |
| `SCEN-062` TCS.NS | Results after market Thu 11 Jul 2024; anchor Wed 10 Jul 2024 | **1** ✓ (NSE calendar) | **LOSS** (`stop_first`) — entry 3983.70 (T_rec open), stop 3949.17 (0.5×ATR14 69.05), target 4052.76; stop pierced at the 11 Jul **open** (3931.00) |
| `SCEN-063` INFY.NS | Results after market Thu 18 Jul 2024; anchor Tue 9 Jul 2024 | **6** ✓ — 17 Jul absent (Moharram holiday), sessions 10/11/12/15/16; weekday counting would wrongly say 7 | **LOSS** (`stop_first`) — entry 1659.90, stop 1645.24 (ATR 29.33), target 1689.22; stop touched intraday 10 Jul |

Resolved with the repo's own machinery: `curate.atr14_simple_mean`, the catalog's crisis geometry policy (entry = T_rec open, stop = entry − 0.5×ATR(14), 2.0R target), and `dataset_refs.resolution_of` (first-touch-wins — the single implementation shared by the `$derive` rule, seed scan, scaffolder, and migration tool). Stable across the full forward window and the frozen 22-session window; the stop bar precedes the target bar in both pairs, so neither LOSS is a `both_hit_conservative_loss` artifact.

**Disclosure (stop-before-release):** in both pairs the stop prints *before* the announcement itself — TCS gapped through the stop at the 11 Jul open and that evening's release was received well (+6.63% next session, target printed 12 Jul); INFY stopped six sessions pre-results. Both are pre-release drift inside the hazard window, not realized earnings shocks. Under the tape-truth rule (SCEN-016 precedent) each remains an honest `LOSS`/`TN`: the row documents "advisory issued inside the binary window, tape resolved LOSS," and the census grades the stage-6 hazard *value*, not the loss's cause. A probe where the announcement itself breaks the tape would be a different sourcing target, not a spec violation of these. Tapes are live yfinance pulls (`auto_adjust=False`, raw OHLCV); fixture bytes get pinned at authoring, which is where any price-source divergence would surface.

### Marginal pledge probe: in-band unobtainable

~18 candidate issuers were checked against actual shareholding-pattern pages; **none** discloses a promoter pledge in the 25.1–28.0% band with a pinnable tape. Under §3's refusal clause, `SCEN-061` cannot be authored as designed. Closest real sourced value: **Indus Towers at 30.5%** (pattern as of 30 Jun 2023, Trendlyne shareholding page; yfinance price history verified) — a genuine disclosure nearish the kill switch, but outside the designed band and a hair under the class's existing minimum level (32.0), so it would not by itself probe closer to 25% than the census already does.

**Owner ruling (open question 2), on the evidence:** `SCEN-061` is drop-or-widen — (a) drop it and ship the two earnings probes alone (census 38→40, quotas 50→52, `earnings_hazard` 2→4), or (b) widen the band to admit Indus Towers 30.5% (a real pinned disclosure, three rows intact, census 38→41 as §3 states). Either way nothing is authored to complete a table.

### Side finding (recorded, not acted on)

The frozen catalog's own `SCEN-054` clue — Bajaj Finance "pledge > 25%" — reads **0.0%** in the readable disclosures, so that row's pledge claim is uncorroborated by its source. It grades nothing today (no mutation implied); flagged for the owner.

---

## Paste-Ready Edits (for `business_test_cases.md` §3, on approval — sourced fields filled by step 1)

```
| **SCEN-061** | ‹sourcing› | ‹sourcing› | ‹sourcing› | Marginal Pledge Kill-Switch Probe | Promoter pledge ‹pinned level%› (> 25% kill switch) | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-062** | ‹sourcing› | ‹sourcing› | ‹sourcing› | Knife-Edge Earnings Probe (1 Session) | Results announced 1 trading session after T_rec | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-063** | ‹sourcing› | ‹sourcing› | ‹sourcing› | Far-Edge Earnings Probe (6 Sessions) | Results announced 6 trading sessions after T_rec | **`🔴 AVOID`** | **`LOSS`** | **TN** |
```

A placeholder row (`‹sourcing›`) may never be committed: the scaffolder must refuse a row whose anchor/symbol are unreadable, and step 1 is the gate.

## Paste-Ready ADR Block (for `docs/agents/domain.md` on approval)

### ADR-009: Hazard Boundary Probes — Pinned Evidence at the Breaker Thresholds
- **Status:** APPROVED & FROZEN
- **Context:** The stage-6 hazard classes' rule-exercise census is capped by the distinct hazard values their rows carry (`pledge_hazard` 5, `earnings_hazard` 2), and neither value is a function of the pinned tape — catalog growth is the only honest lever (ADR-008). The pre-grill full package (+7 rows) was rejected in favor of rows that probe each breaker at its threshold.
- **Decision:** Add exactly three boundary-probe scenarios — one marginal pledge just over the 25% kill switch (`SCEN-061`), earnings at 1 session (`SCEN-062`) and 6 sessions (`SCEN-063`) — each with its hazard value pinned to a real point-in-time disclosure or announced results date per ADR-008, each a full pinned-tape scenario realizing its documented `LOSS`, each backed by recorded stage-6 bytes. Quotas move `pledge_hazard` 5→6, `earnings_hazard` 5→7, total 50→53 in the same change; the census floor re-freezes 38→41. A probe that cannot be sourced is dropped and disclosed — never authored to complete the table.

## Paste-Ready Ticket Rows (on approval)

`BRD.md` §9.1, next to the `EVAL-001` harness row:

```
| — | `tests/run_evals.py` (Evals harness — hazard census) | `tests/evals/golden_dataset.json` hazard rows | `tests/test_seed_hazard_records.py` | `EVAL-002` |
```

`docs/tickets/INDEX.md`:

```
| `EVAL-002` | [Hazard Boundary-Probe Rows (Pinned Evidence)](file:///d:/Code/uvest/docs/tickets/EVAL-002-hazard-boundary-probes.md) | FROZEN (sliced) | `tests/test_seed_hazard_records.py` | P1 |
```
