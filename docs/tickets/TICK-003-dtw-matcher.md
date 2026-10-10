# TICK-003: FastDTW 10-Year Historical Pattern Matcher

**Status:** FROZEN
**Priority:** P1
**Test Seam:** `tests/test_3_dtw_pattern.py`
**Target Script:** `scripts/3_dtw_pattern.py`
**Output Artifact:** `out_3_dtw.json`
**Pipeline Contract:** `BRD.md` §9.1 (stage 3)

### 1. Goal & Context

Stage 3 is the **DTW historical analog engine**: it warps the last 30 trading
days of the recommendation's price action against the last 10 years of daily
closes and answers one question — *when this exact shape appeared before, what
happened next?* Its three forward metrics feed the "Quant & ML Edge" scoring
block (25 pts) and the directional calibration eval (evals.md §3 #4).

**Deliberately out of scope (do not build here):**
- LightGBM probability/cone regression → stage 4 (`4_ml_direction.py`).
- Intraday/anchored VWAP geometry → stage 8 (`8_dhan_precision.py`).
- Sector/nifty regime colouring → stage 7.

### 2. Frozen Specification Reference

- `BRD.md` §9.1 stage 3 (contract row) and §2 / §5.1 (DTW matcher spec).
- §9 Script Inventory payload row for `3_dtw_pattern.py`: `dtw_win_rate_22d`,
  `dtw_median_return`, `dtw_mae_drawdown`, `analog_confidence`.
- `requirements.txt` pin: `fastdtw==0.3.4` (with `scipy` distance metric).
- `BRD.md` §10.3 Waiver Rule: `null`s, never fabricated defaults.
- `BRD.md` §16 worked example symbols (`RELIANCE`, 2026-10-09 bundle).

**Canonical output contract** (frozen here; mirrors §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `window_30d` | list[dict] | The 30 closes fed to DTW: `{"date", "close", "z"}` |
| `analog_dates` | list[str] | Start dates of the K nearest historical analogs |
| `analog_z_paths` | list[list[float]] | Each analog's Z-scored 30-day window |
| `analog_forward_returns` | list[float] | Analog forward 22d raw returns (0–1 fraction) |
| `dtw_win_rate_22d` | float \| null | Fraction of analogs up > 4% within 22 bars |
| `dtw_median_return` | float \|null | Median forward 22d return across analogs |
| `dtw_mae_drawdown` | float \| null | Median max-adverse-excursion across analogs |
| `analog_confidence` | float \| null | 1 / (1 + median DTW distance), ∈ (0, 1] |
| `dtw_distance` | float | Median DTW distance across the K nearest analogs |
| `k_analogs` | int | K actually matched (≤ K_MAX when history is shorter) |
| `waived` | bool | `true` when OHLCV history was unavailable or too short |
| `as_of` | str \| null | Last completed session date of the (source) history |

### 3. Frozen Algorithmic Conventions

The seam asserts exact numbers from a *recorded* fixture; these conventions
must stay bit-stable:

- `WINDOW = 30`, `K_MAX = 15`, `FORWARD = 22`, `WIN_RATE_UP = 0.04`.
- Normalize each window with **population** Z-score (`σ` denominator `N`).
- `fastdtw.fastdtw(x, y, radius=1)` with the library-default scalar |Δ|
  cost; distance is the *raw* constrained DTW sum.
- Ranking = ascending raw DTW distance; ties broken by more-recent analog
  first (stable ordering; a deterministic `key=(distance, −index)` sort).
- `dtw_win_rate_22d` counts analogs whose forward 22-day return exceeds
  `+4%` **strictly** (boundary analogue of stage 2's `<` 0.75 gate).
- `dtw_median_return` is the arithmetic median of the analogs' forward
  22-day returns; `dtw_mae_drawdown` is the median **post-entry** MAE —
  the deepest low over entry bar + forward 22 bars, relative to the
  analog's entry close, as a non-negative fraction.

### 4. TDD Acceptance Criteria

- [ ] Reads the canonical `current_trade.json` (symbol + provenance); it
      never fabricates prices of its own.
- [ ] Uses `fastdtw` for the constrained DTW distance — no hand-rolled
      quadratic recursion (YAGNI + pinned dependency contract).
- [ ] Duck-typed: works equally on daily OHLCV bundles (`tests/fixtures/
      RELIANCE_20261009`) and close-only 10-year history fixtures.
- [ ] **Zero network calls**: quality comes from `tests/fixtures/
      reliance_history_10y.json` (real downloaded closes, recorded once).
- [ ] `dtw_win_rate_22d` is exactly the fraction ≥ `+4%` among
      `analog_forward_returns` — self-consistent by construction.
- [ ] Boundary case: an analog return *exactly* `+4%` does **not** count
      toward the win rate (strict `>`, mirroring stage 2's strict gate).
- [ ] **Waiver path:** missing/short history → every metric `null`,
      `waived: true`, exit 0. Never a fabricated `false`/`0.0` (§10.3).
- [ ] Missing `live_price` in `current_trade.json` → exit 2 with
      `ERR_MISSING_LIVE_PRICE` (mirrors stages 0/2 conventions).
- [ ] Emits `as_of` provenance so every reported rate traces to a dated
      local payload (Zero Hallucination, evals.md §3 #1).
- [ ] Byte-identical payload for repeated runs over the same fixture.
- [ ] `tests/run_evals.py` stays green (Brier gate remains `[SKIP]` until
      stage 4 supplies calibrated probabilities).

### 5. Implementation Checklist

- [ ] Write the failing seam in `tests/test_3_dtw_pattern.py` first (`/tdd`).
- [ ] Implement the minimum code in `scripts/3_dtw_pattern.py`.
- [ ] Register `out_3_dtw.json` in the shared recorder helper's FROZEN_ARTIFACTS
      map (the stage-3 contract row).
- [ ] Verify `tests/test_contract_consistency.py` stays green.
- [ ] Once ≥ 15 golden trades carry `out_3_dtw.json` payloads, extend
      `tests/run_evals.py` gate 4 to consume real DTW rows.
