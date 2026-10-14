# 🛡️ UVEST Implementation Action Plan & Checklist

> **Single Source of Truth References:**  
> - Architecture, specifications, and pipeline contracts: [BRD.md](file:///d:/Code/uvest/BRD.md)  
> - Ticket decomposition and test seams: [docs/tickets/INDEX.md](file:///d:/Code/uvest/docs/tickets/INDEX.md)  
> - Behavioral evaluation benchmarks: [docs/agents/evals.md](file:///d:/Code/uvest/docs/agents/evals.md)  
> - Business proof scenarios: [business_test_cases.md](file:///d:/Code/uvest/business_test_cases.md)

---

## Stage 0: Canonical Ingestion Parser & Gatekeeper (`TICK-001`)
*Ref: [BRD.md §3](file:///d:/Code/uvest/BRD.md#L88), [docs/tickets/TICK-001-canonical-parser.md](file:///d:/Code/uvest/docs/tickets/TICK-001-canonical-parser.md)*

- [x] Text input parser for Univest notification text ([scripts/0_ingest_reco.py](file:///d:/Code/uvest/scripts/0_ingest_reco.py)).
- [x] Canonical schema emission to `current_trade.json` ([BRD.md §3.2](file:///d:/Code/uvest/BRD.md#L95)).
- [x] Hard options prohibition gate (`ERR_OPTIONS_PROHIBITED`, exit code 2; [BRD.md §3.3 #1](file:///d:/Code/uvest/BRD.md#L121)).
- [x] Ingestion refusals wired into the eval scorecard as a graded family: the `refusal_probes` roster (homoglyph option tokens and unrecognized labels) is replayed live by gate 1c — **Unrecognized Instrument Guardrail**, 100.0% threshold — beside the option probes rather than folded into them ([tests/run_evals.py](file:///d:/Code/uvest/tests/run_evals.py), [tests/test_run_evals.py](file:///d:/Code/uvest/tests/test_run_evals.py), [docs/agents/evals.md §2.1/§3 #3](file:///d:/Code/uvest/docs/agents/evals.md#L21)).
- [x] Close the instrument domain (ADR-001 "only Equities and Futures are permitted"): stage 0 accepts exactly `CASH`/`FUT` and terminates anything it cannot recognize with `ERR_INSTRUMENT_UNRECOGNIZED` (exit 2, no canonical payload) instead of admitting it and letting downstream stages read it as cash. Stops the homoglyph bypass — `Type: СЕ` (Cyrillic) is not the ASCII token `CE`, so the token gate cannot name it — and refuses invented labels (`NFO`, `EQUITY`) and the un-aliased `FUTURES` (stages 1/5/9 key off `FUT` alone). Its own code, never `ERR_OPTIONS_PROHIBITED`, so an unclassified paste cannot inflate the options rejection rate ([scripts/0_ingest_reco.py](file:///d:/Code/uvest/scripts/0_ingest_reco.py); fuzz family [tests/test_0_ingest_reco.py](file:///d:/Code/uvest/tests/test_0_ingest_reco.py), end-to-end stop [tests/test_run_pipeline.py](file:///d:/Code/uvest/tests/test_run_pipeline.py); [docs/agents/domain.md ADR-001](file:///d:/Code/uvest/docs/agents/domain.md#L42)).
- [x] Options gate hardened to **token** detection across both declaration sites (`Type:` and the option-shaped `Symbol:` a cash label can carry) after NFKC + invisible-character normalization, so near-miss pastes are refused instead of admitted — lowercase/fullwidth/zero-width `CE`, `Type: CASH (CE)`, and `Type: CASH` over `Symbol: NIFTY 25000 CE` all exit 2 with `ERR_OPTIONS_PROHIBITED` ([scripts/0_ingest_reco.py](file:///d:/Code/uvest/scripts/0_ingest_reco.py); deterministic property suite [tests/test_0_ingest_reco.py](file:///d:/Code/uvest/tests/test_0_ingest_reco.py), which fails on the old field-equality gate). Colon look-alikes NFKC does not fold (`Type∶CE` U+2236) parse as nothing and are refused with no canonical payload — graded at that weaker, achievable contract rather than on a code the parser cannot reach ([docs/agents/evals.md §2.1](file:///d:/Code/uvest/docs/agents/evals.md#L21)).
- [x] Mock MWPL ban check and ASM/GSM surveillance flags ([BRD.md §3.3 #2-3](file:///d:/Code/uvest/BRD.md#L123)).
- [x] Lot margin exposure calculation for Futures ([BRD.md §3.3](file:///d:/Code/uvest/BRD.md#L121)).
- [x] Replay bundle recorder support via [_recorder.py](file:///d:/Code/uvest/scripts/_recorder.py).
- [x] Unit test seam ([tests/test_0_ingest_reco.py](file:///d:/Code/uvest/tests/test_0_ingest_reco.py)).
- [x] Close the stage-12 integration gap on `recommended_date`: the parser emitted the field but never required it, so a paste without `Recommended Date:` was admitted, the chain wrote stages 0 → 9, and the run then died at the **last** stage with `ERR_INPUT: trade input is missing recommended_date` — eleven artifacts down, no report. Ingestion now refuses that paste (`require_recommended_date`: present, and a real ISO calendar day) after the ADR-001 gates, so an option or unrecognized-instrument paste still owns its own exit code and "a paste stage 0 accepts" is now provably "a paste stage 12 can render" (`report_{SYMBOL}_{DATE}.html`; [TICK-010](file:///d:/Code/uvest/docs/tickets/TICK-010-html-visualizer.md)). Seams: `TestRecommendedDate` in [tests/test_0_ingest_reco.py](file:///d:/Code/uvest/tests/test_0_ingest_reco.py) (missing/malformed date refused, policy verdicts still speak first) and `test_a_dateless_paste_aborts_at_ingestion_not_at_stage_12` in [tests/test_run_pipeline.py](file:///d:/Code/uvest/tests/test_run_pipeline.py) (the chain stops at stage 0 with no downstream artifact).
- [ ] Screenshot / Image file OCR input ingestion handling via `--image <path>` ([BRD.md §3.1 #2](file:///d:/Code/uvest/BRD.md#L92), [TICK-001 §2](file:///d:/Code/uvest/docs/tickets/TICK-001-canonical-parser.md#L15)).
- [ ] Default fallback assignment `user_capital_inr: 500000.00` when omitted in raw paste ([BRD.md §3.2](file:///d:/Code/uvest/BRD.md#L116)).
- [ ] Contract lot size validation against exchange standard lots with mismatch warning ([TICK-001 §3](file:///d:/Code/uvest/docs/tickets/TICK-001-canonical-parser.md#L22)).
- [ ] Live automated scraper for NSE MWPL F&O ban list replacing mock set ([BRD.md §3.3 #2](file:///d:/Code/uvest/BRD.md#L123), [BRD.md §8](file:///d:/Code/uvest/BRD.md#L249)).
- [ ] Live automated scraper for SEBI ASM/GSM surveillance list replacing mock set ([BRD.md §3.3 #3](file:///d:/Code/uvest/BRD.md#L124), [BRD.md §8](file:///d:/Code/uvest/BRD.md#L249)).

---

## Stage 1: Mathematical R:R, Slippage & Margin Engine (`TICK-002`)
*Ref: [BRD.md §9.1](file:///d:/Code/uvest/BRD.md#L285), [BRD.md §10.3 #2](file:///d:/Code/uvest/BRD.md#L388), [docs/tickets/TICK-002-math-and-rr.md](file:///d:/Code/uvest/docs/tickets/TICK-002-math-and-rr.md)*

- [x] Nominal R:R (`raw_rr`) vs live R:R (`live_rr`) calculation ([scripts/1_math_and_rr.py](file:///d:/Code/uvest/scripts/1_math_and_rr.py)).
- [x] Signed outside-range slippage percentage (`outside_range_pct`).
- [x] Inverted R:R circuit breaker flag (`inverted_rr_flag`: `live_rr < 1.0`).
- [x] Risk/reward INR calculation across Cash and Futures contracts.
- [x] User capital allocation percentage calculation (`capital_allocation_pct`).
- [x] Replay bundle recorder support via [_recorder.py](file:///d:/Code/uvest/scripts/_recorder.py).
- [x] Contract output emission to `out_1_math.json`.
- [x] Unit test seam ([tests/test_1_math_and_rr.py](file:///d:/Code/uvest/tests/test_1_math_and_rr.py)).

---

## Stage 2: Daily OHLCV, ATR & Trend Regime Engine (`TICK-013`)
*Ref: [BRD.md §9.1](file:///d:/Code/uvest/BRD.md#L285), [BRD.md §10.3 #5](file:///d:/Code/uvest/BRD.md#L391), [docs/tickets/TICK-013-market-data.md](file:///d:/Code/uvest/docs/tickets/TICK-013-market-data.md)*

- [x] 14-period True Range and arithmetic ATR (`atr_14`) calculation ([scripts/2_market_data.py](file:///d:/Code/uvest/scripts/2_market_data.py)).
- [x] Volatility noise-stop circuit breaker: `is_tight_sl` (`sl_atr_ratio < 0.75`).
- [x] EMA 20, EMA 50, SMA 200, and Bollinger Bands with population $\sigma$.
- [x] Trend regime classification (`trend_state`).
- [x] Tri-state waiver path (`waived: true`, null metrics, exit 0) on missing sessions.
- [x] Replay bundle recorder support via [_recorder.py](file:///d:/Code/uvest/scripts/_recorder.py).
- [x] Contract output emission to `out_2_market.json`.
- [x] Unit test seam ([tests/test_2_market_data.py](file:///d:/Code/uvest/tests/test_2_market_data.py)).
- [x] Fixture loader honours the documented tri-state waiver for a **malformed or unreadable** OHLCV fixture (it took the waiver path only for a *missing* file and escaped with an uncaught `JSONDecodeError` traceback otherwise, aborting the chain on a data gap) — aligned with the convention stages 5/6 freeze, seam-pinned by `test_malformed_ohlcv_fixture_is_waived_not_a_traceback` ([scripts/2_market_data.py](file:///d:/Code/uvest/scripts/2_market_data.py)).
- [ ] Live Tier-1 Yahoo Finance (`yfinance`) daily OHLCV fetcher fallback with `.NS` ticker suffix when `--ohlcv-fixture` is omitted ([BRD.md §8](file:///d:/Code/uvest/BRD.md#L249), [TICK-013 §1](file:///d:/Code/uvest/docs/tickets/TICK-013-market-data.md#L20)).
- [ ] Local filesystem caching of downloaded OHLCV daily bars under `data_cache/` ([BRD.md §8](file:///d:/Code/uvest/BRD.md#L249)).

---

## Stage 3: FastDTW 10-Year Historical Pattern Matcher (`TICK-003`)
*Ref: [BRD.md §5.1](file:///d:/Code/uvest/BRD.md#L142), [docs/tickets/TICK-003-dtw-matcher.md](file:///d:/Code/uvest/docs/tickets/TICK-003-dtw-matcher.md)*

- [x] 30-day window population Z-score normalization ([scripts/3_dtw_pattern.py](file:///d:/Code/uvest/scripts/3_dtw_pattern.py)).
- [x] Sakoe-Chiba FastDTW distance calculation against historical series.
- [x] Top $K=15$ nearest analog extraction and deterministic ranking.
- [x] 22-day forward metrics: `dtw_win_rate_22d` (+4%), `dtw_median_return`, `dtw_mae_drawdown`.
- [x] Bounded analog confidence score (`analog_confidence`).
- [x] Tri-state waiver path on missing history fixture.
- [x] Replay bundle recorder support via [_recorder.py](file:///d:/Code/uvest/scripts/_recorder.py).
- [x] Contract output emission to `out_3_dtw.json`.
- [x] Unit test seam ([tests/test_3_dtw_pattern.py](file:///d:/Code/uvest/tests/test_3_dtw_pattern.py)).
- [x] History fixture loader honours the same tri-state waiver for a malformed/unreadable fixture instead of aborting the chain with a traceback ([scripts/3_dtw_pattern.py](file:///d:/Code/uvest/scripts/3_dtw_pattern.py); seam `test_malformed_history_fixture_is_waived_not_a_traceback`).
- [ ] Live Tier-1 10-year daily close fetcher via `yfinance` when `--history-fixture` is omitted ([BRD.md §5.1](file:///d:/Code/uvest/BRD.md#L142), [BRD.md §8](file:///d:/Code/uvest/BRD.md#L249)).
- [ ] Local filesystem caching of 10-year close series in `data_cache/` ([BRD.md §8](file:///d:/Code/uvest/BRD.md#L249)).

---

## Stage 4: LightGBM 1-Month Direction & Volatility Cone (`TICK-004`)
*Ref: [BRD.md §4](file:///d:/Code/uvest/BRD.md#L128), [BRD.md §5.2](file:///d:/Code/uvest/BRD.md#L156), [docs/tickets/TICK-004-lightgbm-cone.md](file:///d:/Code/uvest/docs/tickets/TICK-004-lightgbm-cone.md)*

- [ ] Test seam implementation: `tests/test_4_ml_direction.py` ([TICK-004 §3](file:///d:/Code/uvest/docs/tickets/TICK-004-lightgbm-cone.md#L42)).
- [ ] Script creation: `scripts/4_ml_direction.py` ([BRD.md §5.2](file:///d:/Code/uvest/BRD.md#L156)).
- [ ] Multi-factor feature engineering pipeline ([BRD.md §5.2](file:///d:/Code/uvest/BRD.md#L159)):
  - Technical drift: EMA 10/20/50 slopes, RSI 14, MACD histogram / ATR, ADX.
  - Volatility spectrum: 14D Parkinson & Garman-Klass volatility, ATR/price ratio, Bollinger bandwidth.
  - Mean reversion & gap: 20D SMA Z-score, 5-day overnight gap return sum.
  - Microstructure momentum: 5D delivery % slope, Volume-Weighted Price Drive.
  - Cross-asset correlation: 20D beta to NIFTY 50, 20D sectoral correlation.
- [ ] Pre-trained LightGBM direction classifier inference ($P(\text{Bullish})$, $P(\text{Neutral})$, $P(\text{Bearish})$) and target probability `prob_target_hit_22d` ([BRD.md §5.2](file:///d:/Code/uvest/BRD.md#L156)).
- [ ] Pretrained LightGBM model weights artifact or embedded fallback weights stored in repository ([TICK-004 §1](file:///d:/Code/uvest/docs/tickets/TICK-004-lightgbm-cone.md#L13)).
- [ ] 22-day forward volatility price cone $[P_{10}, P_{25}, P_{50}, P_{75}, P_{90}]$ with monotonic ordering check ([BRD.md §4](file:///d:/Code/uvest/BRD.md#L135), [TICK-004 §3](file:///d:/Code/uvest/docs/tickets/TICK-004-lightgbm-cone.md#L45)).
- [ ] Projected Max Adverse Excursion (`mae_projected`) and Max Favorable Excursion (`mfe_projected`) ([BRD.md §4](file:///d:/Code/uvest/BRD.md#L135)).
- [ ] Validate whether recommended stoploss is safely positioned below `mae_projected` ([BRD.md §4](file:///d:/Code/uvest/BRD.md#L135), [TICK-004 §3](file:///d:/Code/uvest/docs/tickets/TICK-004-lightgbm-cone.md#L46)).
- [ ] Top 5 feature attribution / SHAP drivers (`shap_drivers`) ([BRD.md §5.2](file:///d:/Code/uvest/BRD.md#L166)).
- [ ] Directional label categorization (`direction_label`: `STRONG_BULLISH`, `BULLISH`, `NEUTRAL`, `BEARISH`) ([TICK-004 §2](file:///d:/Code/uvest/docs/tickets/TICK-004-lightgbm-cone.md#L39)).
- [ ] Tri-state waiver path (`waived: true`, null metrics, exit 0) when model artifacts or features are absent ([BRD.md §10.4](file:///d:/Code/uvest/BRD.md#L409)).
- [ ] Replay bundle recorder support via [_recorder.py](file:///d:/Code/uvest/scripts/_recorder.py).
- [ ] Contract output emission to `out_4_ml.json`.

---

## Stage 5: Institutional Order Flow: Bhavcopy × Futures OI Matrix (`TICK-005`)
*Ref: [BRD.md §5.3](file:///d:/Code/uvest/BRD.md#L168), [BRD.md §5.4](file:///d:/Code/uvest/BRD.md#L180), [docs/tickets/TICK-005-institutional-matrix.md](file:///d:/Code/uvest/docs/tickets/TICK-005-institutional-matrix.md)*

- [x] Bhavcopy Delivery % and 20-day rolling Z-score calculation ([scripts/5_institutional_flow.py](file:///d:/Code/uvest/scripts/5_institutional_flow.py)).
- [x] Futures Open Interest (OI) change % calculation.
- [x] Holy Trinity Matrix classification (`LONG_BUILDUP`, `SHORT_COVERING_TRAP`, `SHORT_BUILDUP`, `LONG_UNWINDING`; [BRD.md §5.3](file:///d:/Code/uvest/BRD.md#L173)).
- [x] Futures Spot-Futures Basis and annualized carry cost calculation ([BRD.md §5.4](file:///d:/Code/uvest/BRD.md#L180)).
- [x] Tri-state waiver path (`waived: true`, null metrics, exit 0) on missing Bhavcopy fixture.
- [x] Replay bundle recorder support via [_recorder.py](file:///d:/Code/uvest/scripts/_recorder.py).
- [x] Contract output emission to `out_5_flow.json`.
- [x] Unit test seam ([tests/test_5_institutional_flow.py](file:///d:/Code/uvest/tests/test_5_institutional_flow.py)).
- [ ] Live automated downloader/scraper for official NSE Daily Bhavcopy (`sec_bhavdata_full.csv`, `fo_bhav.csv`) when `--bhavcopy-fixture` is omitted ([BRD.md §5.3](file:///d:/Code/uvest/BRD.md#L170), [BRD.md §8](file:///d:/Code/uvest/BRD.md#L249)).
- [ ] Local CSV archive caching of downloaded Bhavcopy under `data_cache/` ([BRD.md §8](file:///d:/Code/uvest/BRD.md#L249)).
- [ ] Expiry-week rollover % calculation relative to 3-month average for Futures ([BRD.md §5.4](file:///d:/Code/uvest/BRD.md#L183)).

---

## Stage 6: Fundamental & Corporate Governance Auditor (`TICK-006`)
*Ref: [BRD.md §6](file:///d:/Code/uvest/BRD.md#L187), [BRD.md §10.3 #1, #4](file:///d:/Code/uvest/BRD.md#L386), [docs/tickets/TICK-006-fundamental-audit.md](file:///d:/Code/uvest/docs/tickets/TICK-006-fundamental-audit.md)*

- [x] Test seam implementation: `tests/test_6_fundamental_audit.py` ([TICK-006 §3](file:///d:/Code/uvest/docs/tickets/TICK-006-fundamental-audit.md#L42)).
- [x] Script creation: `scripts/6_fundamental_audit.py`.
- [x] Piotroski F-Score (0–9 scale) computation (`piotoski_score`; [BRD.md §6.1 #1](file:///d:/Code/uvest/BRD.md#L192)).
- [x] Score penalty (-15 pts) gate when $F \le 4$ ([BRD.md §6.1 #1](file:///d:/Code/uvest/BRD.md#L196)).
- [x] Emerging market Altman Z''-Score computation (`altman_z_score`: distress $<1.1$, safe $>2.6$; [BRD.md §6.1 #2](file:///d:/Code/uvest/BRD.md#L197)).
- [x] Beneish M-Score earnings manipulation check ($M > -1.78$ red flag; [BRD.md §6.1 #3](file:///d:/Code/uvest/BRD.md#L199)).
- [x] Cash flow quality ratio: Operating Cash Flow to Net Profit (`cfo_pat_ratio` < 0.6 accrual warning; [BRD.md §6.2](file:///d:/Code/uvest/BRD.md#L202)).
- [x] Promoter pledging check (`promoter_pledge_pct`): warning if $>15\%$, hard kill switch `pledge_circuit_breaker: true` if $>25.0\%$ (forces `AVOID`; [BRD.md §6.3 #1](file:///d:/Code/uvest/BRD.md#L206), [BRD.md §10.3 #1](file:///d:/Code/uvest/BRD.md#L387)).
- [x] Promoter holding trajectory check (2-quarter decline $>2\%$) and FII/DII retail divergence ([BRD.md §6.3 #2-3](file:///d:/Code/uvest/BRD.md#L208)).
- [x] Auditor disclosures screening for non-routine resignations within 12 months ([BRD.md §6.3 #4](file:///d:/Code/uvest/BRD.md#L212)).
- [x] Corporate earnings calendar hazard: `days_to_earnings`, flag `earnings_hazard_flag: true` if $<7$d on Futures (forces `AVOID`; [BRD.md §6.4](file:///d:/Code/uvest/BRD.md#L215), [BRD.md §10.3 #4](file:///d:/Code/uvest/BRD.md#L390)).
- [x] Earnings calendar size-cap: flag `confidence_deficit` / cap size to 50% (`GO_WEAK`) if $<10$d on Cash Equities ([BRD.md §6.4](file:///d:/Code/uvest/BRD.md#L217), [BRD.md §10.4](file:///d:/Code/uvest/BRD.md#L400)).
- [x] Tri-state waiver path (`waived: true`, null metrics, exit 0) on missing financial reports ([BRD.md §10.4](file:///d:/Code/uvest/BRD.md#L409)).
- [x] Replay bundle recorder support via [_recorder.py](file:///d:/Code/uvest/scripts/_recorder.py).
- [x] Contract output emission to `out_6_fundamental.json`.
- [ ] Public data connector for Screener.in / NSE corporate filings with offline fixture fallback ([BRD.md §8](file:///d:/Code/uvest/BRD.md#L249)).

---

## Stage 7: Sector Relative Strength & Macro VIX Regime (`TICK-007`)
*Ref: [BRD.md §7](file:///d:/Code/uvest/BRD.md#L221), [BRD.md §10.3 #6](file:///d:/Code/uvest/BRD.md#L392), [docs/tickets/TICK-007-sector-macro.md](file:///d:/Code/uvest/docs/tickets/TICK-007-sector-macro.md)*

- [ ] Test seam implementation: `tests/test_7_sector_macro_regime.py` ([TICK-007 §3](file:///d:/Code/uvest/docs/tickets/TICK-007-sector-macro.md#L43)).
- [ ] Script creation: `scripts/7_sector_macro_regime.py`.
- [ ] Stock symbol to parent NSE sectoral index mapping ([BRD.md §7.1](file:///d:/Code/uvest/BRD.md#L226)).
- [ ] 50-day rolling Mansfield Relative Strength (`sector_mrs`) vs NIFTY 50 ([BRD.md §7.1](file:///d:/Code/uvest/BRD.md#L225)).
- [ ] Relative Rotation Graph (RRG) quadrant classification: `LEADING`, `WEAKENING`, `LAGGING`, `IMPROVING` (`sector_rrg_state`; [BRD.md §7.1](file:///d:/Code/uvest/BRD.md#L229)).
- [ ] NIFTY 50 macro trend classification relative to 50 DMA and 200 DMA (`nifty_trend`; [BRD.md §7.2](file:///d:/Code/uvest/BRD.md#L231)).
- [ ] NSE 500 market breadth percentage (`market_breadth_pct`: % stocks > 200 DMA; [BRD.md §7.2](file:///d:/Code/uvest/BRD.md#L233)).
- [ ] India VIX volatility regime classification (`vix_regime`: `LOW` <12, `NORMAL` 12–18, `ELEVATED` 18–24, `PANIC` >24; [BRD.md §7.3](file:///d:/Code/uvest/BRD.md#L238)).
- [ ] Panic regime circuit breaker: `panic_regime_breaker: true` if VIX $> 24.0$ and Breadth $< 30.0\%$ (forces `AVOID` for long setups; [BRD.md §7.3](file:///d:/Code/uvest/BRD.md#L242), [BRD.md §10.3 #6](file:///d:/Code/uvest/BRD.md#L392)).
- [ ] Rolling 5-day combined FII + DII net cash flow bias (`fii_dii_flow_bias`: cautious sizing if $< -₹5,000 \text{ Cr}$; [BRD.md §7.4](file:///d:/Code/uvest/BRD.md#L244)).
- [ ] Tri-state waiver path (`waived: true`, null metrics, exit 0) on missing index data ([BRD.md §10.4](file:///d:/Code/uvest/BRD.md#L409)).
- [ ] Replay bundle recorder support via [_recorder.py](file:///d:/Code/uvest/scripts/_recorder.py).
- [ ] Contract output emission to `out_7_regime.json`.
- [ ] Public data connector for NIFTY Indices portal / yfinance macro tickers (`^NSEI`, `^INDIAVIX`) with offline fixture fallback ([BRD.md §8](file:///d:/Code/uvest/BRD.md#L249)).

---

## Stage 8: Dhan API Precision Intraday & Depth Client (`TICK-008`)
*Ref: [BRD.md §4](file:///d:/Code/uvest/BRD.md#L136), [BRD.md §8](file:///d:/Code/uvest/BRD.md#L249), [BRD.md §8.1](file:///d:/Code/uvest/BRD.md#L271), [docs/tickets/TICK-008-dhan-precision.md](file:///d:/Code/uvest/docs/tickets/TICK-008-dhan-precision.md)*

- [ ] Test seam implementation: `tests/test_8_dhan_precision.py` ([TICK-008 §3](file:///d:/Code/uvest/docs/tickets/TICK-008-dhan-precision.md#L40)).
- [ ] Script creation: `scripts/8_dhan_precision.py`.
- [ ] Dhan API client authentication via `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` in `.env` ([BRD.md §8.1](file:///d:/Code/uvest/BRD.md#L272)).
- [ ] Fetch 5m/10m intraday bars via `POST /v2/charts/historical` ([BRD.md §8.1](file:///d:/Code/uvest/BRD.md#L274)).
- [ ] Fetch real-time market depth quote via `POST /v2/marketfeed/quote` ([BRD.md §8.1](file:///d:/Code/uvest/BRD.md#L275)).
- [ ] Local SQLite caching in `dhan_cache.db` with 15-minute TTL ([BRD.md §8.1](file:///d:/Code/uvest/BRD.md#L276)).
- [ ] Precision Anchored VWAP (`anchored_vwap`) anchored from recent session swing low pivot ([BRD.md §4](file:///d:/Code/uvest/BRD.md#L136)).
- [ ] Demand order block and Fair Value Gap support identification (`orderblock_support`; [TICK-008 §1](file:///d:/Code/uvest/docs/tickets/TICK-008-dhan-precision.md#L14)).
- [ ] Optimal limit buy execution price and range (`optimal_limit_buy`, `optimal_limit_range`) reducing MAE vs market entry ([BRD.md §4](file:///d:/Code/uvest/BRD.md#L136), [TICK-008 §3](file:///d:/Code/uvest/docs/tickets/TICK-008-dhan-precision.md#L43)).
- [ ] Real-time bid-ask spread and impact cost percentage (`spread_impact_cost_pct`; [BRD.md §8.1](file:///d:/Code/uvest/BRD.md#L275)).
- [ ] Graceful offline degradation / waiver path (`waived: true`, null metrics, exit 0) when credentials missing or HTTP 429/500 ([BRD.md §8.1](file:///d:/Code/uvest/BRD.md#L277)).
- [ ] Replay bundle recorder support via [_recorder.py](file:///d:/Code/uvest/scripts/_recorder.py).
- [ ] Contract output emission to `out_8_dhan.json`.
- [ ] Template `.env.example` in repository root for Dhan credentials configuration ([BRD.md §8.1](file:///d:/Code/uvest/BRD.md#L272)).

---

## Stage 9: Consolidated Multi-Factor Decision Engine (`TICK-009`)
*Ref: [BRD.md §4](file:///d:/Code/uvest/BRD.md#L128), [BRD.md §10](file:///d:/Code/uvest/BRD.md#L352), [BRD.md §16](file:///d:/Code/uvest/BRD.md#L630), [docs/tickets/TICK-009-decision-engine.md](file:///d:/Code/uvest/docs/tickets/TICK-009-decision-engine.md)*

- [x] Test seam implementation: `tests/test_9_decision_engine.py` ([TICK-009 §3](file:///d:/Code/uvest/docs/tickets/TICK-009-decision-engine.md#L54)).
- [x] Script creation: `scripts/9_decision_engine.py`.
- [x] Aggregation of upstream JSON artifacts (`current_trade.json`, `out_1_*.json` through `out_8_*.json`).
- [x] 100-Point calibration scoring model across 6 weighted modules (Math 20, Quant/ML 25, Flow 20, Fundamentals 15, Sector/Macro 15, Precision 5; [BRD.md §10.2](file:///d:/Code/uvest/BRD.md#L363)).
- [x] Reading baseline scoring weights from `thresholds.json` with fallback defaults ([BRD.md §9.1](file:///d:/Code/uvest/BRD.md#L309), [BRD.md §12.2](file:///d:/Code/uvest/BRD.md#L510)).
- [x] Waiver rule scoring adjustments: deduct waived points from `max_possible_score`, compute `effective_score_pct`, and enforce $\ge 1$ waived stage caps verdict at `GO_WEAK` ([BRD.md §10.4](file:///d:/Code/uvest/BRD.md#L409)).
- [x] Hard circuit breaker veto enforcement: any tripped breaker among the 6 fatal triggers forces `AVOID` (except inverted R:R alone, which flips to `WAIT` if limit order restores $R:R \ge 1.5$; [BRD.md §10.3](file:///d:/Code/uvest/BRD.md#L381)).
- [x] Statistical expectancy calculation: $E = P(\text{Win}) \times R_{\text{Reward}} - (1 - P(\text{Win})) \times R_{\text{Risk}}$ ([BRD.md §10.1](file:///d:/Code/uvest/BRD.md#L357)).
- [x] Calibrated win probability $P(\text{win})$ calculation anchoring on advisory baseline ($P_{\text{base}} \approx 55\%–60\%$) plus conditional improvement $\Delta P_{\text{win}}$ ([BRD.md §10.1](file:///d:/Code/uvest/BRD.md#L360)).
- [x] Canonical 7-state pre-entry verdict assignment: `GO` ($P_{\text{win}} \ge 55\%$, $R:R \ge 1.5$, 100% size), `GO_WEAK` (waived data or size cap, 50% size), `WAIT` (recoverable by limit), `AVOID` (fatal breaker or negative expectancy; [BRD.md §10.4](file:///d:/Code/uvest/BRD.md#L394)).
- [x] Strict emission of both `decision` and `verdict` matching canonical tokens; no retired tokens (`NO_GO`, `CONDITIONAL_GO`; [BRD.md §9.1](file:///d:/Code/uvest/BRD.md#L345), [ADR-006](file:///d:/Code/uvest/docs/agents/domain.md#L62)).
- [x] Flip Condition Invariant: populate mandatory `what_would_flip_me` for all non-`GO` verdicts (`WAIT`, `AVOID`; [BRD.md §10.4](file:///d:/Code/uvest/BRD.md#L407)).
- [x] Synthesis of Three Core Predictions payload (`three_predictions`: Direction 1M, Expected Range Cone, Optimal Price Range; [BRD.md §4](file:///d:/Code/uvest/BRD.md#L128)).
- [x] Decision Flip Table synthesis (`flip_table`: Market Order vs Optimal Limit Order; [BRD.md §16](file:///d:/Code/uvest/BRD.md#L684)).
- [x] Formatted Terminal Markdown Decision Report emitted to stderr / terminal for IDE Agent chat ([BRD.md §16](file:///d:/Code/uvest/BRD.md#L630), [prompt.md §5](file:///d:/Code/uvest/prompt.md#L121)).
- [x] Replay bundle recorder support via [_recorder.py](file:///d:/Code/uvest/scripts/_recorder.py).
- [x] Contract output emission to `out_9_decision.json`.

---

## Stage 10: Daily Position Triage & Adverse Movement Tracker (`TICK-011`)
*Ref: [BRD.md §11](file:///d:/Code/uvest/BRD.md#L413), [docs/tickets/TICK-011-daily-triage.md](file:///d:/Code/uvest/docs/tickets/TICK-011-daily-triage.md)*

- [ ] Test seam implementation: `tests/test_10_daily_triage.py` ([TICK-011 §3](file:///d:/Code/uvest/docs/tickets/TICK-011-daily-triage.md#L44)).
- [ ] Script creation: `scripts/10_daily_triage.py`.
- [ ] SQLite database initialization and management for `uvest_history.db` (`trades`, `triage_history` tables; [BRD.md §11](file:///d:/Code/uvest/BRD.md#L416)).
- [ ] CLI commands: `--action track` (log new confirmed trade), `--action triage` (daily loop; [BRD.md §9.1](file:///d:/Code/uvest/BRD.md#L327), [BRD.md §11.1](file:///d:/Code/uvest/BRD.md#L418)).
- [ ] Classification across Four Operational Zones: Profit Harvesting, Healthy Runway, Adverse Movement, Time Stop ([BRD.md §11.1](file:///d:/Code/uvest/BRD.md#L418)).
- [ ] Premise auditing: score Day-1 frozen premises into `reinforced`, `intact`, `weakening`, or `broken` ([BRD.md §11.1 #1](file:///d:/Code/uvest/BRD.md#L420), [BRD.md §11.2](file:///d:/Code/uvest/BRD.md#L430)).
- [ ] Deterministic posterior update: $\text{still\_chance\_pct} = \text{clamp}(P_{\text{prior}} + \sum \Delta_{\text{premise}} - \Delta_{\text{drawdown}}, 5\%, 95\%)$ ([BRD.md §11.2](file:///d:/Code/uvest/BRD.md#L428)).
- [ ] Adverse drawdown penalty: $\le 1.0\times\text{ATR} \implies 0\%$, $> 1.0\times\text{ATR} \implies -5\%$, $> 1.5\times\text{ATR} \implies -12\%$ ([BRD.md §11.2](file:///d:/Code/uvest/BRD.md#L435)).
- [ ] Promotion to `PRESSURE` if `still_chance_pct` drops $>15\text{pp}$ below entry prior or $<55\%$ for 2 consecutive sessions ([BRD.md §11.2](file:///d:/Code/uvest/BRD.md#L439)).
- [ ] Actionable tri-state verdict assignment: `HOLD`, `PRESSURE`, `EXIT` ([BRD.md §11.3](file:///d:/Code/uvest/BRD.md#L441), [BRD.md §11.4](file:///d:/Code/uvest/BRD.md#L482)).
- [ ] Pre-calculated action levels calculation: `soft_exit_zone`, `tighten_stop_to`, `hard_stop` ([BRD.md §11.3](file:///d:/Code/uvest/BRD.md#L468)).
- [ ] Time stop enforcement: close futures active $>12$ days without progress before weekend carry cost ([BRD.md §11.1 #4](file:///d:/Code/uvest/BRD.md#L418)).
- [ ] Golden Rule enforcement: flag and strictly prohibit averaging down losing positions ([BRD.md §11.4](file:///d:/Code/uvest/BRD.md#L490)).
- [ ] Contract output emission to `out_10_triage.json` matching canonical schema ([BRD.md §11.3](file:///d:/Code/uvest/BRD.md#L444)).

---

## Stage 11: Continuous Learning & Failure Autopsy Engine (`TICK-012`)
*Ref: [BRD.md §12](file:///d:/Code/uvest/BRD.md#L494), [docs/tickets/TICK-012-failure-autopsy.md](file:///d:/Code/uvest/docs/tickets/TICK-012-failure-autopsy.md)*

- [ ] Test seam implementation: `tests/test_11_postmortem_learning.py` ([TICK-012 §3](file:///d:/Code/uvest/docs/tickets/TICK-012-failure-autopsy.md#L43)).
- [ ] Script creation: `scripts/11_postmortem_learning.py`.
- [ ] CLI invocation: `--resolve <TRADE_ID> --outcome LOSS/WIN` ([BRD.md §9.1](file:///d:/Code/uvest/BRD.md#L328), [TICK-012 §3](file:///d:/Code/uvest/docs/tickets/TICK-012-failure-autopsy.md#L45)).
- [ ] 5-category root-cause failure taxonomy decomposition for losses: `VOLATILITY_NOISE`, `REGIME_SHOCK`, `INSTITUTIONAL_DISTRIBUTION`, `EARNINGS_SURPRISE`, `ENTRY_SLIPPAGE` ([BRD.md §12.1](file:///d:/Code/uvest/BRD.md#L498)).
- [ ] Deterministic Bayesian self-audit & cumulative Brier score calculation: $\text{Brier} = \frac{1}{N} \sum (P_{\text{win}, k} - y_k)^2$ ([BRD.md §12.2](file:///d:/Code/uvest/BRD.md#L510)).
- [ ] Sole writer of `thresholds.json`: conservative bounded linear rubric weight adjustments ($\Delta w = \pm 0.5$ pts, learning rate $\eta = 0.05$; [BRD.md §12.2](file:///d:/Code/uvest/BRD.md#L511)).
- [ ] Persist autopsy breakdown and weight adjustment rationale in `uvest_history.db` (`autopsy_records` table; [BRD.md §12.2](file:///d:/Code/uvest/BRD.md#L512)).
- [ ] Update resolved trade status to `CLOSED` in `uvest_history.db` leaving active open positions untouched ([TICK-012 §3](file:///d:/Code/uvest/docs/tickets/TICK-012-failure-autopsy.md#L48)).
- [ ] Contract output emission to `out_11_postmortem.json` ([BRD.md §9.1](file:///d:/Code/uvest/BRD.md#L309)).

---

## Stage 12: Standalone Interactive HTML Visualizer (`TICK-010`)
*Ref: [BRD.md §13](file:///d:/Code/uvest/BRD.md#L516), [reports/sample_report.html](file:///d:/Code/uvest/reports/sample_report.html), [docs/tickets/TICK-010-html-visualizer.md](file:///d:/Code/uvest/docs/tickets/TICK-010-html-visualizer.md)*

- [x] HTML report card design mockup / prototype ([reports/sample_report.html](file:///d:/Code/uvest/reports/sample_report.html)).
- [x] Test seam implementation: `tests/test_12_generate_html_report.py` ([TICK-010 §3](file:///d:/Code/uvest/docs/tickets/TICK-010-html-visualizer.md#L19)).
- [x] The trade fields stage 12 cannot render without are declared once (`REQUIRED_FIELDS`: `symbol`, plus the date the artifact name is built from) and read by the chain contract, so stage 0's guarantee and stage 12's requirement can never drift apart in silence ([scripts/12_generate_html_report.py](file:///d:/Code/uvest/scripts/12_generate_html_report.py), [tests/test_chain_contract.py](file:///d:/Code/uvest/tests/test_chain_contract.py)).
- [x] Script creation: `scripts/12_generate_html_report.py`.
- [x] Ingest canonical outputs: `current_trade.json` and `out_1_*.json` through `out_9_*.json` ([BRD.md §13](file:///d:/Code/uvest/BRD.md#L516), [TICK-010 §2](file:///d:/Code/uvest/docs/tickets/TICK-010-html-visualizer.md#L16)). Stages 1/2/9 keep their own sections; a supplied stage 3–11 payload renders a card of the scalar fields it states (verbatim, never recomputed), so evidence a report is given is never silently dropped — the specialized gauges below remain open.
- [x] Standalone zero-dependency HTML5 rendering saved directly to `reports/report_{SYMBOL}_{DATE}.html` ([BRD.md §13.1](file:///d:/Code/uvest/BRD.md#L520)).
- [x] Sleek dark mode palette (`#0d1117` background, emerald `#2ea043`, crimson `#da3633`, amber `#d29922`) and typography ([BRD.md §13.1](file:///d:/Code/uvest/BRD.md#L522)).
- [ ] Header & Decision badge in canonical 7-state vocabulary with composite score, $P(\text{win})$, and expectancy ([BRD.md §13.2](file:///d:/Code/uvest/BRD.md#L527)).
- [ ] Direction radial gauge / speedometer visualization ([BRD.md §13.2](file:///d:/Code/uvest/BRD.md#L531)).
- [ ] 22-Day forward volatility price cone fan chart visualization ($[P_{10}, P_{50}, P_{90}]$; [BRD.md §13.2](file:///d:/Code/uvest/BRD.md#L531)).
- [ ] Optimal limit execution zone range bar comparison (Live slipped vs recommended vs optimal limit; [BRD.md §13.2](file:///d:/Code/uvest/BRD.md#L531)).
- [ ] FastDTW historical pattern overlay chart (Current 30D path vs Top 5 historical analogs; [BRD.md §13.2](file:///d:/Code/uvest/BRD.md#L538)).
- [ ] Component cards: Institutional Flow Matrix, Fundamental Health, Sector/Macro Barometer ([BRD.md §13.2](file:///d:/Code/uvest/BRD.md#L542)).
- [ ] Decision Flip Table and Actionable Order Execution Ticket (Limit Buy price, SL, Target, Lot size, INR risk, R:R expansion; [BRD.md §13.2](file:///d:/Code/uvest/BRD.md#L548)).
- [ ] Zero external CDN/HTTP script requirements; renders offline in $<300\text{ms}$ via `file:///` ([BRD.md §13.1](file:///d:/Code/uvest/BRD.md#L521), [evals.md §3 #7](file:///d:/Code/uvest/docs/agents/evals.md#L41)).

---

## Behavioral Evaluations & Golden Dataset (`EVAL-001`)
*Ref: [BRD.md §15](file:///d:/Code/uvest/BRD.md#L595), [docs/agents/evals.md](file:///d:/Code/uvest/docs/agents/evals.md), [docs/tickets/EVAL-001-benchmark-suite.md](file:///d:/Code/uvest/docs/tickets/EVAL-001-benchmark-suite.md)*

- [x] Evaluation runner harness ([tests/run_evals.py](file:///d:/Code/uvest/tests/run_evals.py)).
- [x] Hard Circuit Breaker Accuracy grade: 100% enforcement pass.
- [x] Zero Hallucination Audit grade: 100% exact JSON match pass.
- [x] Price Cone Range Coverage grade: $\ge 80\%$ pass.
- [x] Optimal Limit Slippage Reduction grade: $\ge 75\%$ pass.
- [x] Golden dataset seeded to its frozen quotas (52 of 52 trades in `tests/evals/golden_dataset.json`).
- [x] Complete the remaining 10 trades in `tests/evals/golden_dataset.json` to reach the full 52 trades ([docs/agents/evals.md §2](file:///d:/Code/uvest/docs/agents/evals.md#L11)):
  - [x] 4 remaining High Promoter Pledging Traps seeded (`pledge_hazard`: 5/5) — the catalog's own pledge rows (SCEN-007/009/011/012) with recorded stage-6 evidence, so gate 2 grades `promoter_pledging_gt_25pct` from proven bytes ([tests/evals/seed_hazard_records.py](file:///d:/Code/uvest/tests/evals/seed_hazard_records.py)).
  - [x] 7 Impending Earnings Event Traps seeded (`earnings_hazard`: 7/7) — SCEN-013…017, the catalog's binary-event rows (its §5 roll-up counts SCEN-017's unscheduled board meeting as the fifth), plus the two ADR-009 boundary probes SCEN-062/063 at 1 and 6 sessions, each graded on `earnings_within_7d`; SCEN-016 needed the owner-commanded re-freeze R3 ([docs/agents/scenario-manifest-schema.md §10](file:///d:/Code/uvest/docs/agents/scenario-manifest-schema.md#L328)).
  - [x] 10 Institutional Distribution Traps seeded (`institutional_distribution`: 10/10) — scan-authored from NSE's own daily files (`sec_bhavdata_full` EQ delivery % + the F&O bhavcopy's front-month open interest), admitting only sessions the files measure as price up, delivery < 25% and OI <= -5% whose pinned tape then resolves LOSS under the frozen crisis geometry, with recorded stage-5 evidence over a disclosed bhavcopy fixture ([tests/evals/seed_distribution_records.py](file:///d:/Code/uvest/tests/evals/seed_distribution_records.py)).
  - The authored hazard bundles disclose what is authored (the catalog's documented pledge/earnings level) versus absent (the issuers' FY statements, so the ratio metrics stay null with `missing_inputs` naming every gap).
  - Both hazard classes grade from proven bytes with no abstention: the register's last quarantine (`GB-2023-0003`'s hand-typed stage-6 pledge payload) was retired into recorded stage-6 evidence, so the E5 meter is at 0 ([tests/evals/retire_quarantine_records.py](file:///d:/Code/uvest/tests/evals/retire_quarantine_records.py), [docs/agents/scenario-manifest-schema.md §8.3](file:///d:/Code/uvest/docs/agents/scenario-manifest-schema.md#L307)).
- [x] Seed Option-instrument test cases (e.g. 5 CE/PE recommendations) to unskip Option Rejection Guardrail eval ([docs/agents/evals.md §3 #3](file:///d:/Code/uvest/docs/agents/evals.md#L49)) — five authored Univest-style option pastes (`OPT-001`…`OPT-005`, tokens CE/PE/OPT/CALL/PUT across 2022–2026) seeded in a new top-level `option_probes` roster governed by `meta.option_probes_expected: 5` ([docs/agents/evals.md §2.1](file:///d:/Code/uvest/docs/agents/evals.md#L21)). The roster sits beside the frozen 52-trade quota rather than inside it — an option can never be a trade: stage 0 rejects the paste before a canonical `current_trade.json` exists, and the proof root pins no option scenario — so no class count, census cell or `distribution_verified` claim moved. Gate 1 replays each paste live through `scripts/0_ingest_reco.py` (invoked as a stage, never imported) and grades the frozen contract: exit 2 + `ERR_OPTIONS_PROHIBITED` + no canonical payload, with the fatal-token vocabulary asserted equal to the parser's own `OPTIONS_TYPES` ([tests/run_evals.py](file:///d:/Code/uvest/tests/run_evals.py)).
- [x] Grade the orchestrator's half of ADR-001 — gate 1b replays a *named* option probe through the full `scripts/run_pipeline.py` 0 → 12 chain and requires the **stop**: propagated exit 2, no downstream artifact, no report rendered from a rejected trade, and a `run_record.json` naming stage 0 `failed` with the gate's own code while every later stage is `not_reached`. Kept separate from gate 1 because a runner that logged the refusal and walked on would leave the rejection rate at 100.0%; one replay is enough because the runner does not branch on the token (per-token coverage stays gate 1's, and the stage-0 seam replays every probe through the orchestrator) ([tests/run_evals.py](file:///d:/Code/uvest/tests/run_evals.py), seam [tests/test_run_evals.py](file:///d:/Code/uvest/tests/test_run_evals.py), [docs/agents/evals.md §2.1/§3 #3](file:///d:/Code/uvest/docs/agents/evals.md#L21)). Extended to **both refusal families**: the gate — renamed **Ingestion Chain Abort** — replays one named option probe *and* one named refusal probe (a paste the token scan cannot name) through the chain, grading each stop against that family's own code (`ERR_OPTIONS_PROHIBITED` vs `ERR_INSTRUMENT_UNRECOGNIZED`; one is no evidence for the other), naming both probes and both codes on the scorecard line, and disclosing an unseeded roster as an ungraded half rather than a full green over the family left.
- [x] Grade stage 0's ingestion **refusals** as their own scorecard family — gate 1c, `Unrecognized Instrument Guardrail`, replays the new top-level `refusal_probes` roster live through `scripts/0_ingest_reco.py` and requires exit 2 + `ERR_INSTRUMENT_UNRECOGNIZED` + no canonical payload for every paste ([docs/agents/evals.md §2.1](file:///d:/Code/uvest/docs/agents/evals.md#L21)). Five authored pastes across the two shapes the option-token gate cannot name: three homoglyph option tokens (Cyrillic `СЕ`, Greek `ϹΕ`, mixed Latin/Cyrillic) and two unrecognized labels (`NFO`, `FUTURES`). Counted as a **separate line** from the Option Rejection Guardrail on purpose — a paste the gate never classified as an option must not inflate the option rejection rate — and the roster's own integrity check refuses a misfiling (an ASCII option token here, a `family` outside the frozen pair, a `declared_instrument` absent from the paste, an origin passed off as sourced) while `meta.refusal_probes_expected: 5` makes losing a probe exit 1 rather than a quietly smaller graded set ([tests/run_evals.py](file:///d:/Code/uvest/tests/run_evals.py), [tests/evals/golden_dataset.json](file:///d:/Code/uvest/tests/evals/golden_dataset.json), seam [tests/test_run_evals.py](file:///d:/Code/uvest/tests/test_run_evals.py)).
- [x] Give both probe rosters the rule census's regression floor: `probe_rosters` in [tests/proof/baseline_scorecard.json](file:///d:/Code/uvest/tests/proof/baseline_scorecard.json) freezes the roster sizes (5 option + 5 refusal) and `probe_roster_ids` each probe's identity: the SHA-256 of everything it declares — `raw_text` (the bytes the gates replay) *and* the metadata the roster checks enforce (`family`, `declared_instrument`, `origin`, `expected`) — keyed by `probe_id` — `run_evals.check_probe_roster_floor` recomputes both every run and prints `Probe roster floor: OK|REGRESSION` beside the roster checks, and a roster that shrinks — or vanishes entirely — is a named shortfall and exit 1, as is a probe **swapped** for a different paste (same count, different member — the gap a size-only floor cannot see: the frozen `probe_id` is named and the run fails even though every count-based check is green) or an edited paste — or a relabelled family or contract, which the roster checks can only validate for shape — at a frozen id, until a deliberate re-freeze (`scaffold_catalog.re_freeze_baseline`, which now banks both floors) accepts the new roster. Growing past the floor is free ([tests/run_evals.py](file:///d:/Code/uvest/tests/run_evals.py), seam [tests/test_run_evals.py](file:///d:/Code/uvest/tests/test_run_evals.py), [docs/agents/evals.md §4](file:///d:/Code/uvest/docs/agents/evals.md#L80)).
- [ ] Enable Directional Brier Score eval once Stage 4 emits probabilities (Brier score $\le 0.180$; [docs/agents/evals.md §3 #4](file:///d:/Code/uvest/docs/agents/evals.md#L38)).
- [ ] Connect HTML visualizer validation in `tests/run_evals.py` once Stage 12 is implemented ([docs/agents/evals.md §3 #7](file:///d:/Code/uvest/docs/agents/evals.md#L41)).
- [x] Set `distribution_verified: true` in `tests/evals/golden_dataset.json` — flipped when the dataset reached all 52 trades with every class count matched.

---

## Business Proof & Historical Scenarios Suite
*Ref: [business_test_cases.md](file:///d:/Code/uvest/business_test_cases.md), [BRD.md §15.1](file:///d:/Code/uvest/BRD.md#L602), [docs/agents/scenario-manifest-schema.md](file:///d:/Code/uvest/docs/agents/scenario-manifest-schema.md)*

- [x] 60 historical scenario catalog defined ([business_test_cases.md §3](file:///d:/Code/uvest/business_test_cases.md#L46)).
- [x] Point-in-time filesystem proof harness & manifest validation ([tests/test_validate_manifest.py](file:///d:/Code/uvest/tests/test_validate_manifest.py), [tests/test_proof_manifest.py](file:///d:/Code/uvest/tests/test_proof_manifest.py)).
- [x] First 20 scenarios scaffolded in `tests/proof/scenario_manifest.json` (`GB-2022-0001`..`0005`, `SCEN-001`..`013`, `SCEN-901`, `902`).
- [ ] Implement headless business proof runner script `tests/run_business_proof.py` ([business_test_cases.md §2](file:///d:/Code/uvest/business_test_cases.md#L41), [business_test_cases.md §7](file:///d:/Code/uvest/business_test_cases.md#L230)).
- [ ] Scaffold remaining 47 historical scenarios (`SCEN-014` through `SCEN-060`) into `tests/proof/scenario_manifest.json` using `tests/evals/scaffold_catalog.py` ([business_test_cases.md §3](file:///d:/Code/uvest/business_test_cases.md#L46)).
- [ ] Generate point-in-time fixtures and forward tapes in `tests/proof/fixtures/` for `SCEN-014` through `SCEN-060` ([business_test_cases.md §2](file:///d:/Code/uvest/business_test_cases.md#L25)).
- [ ] Update `tests/proof/manifest.sha256` checksums for all new fixtures.
- [ ] Populate reference metrics in `tests/proof/baseline_scorecard.json` (TP: 21, FP: 4, TN: 26, FN: 9, Brier $\le 0.180$, $\Delta E \ge +0.58R$, breaker rates 100%; [business_test_cases.md §5](file:///d:/Code/uvest/business_test_cases.md#L181)).
- [ ] Implement automated evaluation of Gates S1–S7 in `run_business_proof.py`:
  - Gate S1: Filter Delta (55% Gate: $P_{\text{win}} \ge 55\%$, win freq $>50\%$, $E_{\text{GO}} > E_{\text{baseline}}$; [business_test_cases.md §6](file:///d:/Code/uvest/business_test_cases.md#L216)).
  - Gate S2: Calibration (Brier $\le 0.180$, bucket error $\le 10\text{pp}$; [business_test_cases.md §6](file:///d:/Code/uvest/business_test_cases.md#L217)).
  - Gate S3: Per-Regime Honesty ($E_{\text{GO}} > 0$ across VIX and sector regimes; [business_test_cases.md §6](file:///d:/Code/uvest/business_test_cases.md#L218)).
  - Gate S4: AVOID Cost Accounting (FN rate $\le 20\%$, zero-regret classification for toxic winners; [business_test_cases.md §4.1](file:///d:/Code/uvest/business_test_cases.md#L129), [business_test_cases.md §6](file:///d:/Code/uvest/business_test_cases.md#L219)).
  - Gate S5: Stewardship Value-Add ($\ge 15\%$ drawdown reduction via soft exit; [business_test_cases.md §6](file:///d:/Code/uvest/business_test_cases.md#L220)).
  - Gate S6: No-Lookahead Integrity (100% fixture SHA verification, truncation at $T_{\text{rec}}$; [business_test_cases.md §6](file:///d:/Code/uvest/business_test_cases.md#L221)).
  - Gate S7: Marginal-Band Honesty ($55\%–62\%$ predicted band realizes $E \ge 0$; [business_test_cases.md §6](file:///d:/Code/uvest/business_test_cases.md#L222)).

---

## End-to-End Orchestration, Storage & Infrastructure
*Ref: [BRD.md §2](file:///d:/Code/uvest/BRD.md#L41), [BRD.md §8.1](file:///d:/Code/uvest/BRD.md#L271), [BRD.md §11](file:///d:/Code/uvest/BRD.md#L413), [prompt.md §2](file:///d:/Code/uvest/prompt.md#L29)*

- [x] Shared replay bundle recorder utility ([scripts/_recorder.py](file:///d:/Code/uvest/scripts/_recorder.py)).
- [x] Fast invariant test runner ([tests/run_all_tests.py](file:///d:/Code/uvest/tests/run_all_tests.py)).
- [x] Cross-document contract consistency enforcement ([tests/test_contract_consistency.py](file:///d:/Code/uvest/tests/test_contract_consistency.py)).
- [x] Repository hygiene test for `.gitignore` ([tests/test_repo_hygiene.py](file:///d:/Code/uvest/tests/test_repo_hygiene.py)).
- [x] Prompt integrity tests ([tests/test_prompt_integrity.py](file:///d:/Code/uvest/tests/test_prompt_integrity.py)).
- [x] Pre-commit hook script ([githooks/pre-commit](file:///d:/Code/uvest/githooks/pre-commit), [scripts/setup_hooks.sh](file:///d:/Code/uvest/scripts/setup_hooks.sh)).
- [x] The hook runs the suite on the **repo's** interpreter: it prefers `.venv` (`Scripts/` on Windows, `bin/` elsewhere), falls back to `python`, and prints which one it used. It used to call bare `python`, so the runtime that decided whether a commit shipped was whichever one a shell put first on PATH — a system 3.13 without `fastdtw` failed a *green* tree at `test_3_dtw_pattern` while CI's pinned 3.11 stayed green ([githooks/pre-commit](file:///d:/Code/uvest/githooks/pre-commit)).
- [x] GitHub Actions workflow ([.github/workflows/uvest.yml](file:///d:/Code/uvest/.github/workflows/uvest.yml)).
- [x] Stage-to-stage field contract graded in the suite ([tests/test_chain_contract.py](file:///d:/Code/uvest/tests/test_chain_contract.py)): every chain stage declares `REQUIRED_FIELDS`, and the seam checks both directions — stage 0's real payload satisfies each declaration **present and non-null** (key presence alone is exactly what let `recommended_date: null` reach stage 12), and deleting a declared field makes that stage refuse, so a row is a requirement in the code and not a hopeful comment. Both halves read the stages' own constants, so the contract cannot drift from the code that enforces it.
- [x] The runner publishes an artifact only when its stage succeeded: stdout streams into a `.{artifact}.partial` file that is `os.replace`d into place on exit 0 and deleted otherwise, so a refusal no longer leaves a 0-byte `current_trade.json` / `out_*.json` behind for a later reader to mistake for a payload (the run record already refused to claim those bytes — the filesystem now agrees). The artifact table is *derived* from `_recorder.FROZEN_ARTIFACTS` for implemented stages, with `PENDING_ARTIFACTS` holding the BRD §9.1 names of stages not built yet: the hand-copied table a test merely policed is gone ([scripts/run_pipeline.py](file:///d:/Code/uvest/scripts/run_pipeline.py), seams in [tests/test_run_pipeline.py](file:///d:/Code/uvest/tests/test_run_pipeline.py)).
- [x] Baseline scoring weights made reviewable: `thresholds.example.json` states stage 9's frozen `module_weights` — the 100-point model, pinned by test to the code's own defaults — so a reviewer or a fresh clone can diff the numbers a verdict is scored with. `thresholds.json` itself stays **untracked**: the old open item asked for a committed `thresholds.json`, which two frozen rules forbid — it is generated per-run state ([tests/test_repo_hygiene.py](file:///d:/Code/uvest/tests/test_repo_hygiene.py)) and stage 11 is its sole writer (BRD §9.1). Copy the template into a workdir to pin or retune ([BRD.md §10.2](file:///d:/Code/uvest/BRD.md#L363), [BRD.md §12.2](file:///d:/Code/uvest/BRD.md#L510)).
- [ ] Create environment variables template file `.env.example` in repository root for Dhan credentials ([BRD.md §8.1](file:///d:/Code/uvest/BRD.md#L272)).
- [ ] Formalize SQLite schema definitions & table migrations for `uvest_history.db` (`trades`, `triage_history`, `autopsy_records` tables; [BRD.md §11](file:///d:/Code/uvest/BRD.md#L416), [BRD.md §12.2](file:///d:/Code/uvest/BRD.md#L512)).
- [ ] Formalize SQLite schema definitions for `dhan_cache.db` (`market_cache` table with 15-minute TTL; [BRD.md §8.1](file:///d:/Code/uvest/BRD.md#L276)).
- [x] End-to-end pipeline runner CLI script to execute the sequential chain $0 \to 12$ in one invocation ([prompt.md §2](file:///d:/Code/uvest/prompt.md#L29); [scripts/run_pipeline.py](file:///d:/Code/uvest/scripts/run_pipeline.py), seam [tests/test_run_pipeline.py](file:///d:/Code/uvest/tests/test_run_pipeline.py)).
- [x] Prove the chain-abort contract for **every** stage: a non-zero exit from any chain stage stops the 0 → 12 walk and the run returns *that stage's own* code, with every later stage `not_reached`, no downstream artifact, no report, and no byte claim from the failed stage. Two layers — real failures through the spawned runner (stage 0's options gate exit 2, stage 1's contradictory stoploss exit 1) and an injected **distinct** exit code per chain stage, so a runner that always returned 1 would fail stage by stage. Stage 11 is outside by contract (conditional, always disclosed as skipped); `test_absent_evidence_is_disclosed_not_an_abort` pins the boundary that a corrupt fixture is absent evidence, never an abort ([tests/test_run_pipeline.py](file:///d:/Code/uvest/tests/test_run_pipeline.py), [docs/agents/evals.md §2.1](file:///d:/Code/uvest/docs/agents/evals.md#L21)).
