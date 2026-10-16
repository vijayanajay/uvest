# TICK-011: Daily Position Triage & Adverse Movement Tracker

**Status:** FROZEN  
**Priority:** P1  
**Test Seam:** `tests/test_10_daily_triage.py`  
**Target Script:** `scripts/10_daily_triage.py`  
**Output Artifact:** `out_10_triage.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 10)

### 1. Goal & Context

Stage 10 is the **Daily Position Management and Adverse Movement Protocol Engine** (BRD §11, §9.1 row 10).
Once a trade recommendation is logged to `uvest_history.db`, Stage 10 runs daily prior to market open or post-close to answer: *"What should the user do today?"*

Key operations:
1. **Four Operational Zones (BRD §11.1):**
   - *Profit Harvesting:* Price within 10% of target $\implies$ Trail stop to breakeven + 50% unrealized gains.
   - *Healthy Runway:* Price consolidating above entry $\implies$ Hold, maintain stop loss.
   - *Adverse Movement:* Price drifting towards stop $\implies$ Enforce 3-Stage Adverse Protocol.
   - *Time Stop / Stagnation:* Active $> 12$ days without progress $\implies$ Close futures before weekend carry cost.
2. **Three-Stage Adverse Movement Protocol (BRD §11.2):**
   - **Level 1 (Drawdown $\le 1.0 \times \text{ATR}$):** Normal pullback $\implies$ **DO NOTHING. DO NOT AVERAGE DOWN.**
   - **Level 2 (Drawdown $> 1.0 \times \text{ATR}$ or breaks 20 EMA):** Warning $\implies$ **CANCEL RE-ENTRY. TIGHTEN STOP TO PREV HIGH/LOW.**
   - **Level 3 (Stop Hit or Invalidation):** Hard liquidation $\implies$ **NEVER AVERAGE DOWN. EXIT IMMEDIATELY.**

### 2. Frozen Specification Reference

- `BRD.md` §11.1 (The Four Triage Zones).
- `BRD.md` §11.2 (The Three-Stage Adverse Movement Protocol).
- `BRD.md` §9 Script Inventory: `open_trades_status`, `action_required`, `trailing_stop_update`, `premise_invalidated`.
- **Database:** Local SQLite `uvest_history.db`.
- **Output:** `out_10_triage.json`.

**Canonical output contract** (mirrors `BRD.md` §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `open_trades_status` | list[dict] | Status of all open tracked recommendations |
| `action_required` | str | `HOLD` \| `TRAIL_STOP` \| `TIGHTEN_STOP` \| `LIQUIDATE` |
| `trailing_stop_update` | float \| null | New calculated trailing stop level |
| `premise_invalidated` | bool | `true` if fundamental or structural support broke |
| `adverse_level` | int \| null | 1, 2, or 3 if trade is in drawdown |

### 3. TDD Acceptance Criteria

- [x] Logs new trades into SQLite `uvest_history.db` and queries active positions.
- [ ] Classifies position into the 4 operational zones based on price progress.
- [ ] Level 1 drawdown ($\le 1.0\times\text{ATR}$) strictly prescribes `HOLD` with explicit prohibition against averaging down.
- [ ] Level 2 drawdown ($> 1.0\times\text{ATR}$ or close $< 20\text{ EMA}$) tightens stop and raises warning.
- [ ] Level 3 triggers instant liquidation when stop loss is breached.
- [x] Tested with local in-memory/temp SQLite database and recorded price fixtures.

### 4. Implemented slice (2026-10-14) — the trade store and the macro-premise half

The mode the daily loop needs first is in place; the price half stays open.

**Trade store (BRD §11).** `uvest_history.db` (`trade_id`, symbol, instrument,
entry/stop/target, lot size, entry prior, `tracked_on`, status) plus a
`triage_history` audit trail. `--action track` is the canonical chain invocation
(BRD §9.1 row 10): it freezes the day-1 thesis into `trades` **once** — a second
`track` for the same `trade_id` cannot rewrite the geometry the original verdict
was judged on — and then audits it against the regime evidence the same run
already produced (`--payload out_7_regime.json`, with the entry prior from
`out_9_decision.json`). `trade_id` reuses `_recorder.bundle_name`
(`{SYMBOL}_{YYYYMMDD}`), so TICK-012's `--resolve <TRADE_ID>` addresses the same
row the replay bundle was recorded for.

**Daily triage mode (the rerun).** `--action triage` — with `--action status` as a
documented alias, the wording the `daily-triage` skill prints — enumerates every
**open** tracked position and **reruns stage 7 against live market context**: one
bundle per position (the position's own symbol decides which sectoral index it
carries), fetched by the same `fetch_market_context.py` the runner uses. The rerun
is the real stage: `7_sector_macro_regime.py` is spawned through its frozen CLI
and its payload is transcribed verbatim into the row — no regime arithmetic is
re-implemented here. Each audit is appended to `triage_history` (one row per
position per day, replacing its own same-day row) and the aggregate published.

**Macro premise → action (documented, deterministic).** The day-1 premise BRD
§11.1 #1 names is "sector MRS leading", so the RRG quadrant *is* premise evidence:
`broken :=` panic breaker **or** VIX `PANIC` **or** sector `LAGGING` ⇒ `LIQUIDATE`
(§11.2 Level 3 invalidation; §10.3 #6 forbids long setups outright);
`weakened :=` breadth `RISK_OFF` **or** flow `NET_SELLING` **or** sector
`WEAKENING`/`IMPROVING` ⇒ `TIGHTEN_STOP` (§11.2 Level 2 premise warning); otherwise
`HOLD`. Every frozen RRG token is accounted for — `LEADING` is the premise holding,
the two rolling quadrants weaken it, `LAGGING` breaks it. A waived regime
contributes no premise change (BRD §10.4) and says so. `TRAIL_STOP` is profit
harvesting — the price half — so it is never emitted.

**Disclosed gaps.** `adverse_level` and `trailing_stop_update` are `null`, and
every row names the evidence they need in `pending_inputs`
(`position_price:drawdown_vs_entry`, `atr_14:drawdown_in_atr`,
`close_vs_ema20:premise_evidence`, `delivery_zscore:premise_evidence`), plus
`regime:waived` when the rerun produced none — the same disclosure idiom stage 7
uses for `missing_inputs` and stage 9 for `confidence_deficit`. The four-zone
classification, the deterministic `still_chance_pct` posterior and the action
levels above remain this ticket's open work.

**Payload.** Exactly the §2 contract keys. **Seam.**
`tests/test_10_daily_triage.py` (22 tests): the frozen thesis, the mapping, a
real per-position stage-7 rerun (asserted against the stage's own output), the
per-position waiver, the injected-fetcher live path, store discipline and one CLI
invocation with `--record` identity.
