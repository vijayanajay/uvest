# TICK-007: Sector Relative Strength & Macro VIX Regime

**Status:** FROZEN (amended 2026-10-14 by owner command — contract table, frozen conventions and waiver granularity per ADR-010 in [domain.md](file:///d:/Code/uvest/docs/agents/domain.md))  
**Priority:** P1  
**Test Seam:** `tests/test_7_sector_macro_regime.py`  
**Target Script:** `scripts/7_sector_macro_regime.py`  
**Output Artifact:** `out_7_regime.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 7)

### 1. Goal & Context

Stage 7 is the **Sector Relative Strength and Macro Regime Classifier** (BRD §7, §9.1 row 7). A stock rarely swims against a collapsing sectoral or macroeconomic market tide.

This stage evaluates four macro/sector components:
1. **Sectoral Mansfield Relative Strength (MRS):** Rolling 50-day sector-to-NIFTY performance plus its 5-session momentum, mapping to an RRG quadrant (`LEADING`, `WEAKENING`, `LAGGING`, `IMPROVING`).
2. **NIFTY 50 Macro Tape & Breadth:** NIFTY 50 relative to 50/200 DMA (`nifty_trend`); NSE 500 market breadth (`market_breadth_pct`, `breadth_regime`).
3. **India VIX Volatility Regime:** Categorized into Low (<12), Normal (12–18), Elevated (18–24), and Panic (>24).
4. **Panic Volatility Regime Breaker (BRD §10.3 #6):** If India VIX $> 24$ and Market Breadth $< 30\%$ $\implies$ **🔴 AVOID** for all long setups.
5. **Institutional Macro Flows:** Rolling 5-day combined FII + DII net cash flow bias.

### 2. Frozen Specification Reference

- `BRD.md` §7.1 (Mansfield formula + RRG axes), §7.2 (`nifty_trend` / `breadth_regime` token vocabulary), §7.3, §7.4.
- `BRD.md` §9 Script Inventory row 7 (full payload list), §10.3 #6 (Panic Volatility Regime: VIX $> 24$ and Breadth $< 30\%$), §10.4 for the verdict it produces.
- `docs/agents/domain.md` — Mansfield Relative Strength formula and **ADR-010** (one fixture, one waiver; spec vocabulary wins; `uvest-market-context/1` evidence schema).
- **Input:** `current_trade.json` (requires `symbol`). **Output:** `out_7_regime.json` (stdout).
- **Evidence:** `--regime-fixture PATH` — a `uvest-market-context/1` bundle (ADR-010). Omitted, unreadable, malformed, too short or misaligned ⇒ waiver path.

**Canonical output contract** (mirrors `BRD.md` §9 inventory row 7):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `sector_index` | str \| null | Resolved NSE sectoral-index ticker from `SECTOR_INDEX_MAP` (e.g. `^CNXENERGY`); null when waived |
| `sector_mrs` | float \| null | Mansfield RS of parent sector index vs NIFTY 50 (BRD §7.1) |
| `sector_mrs_mom` | float \| null | 5-session change in `sector_mrs`, percentage points (the RRG momentum axis) |
| `sector_rrg_state` | str \| null | `LEADING` \| `WEAKENING` \| `LAGGING` \| `IMPROVING` |
| `nifty_trend` | str \| null | `ABOVE_50_200_DMA` \| `BELOW_50_ABOVE_200` \| `BELOW_200_DMA` |
| `market_breadth_pct`| float \| null | % of NSE 500 stocks trading above 200 DMA (recorded aggregate) |
| `breadth_regime` | str \| null | `RISK_ON` \| `NEUTRAL` \| `RISK_OFF` (BRD §7.2 bands) |
| `vix_value` | float \| null | India VIX current value |
| `vix_regime` | str \| null | `LOW` \| `NORMAL` \| `ELEVATED` \| `PANIC` |
| `fii_dii_flow_bias` | str \| null | `NET_BUYING` \| `NEUTRAL` \| `NET_SELLING` |
| `panic_regime_breaker`| bool \| null | `true` iff VIX $> 24.0$ **and** Breadth $< 30.0\%$ (both strict; forces 🔴 AVOID, BRD §10.3 #6) |
| `waived` | bool | `true` if index data is unavailable — then every metric above is `null` |
| `as_of` | str \| null | Last common session date (`YYYY-MM-DD`) |
| `missing_inputs` | list[str] | Each absent/short/malformed/misaligned input that caused a waiver; empty when not waived |

**Frozen computation conventions** (tests depend on exact values — the TICK-013 §2 pattern):

- **Evidence bundle:** `uvest-market-context/1` (ADR-010). Series keyed by ticker: `^NSEI` (NIFTY 50), `^INDIAVIX`, the trade's sector index, plus the aggregates `NSE500_BREADTH` and `FII_DII_NET`. Each series carries `unit` and `values: [{date, value}]`; the three index series must share one date axis (misalignment ⇒ waiver).
- **Symbol → sector index:** module constant `SECTOR_INDEX_MAP` (the stage-0 `OPTIONS_TYPES` precedent). A symbol absent from the map is **absent index data ⇒ waiver** — never a guessed index, never a partial payload.
- **Mansfield RS:** $r_t = \text{sector}_t / \text{nifty}_t$ over the common date axis; $\text{MRS}_t = (r_t / \mathrm{SMA}_{50}(r)_t - 1) \times 100$, SMA = arithmetic mean of the trailing 50 ratios.
- **RRG momentum:** $\text{sector\_mrs\_mom} = \text{MRS}_t - \text{MRS}_{t-5}$ (percentage points). Quadrant map (a zero counts as the positive side): level $\ge 0$ & momentum $\ge 0$ → `LEADING`; $\ge 0$ & $< 0$ → `WEAKENING`; $< 0$ & $< 0$ → `LAGGING`; $< 0$ & $\ge 0$ → `IMPROVING`.
- **`nifty_trend` precedence:** close $< 200$ SMA → `BELOW_200_DMA`; else close $< 50$ SMA → `BELOW_50_ABOVE_200`; else `ABOVE_50_200_DMA`. A close **equal** to an average counts as above it (BRD §7.2).
- **VIX bands:** `LOW` $v < 12$; `NORMAL` $12 \le v < 18$; `ELEVATED` $18 \le v \le 24$; `PANIC` $v > 24$. This resolves §7.3's shared 18 boundary to the *higher* band (the ticket freezes what the BRD leaves overlapping); 24 stays out of `PANIC` because §10.3 #6 is strictly $> 24$.
- **Breadth regimes:** `RISK_ON` $b > 65$; `NEUTRAL` $40 \le b \le 65$; `RISK_OFF` $b < 40$ (BRD §7.2).
- **FII/DII flow:** sum of the last 5 sessions' combined net (₹ Cr): $< -5000$ → `NET_SELLING`; $> +5000$ → `NET_BUYING`; else `NEUTRAL`. BRD §7.4 freezes only the selling arm; the $+5000$ mirror is this ticket's documented symmetric completion.
- **Minimums (all must hold, else waiver):** `^NSEI` ≥ 200 sessions (200 DMA), sector index ≥ 55 aligned sessions (50-SMA + 5-session momentum), `^INDIAVIX` ≥ 1, `NSE500_BREADTH` ≥ 1, `FII_DII_NET` ≥ 5.
- **Waiver granularity (ADR-010, decision):** **all-or-nothing at the payload level.** One `waived: true` for the whole stage; every metric `null`; `missing_inputs` names each cause; exit 0. BRD §10.4's waiver rule is payload-granular (stage 9 scores `out_7_regime.json` as one artifact), and a mid-value default for a single missing component is precisely the inflation §10.4 forbids. Per-component waiver arithmetic stays rejected until a stage-9 scoring seam is specced to honor it.
- **Determinism & offline:** identical inputs ⇒ byte-identical stdout; zero network calls (the live NIFTY-Indices/yfinance connector remains the separate open item under BRD §8).

**Deferred, disclosed:** BRD §7.1's "Stock vs. Sector RS" bullet has no canonical field — the stock's own trajectory vs its sector is not computed by this stage (stock-series features belong to stages 2/4). Recorded here so the gap is disclosed rather than silently dropped.

### 3. TDD Acceptance Criteria

- [x] Consumes canonical `current_trade.json` and resolves the parent NSE sectoral index from `SECTOR_INDEX_MAP`; an unmapped symbol waives with `missing_inputs`, never guesses an index.
- [x] Computes Mansfield RS over the 50-period SMA plus its 5-session momentum and maps the (level, momentum) pair to the RRG quadrant — all four quadrants and the zero boundaries.
- [x] Classifies `nifty_trend` with the frozen precedence, `vix_regime` at every band boundary (12 / 18 / 24), `breadth_regime` at 40 / 65, and `fii_dii_flow_bias` at ±5000.
- [x] Flags `panic_regime_breaker: true` only when VIX $> 24.0$ **and** Breadth $< 30.0$ — strict on both arms: 24.0 or 30.0 does not trip.
- [x] Waiver path: missing, malformed, too-short or misaligned fixture (or unmapped symbol) yields `waived: true`, all metrics `null`, `missing_inputs` populated, exit code 0.
- [x] Operates offline with zero network calls against a recorded `--regime-fixture`; a non-waived payload carries exactly the contract keys above, no more, no fewer.
- [x] `--record <dir>` writes `out_7_regime.json` byte-identical to stdout (shared `_recorder.py` bundle support).
