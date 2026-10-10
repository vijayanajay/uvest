#!/usr/bin/env python3
"""UVEST stage 2 — Daily OHLCV, ATR & Trend Regime Engine (TICK-013).

Sole owner of price-derived volatility and trend state (BRD §9.1 stage 2).
Consumes `current_trade.json`, loads daily OHLCV, and emits the volatility
buffer that gates the recommendation — principally the 0.75×ATR noise-stop
circuit breaker (BRD §10.2 #5):

    is_tight_sl is TRUE  iff  sl_atr_ratio < 0.75   (strict inequality;
    ratio exactly 0.75 is NOT tight — a gate that fires on the boundary
    would reject valid trades)

`is_tight_sl` is **tri-state** (BRD §10.3 Waiver Rule): `null` when OHLCV is
unavailable or too short (< 200 completed sessions). Missing data never
inflates and never penalises — the stage never fabricates a default `false`.

**Data policy (ADR-003, Tier-1 free-only):** in production this stage fetches
yfinance daily bars. For deterministic offline operation — the recorded-bundle
architecture this repo runs on — `--ohlcv-fixture` points at a recorded daily
OHLCV file (`tests/fixtures/reliance_daily_ohlcv.json`); without it the stage
honors the waiver path (all-null payload, exit 0). Tier-2 boundaries (intraday
5m/10m, order depth) belong to stage 8 and are never requested here.

**Frozen indicator conventions (TICK-013 §2, tests depend on exact values):**
- TR_t = max(H_t − L_t, |H_t − C_{t−1}|, |L_t − C_{t−1}|)
- atr_14 = simple arithmetic mean of the last 14 true ranges (no Wilder)
- EMA α = 2/(N+1), seeded with the SMA of the first N closes
- Bollinger σ over 20 closes is population (÷ N), not sample (÷ N−1)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recorder import add_record_arg, record_artifact, resolve_bundle_dir  # noqa: E402

ERR_PARSE = "ERR_PARSE"
ERR_MISSING_LIVE_PRICE = "ERR_MISSING_LIVE_PRICE"

REQUIRED_FIELDS = ["live_price", "stoploss"]
MIN_SESSIONS = 200  # frozen: below this the stage is waived, never guessed
ATR_PERIOD = 14
ATR_NOISE_GATE = 0.75  # strict — the BRD §10.2 #5 boundary


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
        die(ERR_MISSING_LIVE_PRICE, 2, "live_price is required for sl_atr_ratio")
    missing = [k for k in REQUIRED_FIELDS if k not in payload]
    if missing:
        die(ERR_PARSE, 1, f"missing required field(s): {', '.join(missing)}")
    return payload


def load_ohlcv(path: str | None) -> list[dict] | None:
    """Load the recorded daily-OHLCV fixture, or None on absence (waiver)."""
    if not path:
        return None
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    sessions = raw.get("sessions") or []
    if len(sessions) < MIN_SESSIONS:
        return None
    return sessions


def true_ranges(sessions: list[dict]) -> list[float]:
    """TR_t = max(H−L, |H−C_prev|, |L−C_prev|); first row has no C_prev."""
    trs: list[float] = []
    prev_close: float | None = None
    for row in sessions:
        h, l = float(row["high"]), float(row["low"])
        if prev_close is None:
            trs.append(h - l)
        else:
            trs.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
        prev_close = float(row["close"])
    return trs


def ema(values: list[float], period: int) -> float:
    """α = 2/(N+1), seeded with the SMA of the first `period` values."""
    if len(values) < period:
        raise ValueError(f"EMA needs ≥ {period} values, got {len(values)}")
    alpha = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out = seed
    for v in values[period:]:
        out = alpha * v + (1 - alpha) * out
    return out


def sma(values: list[float], period: int) -> float:
    return sum(values[-period:]) / period


def bollinger(closes: list[float], period: int = 20) -> tuple[float, float, float]:
    """Bollinger bands with **population** σ (frozen convention, ÷ N).

    Returns (upper, lower, bandwidth_pct) where bandwidth = (upper−lower)/mid×100.
    """
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((c - mid) ** 2 for c in window) / period  # population σ
    sigma = variance ** 0.5
    upper, lower = mid + 2 * sigma, mid - 2 * sigma
    bandwidth = (upper - lower) / mid * 100.0
    return upper, lower, bandwidth


def compute(payload: dict, sessions: list[dict] | None) -> dict:
    waived = sessions is None
    out: dict = {
        "symbol": payload.get("symbol"),
        "atr_14": None, "sl_atr_ratio": None, "is_tight_sl": None,
        "ema_20": None, "above_50_ema": None, "above_200_sma": None,
        "trend_state": None,
        "bollinger_upper": None, "bollinger_lower": None,
        "bollinger_bandwidth": None,
        "waived": waived,
        "as_of": None,
    }
    if waived:
        return out

    closes = [float(row["close"]) for row in sessions]
    last_close = closes[-1]

    trs = true_ranges(sessions)
    atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD

    live = float(payload["live_price"])
    stop = float(payload["stoploss"])
    sl_distance = abs(live - stop)
    if atr > 0:
        sl_atr_ratio = sl_distance / atr
        is_tight = bool(sl_atr_ratio < ATR_NOISE_GATE)  # strict < (boundary 0.75 is NOT tight)
    else:
        sl_atr_ratio = None
        is_tight = None

    ema_20 = ema(closes, 20)
    ema_50 = ema(closes, 50)
    sma_200 = sma(closes, 200)

    above_50 = last_close > ema_50
    above_200 = last_close > sma_200
    if above_50 and above_200:
        trend = "BULLISH"
    elif not above_50 and not above_200:
        trend = "BEARISH"
    else:
        trend = "MIXED"

    bb_upper, bb_lower, bb_bw = bollinger(closes)

    out.update({
        # Full precision: the seam hand-checks the frozen simple-mean value at
        # 4dp, and sl_atr_ratio derives from the unrounded ATR internally.
        "atr_14": round(atr, 6),
        "sl_atr_ratio": sl_atr_ratio,
        "is_tight_sl": is_tight,
        "ema_20": round(ema_20, 2),
        "above_50_ema": above_50,
        "above_200_sma": above_200,
        "trend_state": trend,
        "bollinger_upper": round(bb_upper, 2),
        "bollinger_lower": round(bb_lower, 2),
        "bollinger_bandwidth": round(bb_bw, 4),
        "as_of": sessions[-1]["date"],
    })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 2: ATR / EMA / trend regime")
    parser.add_argument("--input", default="current_trade.json",
                        help="path to canonical current_trade.json")
    parser.add_argument("--ohlcv-fixture", default=None,
                        help="recorded daily-OHLCV bundle (offline mode); omit for waiver path")
    add_record_arg(parser)
    args = parser.parse_args()

    payload = load_payload(args.input)
    sessions = load_ohlcv(args.ohlcv_fixture)

    stdout_text = json.dumps(compute(payload, sessions), indent=2) + "\n"

    bundle = resolve_bundle_dir(args.record, payload.get("symbol") or "UNSPECIFIED",
                                payload.get("recommended_date"))
    if bundle is not None:
        record_artifact(bundle, 2, stdout_text)

    sys.stdout.write(stdout_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
