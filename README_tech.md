# 🛡️ UVEST Technical Manual (`README_tech.md`)

This document serves as the technical maintenance and operations manual for the UVEST Quantitative & Fundamental Validation Platform. It is intended for software engineers, maintainers, and quantitative analysts interacting with the underlying architecture.

---

## 🏗️ Architecture Overview

UVEST operates under a strict philosophy inspired by Kailash Nadh (CTO, Zerodha):
- **Local-First & Zero-Cloud Bloat:** No distributed queues or external databases. Uses SQLite3 (`uvest_history.db`, `dhan_cache.db`), flat JSON contracts, and Python 3.11+ standard libraries.
- **Zero Hallucination:** Every metric, ratio, and win rate output to the user must be deterministically traceable back to local JSON payloads (`out_*.json`) executed by specific scripts. LLMs are strictly used for orchestration and parsing, never for computation.
- **YAGNI (You Aren't Gonna Need It):** Simple, boring, mathematically sound technology wins.

### Tiered Data Hierarchy
To guarantee reliability and minimize overhead, data ingestion is strictly tiered:
1.  **Tier 1 (Free Public Data):** Always queried first. Includes NSE Bhavcopy (delivery, futures OI), Yahoo Finance (`yfinance` for daily OHLCV), and Screener.in/NSE Corporate Filings.
2.  **Tier 2 (Dhan API Precision Fallback):** Invoked *strictly* when high-resolution intraday precision (5m/10m candles, live market depth, anchored VWAP) is required.

---

## ⚙️ The Execution Pipeline

The core engine is a sequence of modular, single-responsibility Python scripts. They must run in ascending canonical order (0 $\to$ 12). Each script reads a prior state, performs calculations, and dumps output to a specific JSON artifact.

| Stage | Script | Primary Responsibility | Key Output Artifact |
| :--- | :--- | :--- | :--- |
| **0** | `0_ingest_reco.py` | Parses raw text/OCR, enforces options bans, and checks NSE F&O bans. | `current_trade.json` |
| **1** | `1_math_and_rr.py` | Computes live vs nominal R:R, slippage penalty, and position sizing. | `out_1_math.json` |
| **2** | `2_market_data.py` | Fetches daily OHLCV, 14D ATR, EMAs, and determines volatility noise traps. | `out_2_market.json` |
| **3** | `3_dtw_pattern.py` | Dynamic Time Warping against 10-yr NSE history to find analogs. | `out_3_dtw.json` |
| **5** | `5_institutional_flow.py` | Analyzes NSE Bhavcopy for Delivery % surges and Futures OI buildup. | `out_5_flow.json` |
| **6** | `6_fundamental_audit.py` | Audits Piotroski F-Score, Altman Z, and critical promoter pledging flags. | `out_6_fundamental.json` |
| **9** | `9_decision_engine.py` | Synthesizes all prior outputs into a 100-point score and assigns the final verdict. | `out_9_decision.json` |
| **12** | `12_generate_html_report.py` | Renders the final interactive HTML visualizer using vanilla JS/CSS. | `reports/report_*.html` |

*(Note: Stages 4, 7, 8, 10, and 11 represent planned quantitative expansions detailed in the BRD but focus on the active execution path here).*

---

## 🛑 Hard Circuit Breakers

The `9_decision_engine.py` enforces fatal tail-risk vetos. If any of these are tripped, the system issues an immediate **🔴 AVOID**:
1.  **Promoter Pledging $> 25\%$:** Extreme risk of lender margin calls.
2.  **Options Contracts:** CE/PE instruments are explicitly banned due to theta decay risk.
3.  **Inverted Risk-Reward:** $R:R < 1.0$ at market entry (flips to `WAIT` if limit order is viable).
4.  **Binary Earnings Events:** Earnings scheduled $< 7$ days out for Futures contracts.
5.  **NSE F&O Ban:** Trading restricted by the exchange.

---

## 🧪 Evals & Business Proof Suite

UVEST utilizes strict Test-Driven Development (TDD) combined with a Behavioral Evaluations benchmark to prevent regression.

*   **Behavioral Evals (`tests/run_evals.py`):** Ensures zero hallucination, validates that price cone coverage $\ge 80\%$, and confirms that hard circuit breakers block 100% of defined hazards against a golden dataset (`tests/evals/golden_dataset.json`).
*   **Business Proof Suite (`business_test_cases.md`):** A frozen 62-scenario historical catalog of real NSE market stress events (2018–2026). Every codebase change must execute this suite headlessly and prove that the overall system expectancy ($\Delta E$) improves or stays stable without degrading safety gates.

### Running the Test Suite
Maintainers must run the core test suite to verify pipeline integrity:
```bash
python tests/run_all_tests.py
```
This runs invariant tests, contract consistency checks, and prompt integrity verifications.

---

## 🛠️ Maintenance & The Matt Pocock Skills Lifecycle

This repository strictly enforces the AI Hero / Matt Pocock skills setup for development. **Vibe coding is prohibited.**

When adding new features or adjusting weights:
1.  **`/grill-with-docs`**: Interrogate the assumption against Indian market mechanics.
2.  **`/to-spec`**: Freeze the architecture and schema changes in `BRD.md` and ADRs. **No code begins until specs are frozen.**
3.  **`/to-tickets`**: Break work into vertical tickets inside `docs/tickets/`.
4.  **`/evals`**: Define behavioral benchmarks before coding.
5.  **Strict TDD (`/tdd`)**: Write a failing test seam first.
6.  **`/code-review`**: Audit against the Kailash Nadh simplicity standards.
