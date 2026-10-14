# Domain Glossary & Architecture Decisions (ADR)
*Configured via /setup-matt-pocock-skills for UVEST*

## 1. Domain Glossary (Indian Market & Quant Terminology)

### Asset Classes & Instruments
- **Equity (Cash / CNC / MIS):** Shares of NSE/BSE listed companies traded for immediate delivery (T+1 rolling settlement) or intraday leverage.
- **Futures (Stock Futures / FUT):** Standardized, leveraged derivative contracts settled on the last Thursday of the expiry month. Governed by lot sizes (e.g., RELIANCE 250 shares) and exchange margin requirements (~20–25% SPAN + Exposure).
- **Options (CE / PE):** **STRICTLY PROHIBITED IN UVEST.** Non-linear derivatives with theta decay and unbounded tail risk. Excluded by architectural decree.

### Indian Market Microstructure & Mechanics
- **Bhavcopy:** Daily official trade summary published by the National Stock Exchange (NSE) post-market, containing symbol-level open, high, low, close, volume, delivery quantity, and turnover.
- **Delivery Volume %:** Percentage of total daily traded volume that resulted in actual transfer of shares between demat accounts rather than intraday day-trading. $\text{Delivery \%} = \frac{\text{Delivery Quantity}}{\text{Traded Quantity}} \times 100$. High delivery ($>50\%$) on rising price indicates institutional accumulation.
- **Open Interest (OI):** Total number of outstanding, active futures contracts not yet settled or closed. Rising OI + Rising Price = Long Buildup; Falling OI + Rising Price = Short Covering; Rising OI + Falling Price = Short Buildup.
- **MWPL (Market Wide Position Limit) & F&O Ban:** Total open interest across all brokers cannot exceed 95% of a stock's free-float limit set by SEBI. Stocks reaching $>95\%$ enter the F&O Ban Period where no new futures positions are permitted.
- **ASM / GSM (Surveillance Measures):** Additional / Graded Surveillance Measures imposed by SEBI on stocks exhibiting abnormal volatility, promoter debt, or price manipulation.
- **Futures Basis:** The price difference between the active Futures contract and the Underlying Cash Spot price ($\text{Basis} = P_{\text{FUT}} - P_{\text{Spot}}$). Indicates carry cost / financing demand.

### Quantitative Analytics
- **Dynamic Time Warping (DTW):** Algorithm measuring geometric similarity between two temporal sequences of differing speeds. Uses Sakoe-Chiba constraint band on Z-score normalized closing prices to identify historical multi-year analogs.
- **Purged Group Time-Series Cross-Validation:** ML cross-validation scheme where training and testing sets are strictly time-ordered, groups of overlapping forward labels are purged, and a 5-day embargo period prevents autocorrelation leakage.
- **Price Cone ([P10, P50, P90]):** Volatility-projected price distribution over 22 trading days (1 month). P10 represents the 10th percentile bearish boundary; P90 represents the 90th percentile bullish expansion.
- **Max Adverse Excursion (MAE):** The maximum unrealized loss (drawdown) experienced by a trade prior to hitting target or expiry.
- **Max Favorable Excursion (MFE):** The maximum unrealized profit achieved by a trade during its lifespan.
- **Anchored VWAP (AVWAP):** Volume-Weighted Average Price anchored to a specific pivotal event (e.g., the swing low preceding a recommendation), serving as institutional benchmark support.

### Fundamental & Governance Metrics
- **Piotroski F-Score:** A 9-point composite score evaluating financial quality across profitability, leverage/liquidity, and operating efficiency.
- **Altman Z''-Score:** Insolvency prediction model adapted for emerging market manufacturing and service corporations.
- **Promoter Pledging:** Percentage of promoter equity pledged as collateral to NBFCs/banks to raise debt. High pledging ($>15\%$) poses severe liquidation margin call risks; $>25\%$ is a hard system kill switch.
- **CFO-to-PAT Ratio:** Operating Cash Flow divided by Net Profit After Tax. Tests whether reported paper earnings are supported by real cash collections.

### Benchmark Curation & Hazard Evidence
- **Level Sourcing (Pledge):** The rule that a `pledge_hazard` row's `promoter_pledge_pct` must be pinned to a point-in-time NSE shareholding-pattern disclosure (the value actually disclosed as of `T_rec`), never an authored number chosen to fill a census cell. Governs how any new pledge row evidences its breaker value.
- **Window Pinning (Earnings):** The rule that an `earnings_hazard` row's `sessions_to_earnings` must be the trading-session count from `T_rec` to the issuer's actual announced results date, never an authored default. Two existing rows (SCEN-014, SCEN-017) carry the class's authored 2-session default rather than a documented calendar fact.
- **Boundary Probe:** A hazard row whose breaker measurement sits near the decision boundary (a marginal ≈26% pledge; earnings at 1 or 6 sessions) so the rule-exercise census tests whether the breaker fires *at* its threshold, not only comfortably past it. Contrast with a mid-band row, which adds a census cell without probing the boundary.

---

## 2. Architectural Decision Records (ADRs)

### ADR-001: Strict Exclusion of Options
- **Status:** APPROVED & FROZEN
- **Context:** Univest occasionally offers options tips. Option buying suffers from severe time decay (Theta) and IV collapse; option selling carries asymmetric tail risk.
- **Decision:** Reject any option contract at ingestion. Only Equities and Futures are permitted.
- **Enforcement (2026-10-13):** "Only Equities and Futures are permitted" is enforced as a **closed domain**, not as a blacklist. Stage 0 accepts exactly `CASH` and `FUT` and refuses anything else with `ERR_INSTRUMENT_UNRECOGNIZED` (exit 2, no canonical payload), because a value the gate cannot classify is a value downstream stages *will* classify — as cash. This is what stops an option written with look-alike letters (`Type: СЕ`, Cyrillic) from entering the pipeline as an "unknown instrument": it is not the ASCII token `CE`, so the token gate cannot name it, and before the closed domain it was scored as an equity. `FUTURES` is deliberately not an alias: stages 1/5/9 key off `FUT` alone, so accepting the long form would hand them a futures trade they read as cash. The rejection is reported with its own code, never as `ERR_OPTIONS_PROHIBITED` — a paste that is not a classified option must not inflate the options rejection rate.

### ADR-002: Local-First Zero-Cloud Architecture
- **Status:** APPROVED & FROZEN
- **Context:** Cloud microservices, hosted databases, and external SaaS introduce operational failure modes, token leaks, and latency.
- **Decision:** Python 3.11+ scripts, local SQLite databases (`uvest_history.db`, `dhan_cache.db`), flat JSON standard input/output contracts.

### ADR-003: Tiered Data Hierarchy (Free First, Dhan Fallback)
- **Status:** APPROVED & FROZEN
- **Context:** Commercial API rate limits and paid subscriptions should be minimized.
- **Decision:** Free web scrapers (Bhavcopy, Screener.in, yfinance) are the primary source. Dhan API is queried strictly for 5m/10m candles and real-time order depth when public data lacks resolution.

### ADR-004: Spec & Evals Frozen Before TDD
- **Status:** APPROVED & FROZEN
- **Context:** Developing quant features without frozen mathematical seams causes regressions and "vibe coding."
- **Decision:** Complete specification in `BRD.md`, tickets in `docs/tickets/`, and behavioral evals in `docs/agents/evals.md` must be frozen before implementation starts.

### ADR-005: Retention of Hard Safety Circuit Breakers (Rejecting Soft Pledge Pricing)
- **Status:** APPROVED & FROZEN
- **Context:** An alternative proposal in `docs/understanding.md` suggested softening circuit breakers (specifically Promoter Pledging > 25%) by attempting to "price the risk" into a continuous expected value calculation rather than hard-rejecting the trade.
- **Decision:** REJECT the softening proposal. In Indian equities and futures, promoter pledge crises trigger cascading NBFC/bank margin call liquidations and lower circuits where bid liquidity completely evaporates. A retail trader physically cannot exit at the theoretical stop loss in cash, while in futures, broker RMS executes fire-sale liquidations. Hard circuit breakers (Promoter Pledge > 25%, Inverted R:R < 1.0, Options, F&O Ban, Futures Earnings < 7d) remain absolute vetoes (`AVOID`).

### ADR-006: 7-State Verdict Schema & Daily Stewardship Contract
- **Status:** APPROVED & FROZEN
- **Context:** The original tri-state verdict (`GO`, `CONDITIONAL_GO`, `NO_GO`) was too coarse, and the system lacked lifecycle ownership of active trades after entry.
- **Decision:** Adopt the 7-state lifecycle vocabulary: Pre-entry (`GO`, `GO_WEAK`, `WAIT`, `AVOID`) and Post-entry (`HOLD`, `PRESSURE`, `EXIT`). Every non-GO verdict carries a mandatory `what_would_flip_me`. Confirmed trades enter daily stewardship where `still_chance_pct` is computed deterministically from premise evidence and published in a canonical daily report card.

### ADR-008: Hazard-Census Growth via Pinned Boundary Probes, Not Authored Levels
- **Status:** APPROVED (sourcing rules) — catalog growth remains a separate owner command; `business_test_cases.md` is untouched by this decision.
- **Context:** Gate 2's rule-exercise census counts distinct tripped `(arm, value)` pairs, so `pledge_hazard` (5) and `earnings_hazard` (2) are capped by the distinct hazard values the frozen catalog documents — a ceiling no number of extra rows can raise. A proposal to raise these by adding rows with authored levels (a disclosed value above the 25% floor, or the class's default session window) was grilled, which falsified two of its assumptions: (1) every seeded hazard row is a full point-in-time scenario carrying a pinned `ohlcv_prefix.json`/`forward_tape.json` whose forward motion must corroborate the catalog outcome — not a stage-6-only breaker probe; (2) two of the five earnings windows (SCEN-014, SCEN-017) are already authored defaults, not documented calendar facts, so authoring more windows would deepen the very weakness the census measures. Separately, `pledge_hazard`'s five levels ({32.0, 34.7, 54.2, 62.5, 99.0}) all sit comfortably over the 25% kill switch — none probes the boundary.
- **Decision:** Any future hazard-row growth must (a) pin each new `promoter_pledge_pct` to a real point-in-time NSE shareholding-pattern disclosure and each new `sessions_to_earnings` to the issuer's actual announced results date — never an authored number or an authored default window; (b) scope to boundary probes that test each breaker at its threshold (a marginal ≈26% pledge, earnings at 1 and 6 sessions), so the census rises as a byproduct of decision-boundary coverage rather than as the goal; and (c) carry the full pinned-tape footprint every existing hazard row already carries. A census rise is not, by itself, sufficient justification: mid-band rows that only inflate the count are refused, and absent an owner command to grow the catalog, the honest position is that pledge 5 / earnings 2 remain the complete, correct measurements of the frozen catalog as written.

### ADR-009: Hazard Boundary Probes — Pinned Evidence at the Breaker Thresholds
- **Status:** APPROVED & FROZEN
- **Context:** The stage-6 hazard classes' rule-exercise census is capped by the distinct hazard values their rows carry (`pledge_hazard` 5, `earnings_hazard` 2), and neither value is a function of the pinned tape — catalog growth is the only honest lever (ADR-008). The pre-grill full package (+7 rows) was rejected in favor of rows that probe each breaker at its threshold; the refusal-capable sourcing proof then found no shareholding-pattern disclosure in the 25.1–28.0% band, so the marginal-pledge probe could not be sourced as designed.
- **Decision:** Ship exactly two boundary-probe scenarios — earnings at 1 trading session (`SCEN-062`) and 6 sessions (`SCEN-063`) — each with its hazard value pinned to the issuer's actual announced NSE results date per ADR-008, each a full pinned-tape scenario realizing its documented `LOSS` under the frozen first-touch resolution, each backed by recorded stage-6 bytes. `SCEN-061` (marginal pledge) is dropped and disclosed under the memo's refusal rule — never authored to complete a table; widening the band to a sourced 30.5% disclosure was considered and declined as an amendment to ADR-008 made only to raise a count. Quotas move `earnings_hazard` 5→7 and total 50→52 in the same change; the census floor re-freezes 38→40 (V12). Sourcing evidence: `docs/agents/adr-009-hazard-boundary-probes.md`, addendum 2026-10-13.

### ADR-010: Stage-7 Regime Evidence — One Fixture, One Waiver, Spec Vocabulary
- **Status:** APPROVED & FROZEN (owner command, 2026-10-14)
- **Context:** Stage 7 (TICK-007) was frozen but unimplemented, and three contract seams were ambiguous or in conflict *before* code existed: (1) TICK-007 froze `nifty_trend` as `ABOVE_50_200_DMA` / `BELOW_50_ABOVE_200` / `BELOW_200_DMA` while shipped stage-9 code scored `BULLISH` / `NEUTRAL` / `BEARISH` — tokens no producer can emit, so every real run would silently take the 0.2 default; (2) TICK-007 asked for a 4-quadrant RRG from the MRS *level* alone, which has no second axis; (3) one `waived` flag covered five independent datasets with no rule for a partial gap, and stage 9's `score_regime` defaults a missing component to a mid value — data absence beating a genuinely weak reading is exactly the inflation BRD §10.4 forbids. The evidence source itself (recorded index series) had no schema, while stage 4's pending 20D NIFTY beta and sectoral-correlation features need the same series.
- **Decision:**
  1. **Spec outranks shipped code (ADR-004):** the `nifty_trend` vocabulary is TICK-007's, now pinned in BRD §7.2 as the single source; stage 9's `score_regime` and its test fixture adopt the frozen tokens with the same numeric calibration (0.3 / 0.2 / 0.0). When frozen spec and shipped code disagree, the code moves.
  2. **RRG gets its missing axis:** `sector_mrs_mom` = $\text{MRS}_t - \text{MRS}_{t-5}$ (5-session change, percentage points) joins the contract (BRD §7.1, §9 inventory row 7); the quadrant map and its zero boundary are frozen in TICK-007.
  3. **One fixture, one waiver:** the stage runs entirely from a single `--regime-fixture` bundle; any absent, short, malformed or misaligned input — including a symbol absent from the sector map — waives the **whole** payload (`waived: true`, every metric `null`, `missing_inputs` naming each cause, exit 0). BRD §10.4's waiver rule is payload-granular: stage 9 scores `out_7_regime.json` as one artifact, so component-level waiver arithmetic would require a stage-9 scoring seam that does not exist, and a mid-value default for one missing component is the inflation §10.4 forbids. Partial (per-component) waiver was considered and explicitly rejected until such a seam is specced.
  4. **Evidence schema `uvest-market-context/1`:** one JSON bundle whose series are keyed by Yahoo-style tickers (`^NSEI`, `^INDIAVIX`, `^CNXENERGY`, …) plus the `NSE500_BREADTH` and `FII_DII_NET` aggregates, each carrying `unit` and `values: [{date, value}]`, with a mandatory `provenance` block disclosing `origin: recorded | authored`. Stage 7 is its first consumer; stage 4 reads the same bundle instead of growing a second fetcher and cache — the no-lookahead truncation at T_rec then travels with one bundle, not three. The bundle can be authored (seeded, disclosed) or recorded live: `scripts/fetch_market_context.py` is the one live fetcher (tier-1 sources; a failed source omits its series and stage 7 waives naming it, never an invented value).
  5. **Sector map lives in code:** symbol → sectoral-index mapping is the module constant `SECTOR_INDEX_MAP` in `scripts/7_sector_macro_regime.py` (the stage-0 `OPTIONS_TYPES` precedent: domain data a test pins, no new file, no I/O). An unmapped symbol waives; it never guesses an index. Extract to shared data only when a second consumer exists (stage 4).
  6. **Macro signals size, they do not veto** (second owner command, 2026-10-14): stage 9 consumes `breadth_regime: RISK_OFF` (BRD §7.2 “all long setups gated or reduced”) and `fii_dii_flow_bias: NET_SELLING` (BRD §7.4 “cautious sizing”) through the existing BRD §6.4 size-cap lever — either signal caps a would-be `GO` at `GO_WEAK` (50% size) with `confidence_deficit` naming each source; both together cap at the same 50% and never compound. A waived or absent `out_7_regime.json` caps nothing (the waiver rule). The fatal breaker stays exactly BRD §10.3 #6 (VIX $> 24$ **and** breadth $< 30$): a Risk-Off breadth print alone is *priced*, never a veto, so the frozen six-breaker registry (ADR-005) is untouched. §7.3's Elevated-VIX 50%-sizing guidance is deliberately left unwired — extending the same cap lever to `vix_regime: ELEVATED` is a separate owner decision.
