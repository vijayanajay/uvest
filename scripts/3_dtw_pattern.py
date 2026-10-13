#!/usr/bin/env python3
"""UVEST stage 3 — FastDTW Historical Pattern Matcher (TICK-003).

Sole owner of the DTW analog engine (BRD §9.1 row 3, §5.1). Consumes the
canonical `current_trade.json`, warps the trade's last 30 closes against
10 years of the same symbol's daily history, and lets the *15 nearest analogs*
answer the only question this stage owns:

    every time this exact price shape printed, what did the next 22
    trading days actually deliver?

Answers are reported as the ticket's four forward metrics; they feed the
"Quant & ML Edge" scoring block and the directional-calibration eval
(evals.md §3 #4).

**Frozen algorithmic conventions (TICK-003 §3 — tests assert exact values):**
- WINDOW = 30, K_MAX = 15, FORWARD = 22, WIN_RATE threshold +4% (strict `>`).
- Z-score each window with **population** σ (÷ N), never sample (÷ N−1).
- Distance is the library-default `fastdtw(x, y, radius=1)` — the
  constrained DTW path-sum of scalar |Δ| terms. (FastDTW's optional
  p-norm `dist=2` degenerates to `abs` on scalar differences, so the
  default cost is both the canonical Sakoe-Chiba sum and the fast path.)
- Ranking = ascending raw DTW distance; ties broken nearest-in-history
  first (deterministic `(distance, index)` sort key).
- `dtw_mae_drawdown` is the median of the analogs' **post-entry** maximum
  adverse excursion: the deepest low over the analog's entry bar +
  forward 22-bar window, relative to the entry close (the analog shape's
  last close), as a non-negative fraction.
- `analog_confidence = 1 / (1 + median DTW distance)` ∈ (0, 1]: bounded,
  strictly positive, and drifting *down* as the current shape loses its
  historical resonance.

**Data policy (ADR-003, Tier-1 free-only):** in production the 10-year
close series comes from yfinance. For the recorded-bundle architecture the
repo runs on, `--history-fixture` points at a close-only bundle
(`tests/fixtures/reliance_history_10y.json`); without it the stage honors
the BRD §10.3 waiver path — all-null payload, exit 0, never a fabricated
`false`/`0.0` (Zero Hallucination).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import numpy as np
from fastdtw import fastdtw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recorder import add_record_arg, record_artifact, resolve_bundle_dir  # noqa: E402

ERR_PARSE = "ERR_PARSE"
ERR_MISSING_LIVE_PRICE = "ERR_MISSING_LIVE_PRICE"

REQUIRED_FIELDS = ["live_price", "stoploss"]
WINDOW = 30
K_MAX = 15
FORWARD = 22
WIN_RATE_UP = 0.04  # strict `>` — exactly +4% does NOT count as a win
# Sweep floor: a smaller history cannot produce K_MAX distinct non-null
# analogs without pooling stale windows, which is exactly the kind of
# fabrication the waiver rule exists to prevent.
MIN_HISTORY = WINDOW + FORWARD + K_MAX + 5  # 72 sessions minimum

ERR_MISSING_HISTORY_HINT = (
    "--history-fixture not passed; stage 3 cannot sweep without it "
    "(production fetches via yfinance — see BRD §8 Tier-1)"
)


def die(code: str, exit_code: int, hint: str = "") -> None:
    print(f"{code}: {hint}" if hint else code, file=sys.stderr)
    sys.exit(exit_code)


def load_payload(path: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(ERR_PARSE, 1, f"input file not found: {path}")
    except json.JSONDecodeError as exc:
        die(ERR_PARSE, 1, f"input is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        die(ERR_PARSE, 1, "input must be a JSON object")
    if "live_price" not in payload:
        die(ERR_MISSING_LIVE_PRICE, 2, "live_price is required for direction context")
    missing = [k for k in REQUIRED_FIELDS if k not in payload]
    if missing:
        die(ERR_PARSE, 1, f"missing required field(s): {', '.join(missing)}")
    return payload


def load_history(path: str | None) -> list[dict] | None:
    """Load the recorded close-only fixture; None → waiver path.

    Tri-state waiver, as stage 2 and stages 5/6 freeze it: omitted, missing,
    unreadable, malformed or too short all mean the history evidence is absent —
    disclosed `waived: true`, never a traceback that aborts the chain.
    """
    if not path:
        return None
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    sessions = raw.get("sessions") or []
    if len(sessions) < MIN_HISTORY:
        return None
    return sessions


def is_forward_win(forward_return: float) -> bool:
    """Strict > +4% frozen convention.

    Boundary discipline (frozen here exactly as stage 2 froze ratio<0.75):
    an analog that returned *exactly* +4% must NOT count as a win, or the
    gate would fire on the equality edge and inflate the win rate.
    """
    return forward_return > WIN_RATE_UP


def zscore(window: np.ndarray) -> np.ndarray:
    """Population σ Z-score (frozen convention, ÷ N)."""
    mu = window.mean()
    sigma = window.std()  # numpy default ddof=0 → population
    if not np.isfinite(sigma) or sigma == 0:
        raise ValueError(f"degenerate window (σ={sigma}) — not Z-scorable")
    return (window - mu) / sigma


def dtw_distance(x: np.ndarray, y: np.ndarray) -> float:
    """Constrained DTW distance via fastdtw, library-default scalar |Δ| cost."""
    distance, _ = fastdtw(x, y)
    return float(distance)


def compute(payload: dict, history: list[dict] | None) -> dict:
    out: dict = {
        "window_30d": None, "analog_dates": None, "analog_z_paths": None,
        "analog_forward_returns": None,
        "dtw_win_rate_22d": None, "dtw_median_return": None,
        "dtw_mae_drawdown": None, "analog_confidence": None,
        "dtw_distance": None, "k_analogs": 0,
        "waived": history is None, "as_of": None,
    }
    if history is None:
        return out

    closes_all = [float(row["close"]) for row in history]
    dates_all = [row["date"] for row in history]
    closes = np.asarray(closes_all, dtype=float)

    xz = zscore(closes[-WINDOW:])
    current_window = [
        {"date": dates_all[len(closes_all) - WINDOW + i], "close": closes[-WINDOW + i],
         "z": float(xz[i])}
        for i in range(WINDOW)
    ]

    candidates: list[tuple[float, int]] = []
    max_start = len(closes) - WINDOW - FORWARD  # analog needs a full forward window
    step = 1
    for start in range(0, max_start + 1, step):
        y_window = closes[start:start + WINDOW]
        yz = zscore(y_window)
        d = dtw_distance(xz, yz)
        candidates.append((d, start))

    # Final deterministic ranking: distance, then nearest-in-history first.
    candidates.sort(key=lambda t: (t[0], t[1]))
    analogs = candidates[:K_MAX]

    analog_dates: list[str] = []
    analog_z_paths: list[list[float]] = []
    analog_forward_returns: list[float] = []
    distances: list[float] = []
    maes: list[float] = []
    for d, start in analogs:
        analog_z_paths.append([round(float(v), 6) for v in zscore(closes[start:start + WINDOW])])
        analog_dates.append(dates_all[start])
        entry_idx = start + WINDOW - 1  # the analog shape's last close = entry
        entry_close = closes_all[entry_idx]
        exit_close = closes_all[entry_idx + FORWARD]
        analog_forward_returns.append(exit_close / entry_close - 1.0)
        # Post-entry MAE: deepest low of the analog's forward 22-bar window
        # (inclusive of the entry bar) relative to the entry close.
        forward_slice = closes_all[entry_idx:entry_idx + FORWARD + 1]
        forward_low = min(forward_slice)
        maes.append(max(0.0, 1.0 - forward_low / entry_close))
        distances.append(d)

    med_distance = statistics.median(distances)
    out.update({
        "window_30d": current_window,
        "analog_dates": analog_dates,
        "analog_z_paths": analog_z_paths,
        "analog_forward_returns": [round(r, 6) for r in analog_forward_returns],
        "dtw_win_rate_22d": sum(1 for r in analog_forward_returns if is_forward_win(r)) / len(analog_forward_returns),
        "dtw_median_return": round(statistics.median(analog_forward_returns), 6),
        "dtw_mae_drawdown": round(statistics.median(maes), 6),
        "analog_confidence": round(1.0 / (1.0 + med_distance), 6),
        "dtw_distance": round(med_distance, 6),
        "k_analogs": len(analogs),
        "waived": False,
        "as_of": dates_all[-1],
    })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 3: DTW historical analog engine")
    parser.add_argument("--input", default="current_trade.json",
                        help="path to canonical current_trade.json")
    parser.add_argument("--history-fixture", default=None,
                        help="recorded close-only history bundle (offline mode); omit for waiver path")
    add_record_arg(parser)
    args = parser.parse_args()

    payload = load_payload(args.input)
    history = load_history(args.history_fixture)

    stdout_text = json.dumps(compute(payload, history), indent=2) + "\n"

    bundle = resolve_bundle_dir(args.record, payload.get("symbol") or "UNSPECIFIED",
                                payload.get("recommended_date"))
    if bundle is not None:
        record_artifact(bundle, 3, stdout_text)

    sys.stdout.write(stdout_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
