"""Script 1: Mathematical Risk-to-Reward & Slippage Engine.

Reads `current_trade.json`, computes raw and live (slippage-adjusted)
risk:reward, and flags inverted risk symmetry and entry-range violations.

Usage:
    python scripts/1_math_and_rr.py [path/to/current_trade.json]
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INPUT_FILE, emit_json, fail, load_json  # noqa: E402


def analyze_math(data: dict[str, Any]) -> dict[str, Any]:
    entry = float(data["entry_price"])
    sl = float(data["stoploss"])
    target = float(data["target"])
    live = float(data["live_price"])
    lot_size = float(data["lot_size"])

    # Raw Math (at the recommended entry price)
    raw_risk_per_share = entry - sl
    raw_reward_per_share = target - entry
    raw_rr = raw_reward_per_share / raw_risk_per_share if raw_risk_per_share > 0 else 0.0

    # Live / Slippage Math (at the current live price)
    live_risk_per_share = live - sl
    live_reward_per_share = target - live
    live_rr = live_reward_per_share / live_risk_per_share if live_risk_per_share > 0 else 0.0

    entry_range_min = float(data.get("entry_range_min", entry))
    entry_range_max = float(data.get("entry_range_max", entry))

    return {
        "entry_price": entry,
        "stoploss": sl,
        "target": target,
        "live_price": live,
        "raw_rr": round(raw_rr, 2),
        "live_rr": round(live_rr, 2),
        "raw_risk_per_share": round(raw_risk_per_share, 2),
        "raw_reward_per_share": round(raw_reward_per_share, 2),
        "live_risk_per_share": round(live_risk_per_share, 2),
        "live_reward_per_share": round(live_reward_per_share, 2),
        "risk_per_lot_inr": round(live_risk_per_share * lot_size, 2),
        "reward_per_lot_inr": round(live_reward_per_share * lot_size, 2),
        "entry_range_min": entry_range_min,
        "entry_range_max": entry_range_max,
        "outside_entry_range": live > entry_range_max or live < entry_range_min,
        "inverted_rr_flag": live_rr < 1.0,
    }


if __name__ == "__main__":
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else INPUT_FILE
    try:
        emit_json(analyze_math(load_json(input_path)))
    except FileNotFoundError:
        fail(f"Input file not found: {input_path}. Create it first (see current_trade.json).")
    except KeyError as exc:
        fail(f"Input file is missing required field: {exc}")
    except (TypeError, ValueError) as exc:
        fail(f"Input file has an invalid numeric value: {exc}")
