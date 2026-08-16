"""Shared helpers for the trade validation pipeline.

All paths are resolved relative to the project root so every script
works identically no matter which directory it is invoked from.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Files the pipeline reads/writes, all at the project root.
INPUT_FILE = PROJECT_ROOT / "current_trade.json"
OUT1_FILE = PROJECT_ROOT / "script1_out.json"
OUT2_FILE = PROJECT_ROOT / "script2_out.json"
OUT3_FILE = PROJECT_ROOT / "script3_out.json"
OUT4_FILE = PROJECT_ROOT / "script4_out.json"
DB_FILE = PROJECT_ROOT / "univest_history.db"
REPORT_FILE = PROJECT_ROOT / "decision_report.md"


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file into a dict. Raises on missing/invalid input."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    """Write a dict as pretty JSON to a file, creating parent dirs."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def emit_json(data: dict[str, Any]) -> None:
    """Print a dict as JSON to stdout — the machine-readable contract
    every script in this suite honours."""
    print(json.dumps(data, indent=2, ensure_ascii=False))


def fail(message: str) -> None:
    """Emit a JSON error payload and exit non-zero."""
    emit_json({"error": message})
    sys.exit(1)


def indian_currency(value: float) -> str:
    """Format a number as Indian-rupee currency, e.g. 26647.5 -> ₹26,647.50."""
    return f"₹{value:,.2f}"
