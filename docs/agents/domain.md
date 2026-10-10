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
