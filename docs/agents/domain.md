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
