#!/usr/bin/env python3
"""UVEST stage 1 — Mathematical R:R, Slippage & Margin Engine (TICK-002).

Consumes the canonical `current_trade.json` (stage 0) and freezes the base
mathematical layer every later stage scores against (BRD §9.1 row 1):

* `raw_rr`   — R:R at the recommended band midpoint (the sanitized promise).
* `live_rr`  — R:R at the price the user can actually execute at right now.
* `outside_range_pct` — how far live price has drifted outside the band,
  as a percentage of the band width (the slippage penalty input).
* `risk_inr` / `reward_inr` — per-position ₹ risk and reward
  (`lot_size` for FUT, 1 share for CASH).
* `inverted_rr_flag` — frozen BRD §10.2 #1 circuit breaker: `live_rr < 1.0`.
  Boundary R:R == 1.0 does NOT trip (strict inequality).

Reads `--input current_trade.json`; writes `out_1_math.json` contract JSON to
stdout. Errors to stderr, non-zero exit. Stdlib only, zero network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Shared --record bundle helper lives next door; importable because scripts/ is
# on sys.path when the stage script runs from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recorder import add_record_arg, record_artifact, resolve_bundle_dir  # noqa: E402

ERR_PARSE = "ERR_PARSE"

REQUIRED_FIELDS = ["entry_price", "entry_range_min", "entry_range_max",
                   "live_price", "target", "stoploss"]


def die(msg: str) -> None:
    print(f"{ERR_PARSE}: {msg}", file=sys.stderr)
    sys.exit(1)


def load_payload(path: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(f"input file not found: {path}")
    except json.JSONDecodeError as exc:
        die(f"input is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        die("input must be a JSON object")
    missing = [k for k in REQUIRED_FIELDS if k not in payload]
    if missing:
        die(f"missing required field(s): {', '.join(missing)}")
    return payload


def compute(payload: dict) -> dict:
    entry_min = float(payload["entry_range_min"])
    entry_max = float(payload["entry_range_max"])
    live = float(payload["live_price"])
    target = float(payload["target"])
    stop = float(payload["stoploss"])

    midpoint = (entry_min + entry_max) / 2.0
    raw_risk = midpoint - stop
    raw_reward = target - midpoint
    if raw_risk <= 0:
        die(f"stoploss {stop} is not below band midpoint {midpoint}")
    raw_rr = raw_reward / raw_risk

    live_risk = live - stop
    live_reward = target - live
    if live_risk <= 0:
        die(f"stoploss {stop} is not below live price {live}")
    live_rr = live_reward / live_risk

    # Slippage: signed distance outside the band, as % of band width.
    band_width = entry_max - entry_min
    if band_width <= 0:
        die(f"entry_range {entry_min}-{entry_max} is not a valid band")
    outside = 0.0
    if live > entry_max:
        outside = (live - entry_max) / band_width * 100.0
    elif live < entry_min:
        outside = (entry_min - live) / band_width * 100.0

    lot_size = payload.get("lot_size")
    instrument = payload.get("instrument_type", "")
    quantity = 1 if (instrument in ("CASH", "EQUITY") or lot_size is None) else int(lot_size)

    capital = float(payload.get("user_capital_inr") or 0)
    risk_inr = live_risk * quantity
    reward_inr = live_reward * quantity

    return {
        "symbol": payload.get("symbol"),
        "instrument_type": instrument,
        "raw_rr": raw_rr,
        "live_rr": live_rr,
        "risk_inr": round(risk_inr, 2),
        "reward_inr": round(reward_inr, 2),
        "quantity": quantity,
        "outside_range_pct": outside,
        "inverted_rr_flag": bool(live_rr < 1.0),
        "capital_allocation_pct": round(risk_inr / capital * 100.0, 4) if capital > 0 else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 1: R:R / slippage / margin math")
    parser.add_argument("--input", default="current_trade.json",
                        help="path to canonical current_trade.json")
    add_record_arg(parser)  # shared --record flag, from scripts/_recorder.py
    args = parser.parse_args()

    payload = load_payload(args.input)

    # ONE serialisation, written to BOTH stdout and the bundle (byte-exact).
    stdout_text = json.dumps(compute(payload), indent=2) + "\n"

    bundle = resolve_bundle_dir(args.record, payload.get("symbol") or "UNSPECIFIED",
                                payload.get("recommended_date"))
    if bundle is not None:
        record_artifact(bundle, 1, stdout_text)

    sys.stdout.write(stdout_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
