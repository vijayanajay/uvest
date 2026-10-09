# UVEST Implementation Tickets Index
*Sliced via /to-tickets from Frozen BRD.md*

| Ticket ID | Title | Status | Test Seam | Priority |
| :--- | :--- | :--- | :--- | :--- |
| `TICK-001` | [Canonical Ingestion Parser & Gatekeeper](file:///d:/Code/uvest/docs/tickets/TICK-001-canonical-parser.md) | FROZEN | `tests/test_0_ingest.py` | P0 |
| `TICK-002` | [Mathematical R:R, Slippage & Margin Engine](file:///d:/Code/uvest/docs/tickets/TICK-002-math-and-rr.md) | FROZEN | `tests/test_1_math_and_rr.py` | P0 |
| `TICK-003` | [FastDTW 10-Year Historical Pattern Matcher](file:///d:/Code/uvest/docs/tickets/TICK-003-dtw-matcher.md) | FROZEN | `tests/test_3_dtw.py` | P1 |
| `TICK-004` | [LightGBM 1-Month Direction & Volatility Cone](file:///d:/Code/uvest/docs/tickets/TICK-004-lightgbm-cone.md) | FROZEN | `tests/test_4_ml_direction.py` | P1 |
| `TICK-005` | [NSE Bhavcopy Delivery % × Futures OI Matrix](file:///d:/Code/uvest/docs/tickets/TICK-005-institutional-matrix.md) | FROZEN | `tests/test_5_flow.py` | P0 |
| `TICK-006` | [Fundamental & Corporate Governance Auditor](file:///d:/Code/uvest/docs/tickets/TICK-006-fundamental-audit.md) | FROZEN | `tests/test_6_fundamental.py` | P1 |
| `TICK-007` | [Sector Relative Strength & Macro VIX Regime](file:///d:/Code/uvest/docs/tickets/TICK-007-sector-macro.md) | FROZEN | `tests/test_7_regime.py` | P1 |
| `TICK-008` | [Dhan API Precision Intraday & Depth Client](file:///d:/Code/uvest/docs/tickets/TICK-008-dhan-precision.md) | FROZEN | `tests/test_8_dhan.py` | P1 |
| `TICK-009` | [Consolidated Multi-Factor Decision Engine](file:///d:/Code/uvest/docs/tickets/TICK-009-decision-engine.md) | FROZEN | `tests/test_9_decision.py` | P0 |
| `TICK-010` | [Standalone Interactive HTML Visualizer](file:///d:/Code/uvest/docs/tickets/TICK-010-html-visualizer.md) | FROZEN | `tests/test_12_html.py` | P1 |
| `TICK-011` | [Daily Position Triage & Adverse Movement Tracker](file:///d:/Code/uvest/docs/tickets/TICK-011-daily-triage.md) | FROZEN | `tests/test_10_triage.py` | P1 |
| `TICK-012` | [Continuous Learning & Failure Autopsy Engine](file:///d:/Code/uvest/docs/tickets/TICK-012-failure-autopsy.md) | FROZEN | `tests/test_11_autopsy.py` | P2 |
| `EVAL-001` | [Behavioral Evals Benchmark Suite (50 Trades)](file:///d:/Code/uvest/docs/tickets/EVAL-001-benchmark-suite.md) | FROZEN | `tests/run_evals.py` | P0 |

---

## The Spec-and-Freeze Milestone
All architectural requirements in `BRD.md`, domain models in `docs/agents/domain.md`, evaluation gates in `docs/agents/evals.md`, and ticket acceptance criteria above are **LOCKED AND FROZEN**. 

Strict TDD (writing failing test seam first, then implementing minimum code) commences ticket-by-ticket once approved.
