# 🛡️ BUSINESS_TEST_CASES.md — The Frozen Business Proof Suite & Scenario Catalog
*Architect & Engineering Persona: Kailash Nadh Mindset (CTO, Zerodha)*  
*Status: FROZEN & IMMUTABLE — NEVER TO BE MODIFIED UNLESS EXPLICITLY COMMANDED BY REPOSITORY OWNER*

---

## 1. Executive Mandate & Immutability Decree

This document defines the permanent, authoritative **Business Test Suite** for UVEST. 
It establishes the empirical ground truth against which **every single update, model adjustment, weight modification, or code change** in this repository must be evaluated.

### The Iron Rule of Regression Testing:
1. **Never Weaken, Never Delete:** No scenario in this catalog may be removed, softened, or modified to make a failing script pass.
2. **Expectancy Monotonicity:** Any change to `scripts/` must demonstrate that the system **successfully gets better** — measured by an increase in Net Edge Multiplier ($\Delta E$) and zero degradation in safety gates. If an update lowers net expectancy or trips a safety circuit breaker, the update is rejected.
3. **Zero Hallucination:** Every metric, probability, and outcome must trace directly to local executed JSON payloads derived from real point-in-time NSE tape.

---

## 2. The Un-Gameable Point-in-Time Architecture

To eliminate lookahead bias, survivorship bias, and curve-fitting, the suite runs on a physically sandboxed architecture:

```
tests/proof/
├── fixtures/                     # Immutable point-in-time daily bars & bhavcopy
├── manifest.sha256               # Cryptographic SHA-256 hashes of all ≤T fixtures
├── scenarios/                    # The 62 isolated historical trade cases
│   ├── SCEN-001_.../
│   │   ├── content/              # The ONLY directory the engine can see (tape ≤ T_rec)
│   │   │   ├── current_trade.json
│   │   │   └── fixtures_truncated.json
│   │   └── outcome/              # Forward 22 bars (Read ONLY after verdict process exits)
│   │       └── forward_tape.json
└── baseline_scorecard.json       # Frozen reference metrics for regression testing
```

- **Filesystem Isolation:** The pipeline stages ($0 \to 9$) execute inside `content/`. The forward tape in `outcome/` is physically inaccessible until the decision engine has exited and written its verdict.
- **Fixture SHA Integrity:** If any fixture is altered or tampered with, the suite fails immediately with code `ERR_FIXTURE_INTEGRITY`.
- **Zero Network, Local-First:** All 62 scenarios execute headlessly in $<5$ seconds via:
  ```bash
  python tests/run_business_proof.py
  ```

---

## 3. The 62-Scenario Historical Catalog (2018–2026 Real NSE Tape)

The suite spans 62 real historical situations across 9 distinct Indian market stress regimes. Prices, volumes, delivery percentages, and corporate actions are taken from real historical NSE Bhavcopy and exchange filings.

### Regimes & Scenarios Matrix

| Scenario ID | Anchor Date ($T_{\text{rec}}$) | Symbol | Instrument | Scenario Name & Stress Regime | Market Microstructure Trap / Catalyst | Target Engine Verdict | Realized Outcome | Target Quadrant |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **SCEN-001** | 2020-03-23 | RELIANCE | FUT | Covid Crash Day 1 (Panic VIX) | India VIX 83.6; market hits 10% lower circuit | **`🔴 AVOID`** | **`WIN`** | **FN** |
| **SCEN-002** | 2020-03-24 | SBIN | CASH | Covid Panic Liquidity Freeze | VIX > 80; NIFTY 500 breadth < 5% bullish | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-003** | 2024-06-04 | SBIN | FUT | Election Results Flash Crash | Exit-poll euphoria followed by 6% NIFTY crash; VIX 32 | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-004** | 2024-06-04 | LT | FUT | PSU Euphoria Reversal | Capital goods sector drops 12% intraday on election results | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-005** | 2022-02-24 | TATASTEEL | FUT | Russia-Ukraine War Outbreak | Commodity price shock; global equity gap-down through stops | **`🔴 AVOID`** | **`WIN`** | **FN** |
| **SCEN-006** | 2022-05-04 | HDFCBANK | FUT | RBI Surprise 40 bps Rate Hike | Unscheduled off-cycle policy rate hike; Bank NIFTY crack | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-007** | 2023-01-25 | ADANIENT | FUT | Adani-Hindenburg Governance Shock | Promoter pledge > 25%, offshore debt; multi-day lower circuits | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-008** | 2023-01-27 | ADANIPORTS | FUT | Adani Contagion Liquidity Freeze | Cascading lender margin call liquidations; bid liquidity dries | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-009** | 2019-01-25 | ZEEL | CASH | Zee Entertainment Collateral Dump | Promoters pledged > 60%; lenders sold collateral; -33% in 1 day | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-010** | 2019-09-20 | YESBANK | CASH | NPA Crisis & Capital Inadequacy | Severe Altman Z distress; promoter holding dilution | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-011** | 2019-10-01 | IBULHSGFIN | FUT | NBFC Liquidity Contagion | Rating downgrades, promoter pledging 32%, cash flow deficit | **`🔴 AVOID`** | **`WIN`** | **FN** |
| **SCEN-012** | 2023-03-15 | VEDL | FUT | Vedanta Debt & Parent Pledge Stress | Parent debt refinancing concerns; promoter pledged 99% of stake | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-013** | 2023-04-17 | INFY | FUT | Q4 Guidance Shock Gap-Down | Results in 2 days; guidance cut caused immediate -10% gap-down | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-014** | 2022-07-11 | TCS | FUT | Margin Compression Pre-Earnings | IT rally into earnings; margin compression triggered instant drop | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-015** | 2024-01-17 | HDFCBANK | FUT | Merger Margin Squeeze Results | Q3 results in 2 days; NIM compression caused -8.5% single-day fall | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-016** | 2023-07-28 | TECHM | FUT | Q1 Operating Margin Collapse | Earnings shock inside 3 days; dividend payout couldn't stem drop | **`🔴 AVOID`** | **`WIN`** | **FN** |
| **SCEN-017** | 2024-05-02 | COFORGE | FUT | Large Acquisition Dilution Shock | Unscheduled board meeting for acquisition funding; -10% selloff | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-018** | 2021-10-18 | IRCTC | CASH | Retail Euphoria Blow-off Top | Stock split frenzy; delivery collapsed to 14%; institutional exit | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-019** | 2021-10-19 | IEX | CASH | Retail Trap on Thin Delivery | Rising price on negative delivery Z-score (-1.8σ); retail bull trap | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-020** | 2024-01-15 | BHEL | FUT | PSU Retail Churn at Resistance | Futures OI unwinding while retail turnover surges; FII cash net sell | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-021** | 2024-02-08 | NHPC | CASH | Parabolic Blow-off Exhaustion | Delivery dropped from 48% to 19%; momentum exhaustion | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-022** | 2022-04-18 | WIPRO | FUT | Sector Bear Market Bleed | IT sector Mansfield RS -4.2; stock attempting bounce off 200 EMA | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-023** | 2022-04-20 | TECHM | CASH | Lagging Sector Drag | NIFTY 50 flat, but NIFTY IT in structural distribution quadrant | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-024** | 2023-11-15 | HDFCBANK | FUT | Bank NIFTY Rotation Breakout | NIFTY makes new ATH; private bank leader absorbs supply and rallies | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-025** | 2021-08-10 | SUNPHARMA | CASH | Defensive Sector Outflow Miss | System rejected due to cyclical rotation; stock steadily ground up | **`🔴 AVOID`** | **`WIN`** | **FN** |
| **SCEN-026** | 2023-11-20 | TITAN | FUT | Optimal Limit Zone Execution | Chasing market price rejected; optimal limit filled at AVWAP support | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-027** | 2024-02-12 | MARUTI | CASH | Optimal Limit Retest Execution | Overbought market entry rejected; optimal limit filled on 20 EMA retest | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-028** | 2022-05-18 | MARUTI | FUT | Volatility Noise Stop Trap | Stoploss ₹40 vs Daily ATR ₹125 (0.32× ATR); noise stopout | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-029** | 2023-09-08 | TATASTEEL | FUT | Noise Trap on Metal Volatility | Recommended SL distance 0.55× ATR; metal index ATR expanded | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-030** | 2023-08-28 | IBULHSGFIN | FUT | MWPL Ban Limit Squeeze | MWPL at 93.8%; entered ban next session; liquidity paralyzed | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-031** | 2023-09-25 | PNB | FUT | Negative Basis Rollover Liquidation | Futures trading at -₹1.20 discount to spot during expiry week | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-032** | 2021-04-15 | TATASTEEL | FUT | Pristine Commodities Supercycle | Delivery 62% (+2.4σ), OI buildup +14%, RS leading, R:R 1:2.4 | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-033** | 2020-11-10 | ICICIBANK | FUT | Post-Covid Banking Trend Resumption | Anchored VWAP hold, FII cash net +₹3,200 Cr, clean fundamentals | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-034** | 2023-05-15 | RELIANCE | FUT | Congestion Box Breakout | 20-day high cross on delivery surge +2.1σ, NIFTY above 50/200 DMA | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-035** | 2023-11-01 | LT | FUT | Capital Goods Order Inflow Surge | Piotroski 8/9, Altman Z Safe, high delivery accumulation | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-036** | 2024-01-10 | BHARTIARTL | CASH | Telecom ARPU Expansion Trend | Institutional accumulation steady, MRS leading, pristine R:R 1:2.3 | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-037** | 2023-06-12 | ITC | CASH | FMCG Defensive Accumulation | Delivery % 58%, low beta, steady trend above 20 EMA | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-038** | 2024-03-15 | TCS | CASH | IT Selective Large-Cap Absorption | Clean fundamentals, low debt, delivery accumulation off 50 EMA | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-039** | 2023-08-18 | SUNPHARMA | CASH | US FDA Clearance Rally | Pharma sector RS leading NIFTY; clean corporate governance | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-040** | 2023-10-06 | TITAN | FUT | Festive Season Demand Buildup | Strong delivery % surge, basis carry +0.45%, sector leading | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-041** | 2023-12-04 | SBIN | FUT | PSU Bank Re-Rating Wave | Low valuation, delivery surge, clean F&O rollover > 78% | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-042** | 2023-09-15 | SUNPHARMA | CASH | Marginal Edge Calibration Band | All indicators neutral-to-positive; DTW analog P(win) = 58.0% | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-043** | 2023-10-16 | CIPLA | CASH | Marginal Edge Case (56% Win Rate) | Delivery slightly above avg; sector neutral; R:R 1:2.0 | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-044** | 2024-02-20 | WIPRO | FUT | Marginal Edge with Waived Intraday | Daily OHLCV pristine; Dhan API waived; Score 68% (50% size) | **`🟡 GO_WEAK`** | **`WIN`** | **TP** |
| **SCEN-045** | 2023-07-10 | HCLTECH | CASH | Marginal Score Edge Case | Score 66/100; p_win 56.5%; entered at 50% position size | **`🟡 GO_WEAK`** | **`WIN`** | **TP** |
| **SCEN-046** | 2022-09-15 | INFY | FUT | False Positive Trap: Trend Failure | Score 71%; entered GO; US Fed rate shock cracked IT next week | **`🟢 GO`** | **`LOSS`** | **FP** |
| **SCEN-047** | 2023-02-15 | TATASTEEL | FUT | False Positive: China Demand Slowdown | High delivery, clean R:R; sudden China export duties tanked tape | **`🟢 GO`** | **`LOSS`** | **FP** |
| **SCEN-048** | 2023-08-24 | MARUTI | FUT | False Positive: Auto Sales Disappointment | Setup pristine; monthly sales missed estimates by 12% | **`🟢 GO`** | **`LOSS`** | **FP** |
| **SCEN-049** | 2024-04-18 | HDFCBANK | FUT | False Positive: Macro FII Outflow Shock | Private bank accumulation; aggressive Middle East escalation shock | **`🟢 GO`** | **`LOSS`** | **FP** |
| **SCEN-050** | 2021-11-25 | RELIANCE | FUT | False Negative: AVOID but Rallied (Breakout) | Avoided due to resistance; stock exploded on Aramco deal news | **`🔴 AVOID`** | **`WIN`** | **FN** |
| **SCEN-051** | 2023-06-20 | ITC | CASH | False Negative: Sluggish Delivery Miss | Avoided due to low delivery volume; stock steadily ground up | **`🔴 AVOID`** | **`WIN`** | **FN** |
| **SCEN-052** | 2024-01-22 | SBIN | FUT | False Negative: Over-Strict Macro Buffer | Avoided due to elevated VIX (16.8); stock ignored macro and surged | **`🔴 AVOID`** | **`WIN`** | **FN** |
| **SCEN-053** | 2023-03-24 | TITAN | CASH | False Negative: Limit Unfilled Miss | WAIT issued for limit pullback; stock rallied without filling | **`⏳ WAIT`** | **`WIN`** | **FN** |
| **SCEN-054** | 2023-11-08 | BAJFINANCE | FUT | False Negative: Toxic Winner (Pledge) | Avoided on pledge > 25%; stock rallied +6% before crash | **`🔴 AVOID`** | **`WIN`** | **FN** |
| **SCEN-055** | 2022-06-15 | TATASTEEL | CASH | False Negative: Toxic Winner (Panic VIX) | Avoided on VIX 26 panic; commodity short-squeeze rally | **`🔴 AVOID`** | **`WIN`** | **FN** |
| **SCEN-056** | 2023-05-22 | INFY | FUT | Stewardship Success: Trailed Profit Harvest | GO_WEAK at entry; Day 2 soft-exit trail locked in +0.8R gains | **`🟡 GO_WEAK`** | **`WIN`** | **TP** |
| **SCEN-057** | 2023-08-14 | RELIANCE | FUT | Stewardship Success: Trailing Stop Harvest | GO trade reached +1.8R; trailed stop booked +1.2R before reversal | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-058** | 2024-02-28 | SBIN | FUT | Stewardship Success: Shakeout Absorption | GO trade; Day 3 shakeout held above AVWAP; held to target (+4.5%) | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-059** | 2023-12-18 | HDFCBANK | FUT | Stewardship Noise Immunity: Normal Pullback | Drawdown 0.7× ATR held within normal noise buffer; reached target | **`🟢 GO`** | **`WIN`** | **TP** |
| **SCEN-060** | 2024-03-20 | LT | CASH | Overfitted Analog Trap (Too-Good-To-Be-True) | DTW analog matched 98% with 2017 rally, but macro regime inverted | **`🔴 AVOID`** | **`LOSS`** | **TN** |> **Re-freeze R1 (owner-commanded, 2026-10-10):** SCEN-001 and SCEN-005 move `LOSS`/`TN` → `WIN`/`FN`. Their recorded point-in-time tape (frozen resolution: outcome bars strictly after `T_rec`) reaches the `2R` target without ever touching even the tightest documented advisory stop (`0.32×ATR`): both advisories rejected realized winners — the honest false-negative accounting of §4.1, never a geometry tuned to preserve the prior claim. Evidence and row provenance: `docs/agents/scenario-manifest-schema.md` §10.
| **SCEN-062** | 2024-07-10 | TCS | FUT | Knife-Edge Earnings Probe (1 Session) | Results announced 1 trading session after T_rec; pre-release drift pierced the crisis stop at that session's open | **`🔴 AVOID`** | **`LOSS`** | **TN** |
| **SCEN-063** | 2024-07-09 | INFY | FUT | Far-Edge Earnings Probe (6 Sessions) | Results announced 6 trading sessions after T_rec (17 Jul Moharram holiday); pre-release drift struck the stop first | **`🔴 AVOID`** | **`LOSS`** | **TN** |

> **ADR-009 boundary probes (owner-commanded, 2026-10-13):** `SCEN-062` and `SCEN-063` are added as the earnings boundary probes — windows of 1 and 6 trading sessions, each pinned to the issuer's announced NSE results date per ADR-008, each a full pinned-tape scenario whose forward tape realizes the documented `LOSS` under the frozen first-touch resolution. `SCEN-061` (the marginal-pledge probe) is **dropped and disclosed**: no shareholding-pattern disclosure in the 25.1–28.0% band was sourceable, so no authored level replaces it (refusal rule of ADR-009 §3; sourcing proof in `docs/agents/adr-009-hazard-boundary-probes.md`, addendum 2026-10-13). Evidence and row provenance: `docs/agents/scenario-manifest-schema.md` §10.

> **Re-freeze R2 (reconciliation, 2026-10-11):** SCEN-011 moves `LOSS`/`TN` → `WIN`/`FN`, and `SCEN-009`'s basis label is corrected to `stop_first` (its `LOSS` never moved). The pinned forward tape prints the target `259.14` on 2019-10-03 — the first bar after `T_rec` — and the stop `208.71` only on 2019-10-07, so the frozen order-aware resolution (`dataset_refs.resolution_of`, first touch wins) is `WIN`/`target_first`; the seeded `LOSS` came from the retired window-level scan (any stop touch inside the window ⇒ `LOSS`). One rule now decides everywhere: the curator's seed scan, the catalog scaffolder, the migration tool and the `$derive` rule all delegate to `resolution_of`, so this row is the honest false negative of §4.1 — an AVOID that rejected a realized winner, never a tuned loss. Evidence and row provenance: `docs/agents/scenario-manifest-schema.md` §10.

> **Re-freeze R3 (owner-commanded, 2026-10-12):** SCEN-016 moves `LOSS`/`TN` → `WIN`/`FN`. Its recorded point-in-time tape reaches the `2R` target `1136.4` on 2023-08-01 (high `1154.5`), the first session after `T_rec` that clears it, and never touches the advisory stop: the window's lowest low is `1087.15` (2023-07-31), which sits above even the tightest documented advisory stop (`0.32×ATR` = `1088.35`). The row was authored by the catalog scaffolder only after this command — it had stayed unauthored because the pre-command `LOSS` was unreachable under the frozen crisis geometry (`entry` = `T_rec` open `1100.0`, stop `1081.8` = `0.5×ATR`), and a geometry tuned to manufacture the loss is exactly what E8 forbids. The honest reading is the §4.1 false negative: an earnings-window AVOID that rejected a realized winner. Evidence and row provenance: `docs/agents/scenario-manifest-schema.md` §10.

---

## 4. Handling Asymmetric Edge Cases: The Frozen Protocol

### 4.1 Scenario A: Initial `AVOID`, but the Trade Rallied in Reality
*(False Negatives & The Psychology of Regret)*

When the system rejects a trade that goes on to make money, the engine decomposes the outcome into one of two immutable classes:

#### Case A.1: The Toxic Winner (Bad Process, Lucky Outcome)
* **Definition:** A recommendation with lethal structural hazards (e.g., Promoter Pledging $> 25\%$, F&O Ban, binary earnings) that rallied due to unexpected news or speculative squeeze.
* **The Physics Reality:** Surviving Russian roulette does not make pulling the trigger a good decision. In Indian equities, buying high-pledge stocks exposes capital to un-exitable zero-bid lower circuits.
* **Evaluation Assertion:** The system **MUST NOT** apologize or adjust weights for avoiding a toxic winner. In the mechanical confusion matrix, these scenarios are strictly accounted as **`FN`** (Realized Winner rejected by `AVOID`) with secondary classification `regret_status: ZERO_REGRET_FATAL_HAZARD_AVOIDED`. Under Gate S4, they are segregated from technical false negatives so they do not trigger model penalty recalibration.
* **Report Field:** `regret_status: ZERO_REGRET_FATAL_HAZARD_AVOIDED`.

#### Case A.2: The Over-Strict Miss (Genuine False Negative)
* **Definition:** A fundamentally sound trade that was rejected because market price had slipped slightly past the entry band, or delivery was marginally below threshold, and the stock rallied without pulling back.
* **The Protocol:**
  1. **The Flip Condition Re-Entry:** At T_rec, the engine issued `what_would_flip_me: Breakout above ₹X on volume > 1.5× 20D mean`. If intraday momentum confirmed, the user was provided an explicit mechanical rule to enter on strength.
  2. **Gate S4 Accounting:** Every missed winner is recorded as **`FN`**. The suite calculates **Forgone Expectancy** ($\sum R_{\text{missed}}$). If the FN rate exceeds $20.0\%$ across the suite, technical penalties are recalibrated.

---

### 4.2 Scenario B: System Issued a Weak Yes (`GO_WEAK`), but Price Drifted Adverse
*(Post-Entry Stewardship & Capital Defense)*

When a marginal or data-waived recommendation begins to fail, the system executes an automated 4-stage adverse movement protocol:

```
DAY 0: ENTRY                      DAY 1: NORMAL DRIFT              DAY 2: PREMISE BREAK             DAY 3: RESOLUTION
[GO_WEAK @ ₹2,980] ─────────────► [Drift to ₹2,955] ─────────────► [Downturn to ₹2,935] ──────────► [Hard Stop / Soft Exit]
 • 50% Sizing (1 lot)              • Drawdown < 1.0 ATR             • Drawdown > 1.0 ATR             • Exit logged
 • Baseline P(win) = 56%           • Delivery still intact          • Bhavcopy: FII dumping          • Failure Autopsy runs
 • 3 Premises Frozen               • Verdict: 🔵 HOLD               • Verdict: 🟠 PRESSURE           • Root-cause classified
                                   • Action: DO NOTHING             • Action: ⚡ SOFT EXIT            (REGIME_SHOCK)
```

#### Step 1: Day 0 Entry — Sizing Defense (Halved Risk)
* `GO_WEAK` setups (waived data or $55\% \le P_{\text{win}} \le 60\%$) enforce **50% Position Sizing**. Risk is capped to half the maximum allowable capital per trade before the tape even moves.

#### Step 2: Day 1 Post-Entry — Volatility Noise Immunity (`HOLD`)
* If drawdown $\le 1.0 \times \text{Daily ATR}$ and core premises hold:
  * Verdict: **`🔵 HOLD`**.
  * Action: `DO NOTHING`. Maintain original stop loss. Let the mathematical edge work without emotional panic.

#### Step 3: Day 2 Post-Entry — Premise Degradation (`PRESSURE`)
* If drawdown $> 1.0 \times \text{Daily ATR}$ OR core premises break (e.g., close $< 20\text{EMA}$, Bhavcopy OI unwinds):
  * Deterministic update: `still_chance_pct` drops below $55\%$ or decays $> 15\text{pp}$.
  * Auto-promoted to **`🟠 PRESSURE`**.
  * Emits `recommended_action`: **`SOFT_EXIT`** at `soft_exit_zone` or **`TIGHTEN`** stop to previous session's low.
  * **Capital Saved:** Saves $40\%–60\%$ of initial risk capital compared to taking a full hard stopout.

#### Step 4: Day 3 Resolution — The Golden Rule & Failure Autopsy
* **THE GOLDEN RULE:** **NEVER AVERAGE DOWN A LOSING POSITION.** Any attempt to add size to an adverse trade is flagged as a fatal violation.
* Upon exit, `scripts/11_postmortem_learning.py` runs automatically:
  * Decomposes failure into 5 root causes (`VOLATILITY_NOISE`, `REGIME_SHOCK`, `INSTITUTIONAL_DISTRIBUTION`, `EARNINGS_SURPRISE`, `ENTRY_SLIPPAGE`).
  * Updates cumulative Brier calibration score and adjusts weights in `thresholds.json`.

---

## 5. The North Star Scorecard & Improvement Delta

Every suite execution calculates this definitive business scorecard:

```text
================================================================================
                    UVEST BUSINESS PROOF SCORECARD (FROZEN v1.0)
================================================================================
  Evaluated: 62 Historical Replays across 2018–2026 NSE Tape
  Blindness Integrity (S6):   [PASS] 100.0% (Zero Lookahead / Fixture SHA Verified)

  CONFUSION MATRIX:
  ├── True Positives  (TP) : 21  (Realized Winners stamped GO / GO_WEAK)
  ├── False Positives (FP) :  4  (Realized Losers stamped GO — Critical Error)
  ├── True Negatives  (TN) : 26  (Realized Hazards/Traps blocked by AVOID)
  └── False Negatives (FN) : 11  (Realized Winners rejected — Cost of Strictness)

  THE THREE BUSINESS METRICS:
  1. Directional Calibration (Brier Score) :  0.138  (Threshold <= 0.180)  [PASS]
  2. Advisory Baseline Expectancy (Blind)  : +0.14R / trade
  3. UVEST Filtered Expectancy (GO Only)   : +0.72R / trade
     ↳ NET EDGE MULTIPLIER (Delta E)       : +0.58R / trade (+414% Value-Add) [PASS]

  SAFETY GATES & CIRCUIT BREAKERS:
  ├── Hard Governance Breakers (Pledge > 25%): 100.0% Blocked (6/6)       [PASS]
  ├── Inverted Risk-Reward Prevention       : 100.0% Blocked/Flipped (8/8) [PASS]
  ├── Binary Earnings Hazard Prevention     : 100.0% Blocked (7/7)       [PASS]
  └── Stewardship Alpha (S5 Soft Exit Value): +19.2% Drawdown Reduction  [PASS]
================================================================================
  VERDICT: SUITE PASSED — SYSTEM DEMONSTRATES STATISTICAL EDGE IMPROVEMENT
================================================================================
```

---

## 6. The 7 Frozen Evaluation Gates (S1 to S7)

| Gate | Name | Exact Mathematical Pass Criterion | Why It Cannot Be Bypassed |
| :--- | :--- | :--- | :--- |
| **S1** | **Filter Delta (55% Gate)** | Trades stamped `GO` must have entered with $P_{\text{win}} \ge 55.0\%$, and the realized win frequency must exceed $50.0\%$. Realized expectancy of `GO` bucket must exceed the advisory baseline ($E_{\text{GO}} > E_{\text{baseline}}$). | Proves that the system's "yes" actually makes money on the real tape. |
| **S2** | **Calibration** | Absolute Brier Score on 22-day direction $\le 0.180$. Absolute calibration error per bucket: $\|P_{\text{predicted}} - P_{\text{realized}}\| \le 10.0\text{pp}$. | Prevents probability hallucination and ungrounded confidence. |
| **S3** | **Per-Regime Honesty** | Expectancy of `GO` bucket must remain strictly positive ($E > 0$) across individual market regimes (VIX bands, sector groups). | Prevents bull-market runs from masking fatal flaws in choppy or bear regimes. |
| **S4** | **AVOID Cost Accounting** | False Negative rate must not exceed $20.0\%$ ($FN \le 12 / 62$). Forgone expectancy is published. | Prevents the engine from taking the cowardly way out by rejecting everything. |
| **S5** | **Stewardship Value-Add** | Evaluated across losing setups ($N \ge 25$) with a $1.0\times\text{ATR}$ noise buffer. Counterfactual exits at `soft_exit_zone` must reduce cumulative portfolio drawdown by $\ge 15.0\%$ compared to hard stop-loss exits. | Proves that daily position triage actually saves retail capital. |
| **S6** | **No-Lookahead Integrity** | 100% match on fixture SHA-256 checksums. Zero forward leakage. Truncation verified at $T_{\text{rec}}$. | Guarantees test results are physically genuine and un-gameable. |
| **S7** | **Marginal-Band Honesty** | Setups in the $55.0\%–62.0\%$ predicted win rate band must realize positive mathematical expectancy ($E \ge 0$). | Validates the owner's exact rule: *"even >55% is enough if managed properly."* |

---

## 7. Automated Regression Testing Protocol

Every git commit or agent workflow must execute the frozen suite:
```bash
python tests/run_business_proof.py --baseline tests/proof/baseline_scorecard.json
```

1. **If any Circuit Breaker drops below 100.0%:** Build FAILS.
2. **If Net Edge Multiplier ($\Delta E$) drops below baseline:** Build FAILS (Regression detected).
3. **If Brier Score $> 0.180$:** Build FAILS (Calibration drift).
4. **If any number in the report does not match local JSON files:** Build FAILS (Hallucination detected).
