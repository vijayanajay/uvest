# UVEST Implementation Tickets Index
*Sliced via /to-tickets from Frozen BRD.md*

> **Pipeline contract:** `BRD.md` §9.1 is the single source of truth for script names, output artifacts, test seams, and execution order. Every seam below is mechanically derived as `tests/test_{script stem}.py` — never hand-assigned. `tests/test_contract_consistency.py` fails the suite if this index, the BRD contract, `prompt.md` §2, or a ticket file drifts apart.

| Ticket ID | Title | Status | Test Seam | Priority |
| :--- | :--- | :--- | :--- | :--- |
| `TICK-001` | [Canonical Ingestion Parser & Gatekeeper](file:///d:/Code/uvest/docs/tickets/TICK-001-canonical-parser.md) | FROZEN (sliced) | `tests/test_0_ingest_reco.py` | P0 |
| `TICK-002` | [Mathematical R:R, Slippage & Margin Engine](file:///d:/Code/uvest/docs/tickets/TICK-002-math-and-rr.md) | FROZEN (sliced) | `tests/test_1_math_and_rr.py` | P0 |
| `TICK-003` | [FastDTW 10-Year Historical Pattern Matcher](file:///d:/Code/uvest/docs/tickets/TICK-003-dtw-matcher.md) | FROZEN (sliced) | `tests/test_3_dtw_pattern.py` | P1 |
| `TICK-004` | [LightGBM 1-Month Direction & Volatility Cone](file:///d:/Code/uvest/docs/tickets/TICK-004-lightgbm-cone.md) | FROZEN (sliced) | `tests/test_4_ml_direction.py` | P1 |
| `TICK-005` | [NSE Bhavcopy Delivery % × Futures OI Matrix](file:///d:/Code/uvest/docs/tickets/TICK-005-institutional-matrix.md) | FROZEN (sliced) | `tests/test_5_institutional_flow.py` | P0 |
| `TICK-006` | [Fundamental & Corporate Governance Auditor](file:///d:/Code/uvest/docs/tickets/TICK-006-fundamental-audit.md) | FROZEN (sliced) | `tests/test_6_fundamental_audit.py` | P1 |
| `TICK-007` | [Sector Relative Strength & Macro VIX Regime](file:///d:/Code/uvest/docs/tickets/TICK-007-sector-macro.md) | FROZEN (sliced) | `tests/test_7_sector_macro_regime.py` | P1 |
| `TICK-008` | [Dhan API Precision Intraday & Depth Client](file:///d:/Code/uvest/docs/tickets/TICK-008-dhan-precision.md) | FROZEN (sliced) | `tests/test_8_dhan_precision.py` | P1 |
| `TICK-009` | [Consolidated Multi-Factor Decision Engine](file:///d:/Code/uvest/docs/tickets/TICK-009-decision-engine.md) | FROZEN (sliced) | `tests/test_9_decision_engine.py` | P0 |
| `TICK-010` | [Standalone Interactive HTML Visualizer](file:///d:/Code/uvest/docs/tickets/TICK-010-html-visualizer.md) | FROZEN (sliced) | `tests/test_12_generate_html_report.py` | P1 |
| `TICK-011` | [Daily Position Triage & Adverse Movement Tracker](file:///d:/Code/uvest/docs/tickets/TICK-011-daily-triage.md) | FROZEN (sliced) | `tests/test_10_daily_triage.py` | P1 |
| `TICK-012` | [Continuous Learning & Failure Autopsy Engine](file:///d:/Code/uvest/docs/tickets/TICK-012-failure-autopsy.md) | FROZEN (sliced) | `tests/test_11_postmortem_learning.py` | P2 |
| `TICK-013` | [Daily OHLCV, ATR & Trend Regime Engine](file:///d:/Code/uvest/docs/tickets/TICK-013-market-data.md) | FROZEN (sliced) | `tests/test_2_market_data.py` | P1 |
| `EVAL-001` | [Behavioral Evals Benchmark Suite (50 Trades)](file:///d:/Code/uvest/docs/tickets/EVAL-001-benchmark-suite.md) | FROZEN (sliced) | `tests/run_evals.py` | P0 |
| `EVAL-002` | [Hazard Boundary-Probe Rows (Pinned Evidence)](file:///d:/Code/uvest/docs/tickets/EVAL-002-hazard-boundary-probes.md) | FROZEN (sliced) | `tests/test_seed_hazard_records.py` | P1 |

---

## Ticket-to-Stage Mapping

Ticket IDs were assigned in **slicing order**; stage numbers encode **execution order**. They deliberately diverge for the closing stages — do not assume `TICK-00N` implies stage `N`.

| Ticket | Stage | Script |
| :--- | :--- | :--- |
| `TICK-001` | 0 | `scripts/0_ingest_reco.py` |
| `TICK-002` | 1 | `scripts/1_math_and_rr.py` |
| `TICK-003` | 3 | `scripts/3_dtw_pattern.py` |
| `TICK-004` | 4 | `scripts/4_ml_direction.py` |
| `TICK-005` | 5 | `scripts/5_institutional_flow.py` |
| `TICK-006` | 6 | `scripts/6_fundamental_audit.py` |
| `TICK-007` | 7 | `scripts/7_sector_macro_regime.py` |
| `TICK-008` | 8 | `scripts/8_dhan_precision.py` |
| `TICK-009` | 9 | `scripts/9_decision_engine.py` |
| `TICK-010` | 12 | `scripts/12_generate_html_report.py` |
| `TICK-011` | 10 | `scripts/10_daily_triage.py` |
| `TICK-012` | 11 | `scripts/11_postmortem_learning.py` |
| `TICK-013` | 2 | `scripts/2_market_data.py` |
| `EVAL-001` | — | `tests/run_evals.py` (harness) |
| `EVAL-002` | — | `tests/run_evals.py` (hazard census) |

**Stage coverage:** every stage `0 → 12` in `BRD.md` §9.1 now has an owning ticket. `TICK-013` was sliced last (hence the highest ID) to claim stage 2, which is why ticket IDs and stage numbers must never be assumed to correspond.

---

## Slice Status

- **Sliced** — all ticket files are committed in `docs/tickets/`: `TICK-001` through `TICK-013`, `EVAL-001`, `EVAL-002`.
- **Pending** — none; all pipeline stages are formally specced, sliced, and ready for TDD implementation.

## The Spec-and-Freeze Milestone
All architectural requirements in `BRD.md`, domain models in `docs/agents/domain.md`, evaluation gates in `docs/agents/evals.md`, and ticket acceptance criteria above are **LOCKED AND FROZEN**.

Strict TDD (writing failing test seam first, then implementing minimum code) commences ticket-by-ticket once approved.
