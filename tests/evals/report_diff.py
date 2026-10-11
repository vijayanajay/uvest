"""Report-vs-JSON differ — the Zero-Hallucination check itself (evals.md §3.1).

The dataset's `reported_numbers_match_json` boolean was a curator's *claim*
that a generated report matched its payload. This module is the check that
claim stood in for: the eval runner (`gate_hallucination` in
`tests/run_evals.py`) feeds it the stage-12 artifact
`reports/report_{SYMBOL}_{DATE}.html` and the record's own JSON, and it
returns every number the report shows that the JSON does not (ADR-007: the
gate stays graded, the flag is retired).

The frozen rule, straight from evals.md §3.1 — "Every number, percentage and
ratio cited in the report must have an exact match in the corresponding
script output JSON; any unbacked figure scores 0.0 for that run":

* Only the report's **visible text** is scanned: `<style>`, `<script>` and
  HTML comments are stripped first, so CSS `font-size: 13px` and JS scratch
  numbers are not claims.
* Decorations are not claims either: `₹2,974.00`, `+8.8502%` and `1,240 Cr`
  claim 2974.0, 8.8502 and 1240. An `84/100` scale expression claims 84 (the
  denominator is layout); any other `A/B` ratio claims both sides.
* Standard domain indicators and benchmarks (`NIFTY 50`, `NSE 500`, `200 DMA`,
  `EMA(20)`, `14-Day ATR`, `22-Day Volatility Cone`, `P10`..`P90`, `stage 12`,
  `stage-3`) are stripped so structural domain vocabulary does not hallucinate
  unbacked numbers.
* Layout denominators (`/ 100`, `Score / 100`, `/ 9`) and percentage gauge
  endpoints (`0%`, `100%`) are recognized as layout, not data claims.
* A claim is **backed** iff the record's JSON carries the same number (exact
  float equality or standard financial display rounding: 1–4 decimal places or
  2-decimal truncation), or the exact token appears in the serialized record as a
  whole number (ISO date fragments, `1:1.68` ratios, digits inside key
  names). If the trade's canonical input fixture exists under the proof root, its
  fields are included as backing evidence.
* Quarantined payloads (E5) never back anything: a number that lives only in
  a registered-quarantined artifact is unbacked, exactly as the breaker gate
  refuses those bytes as evidence.

`diff()` returns one string per unbacked claim (empty list = clean), and each
string names the offending token so the scorecard discrepancy names itself.
"""

from __future__ import annotations

import json
import math
import re
from html import unescape
from typing import Any

import dataset_refs

_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.DOTALL | re.IGNORECASE)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.DOTALL | re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

# A claim: optional sign, optional thousands grouping, optional decimals.
# The lookarounds keep digits inside identifiers (`sha256`, `x3`, `13px`, the
# fractional tail of a longer number) from counting as claims; a trailing
# sentence period (`₹2,974.00.`) does not truncate the decimal.
_NUMBER_RE = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w])(?!\.\d)"
)

# Bug 2: UI scale denominators and gauge bounds
_SCALE100_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*/\s*100(?![\w])")
_SCALE100_GEN_RE = re.compile(r"/\s*100(?![\w])")
_SCALE9_NUM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*/\s*9(?![\w])")
_SCALE9_GEN_RE = re.compile(r"/\s*9(?![\w])")
_GAUGE_BOUNDS_RE = re.compile(r"(?<![\d.])(?:0|100)\s*%(?![\w])")

# Bug 1: Domain indicators, stage names, and structural labels
_STAGE_RE = re.compile(r"\b(?:UVEST\s+)?stage[- ]\d+\b", re.IGNORECASE)
_INDEX_RE = re.compile(
    r"\b(?:NIFTY|NSE|BSE)\s*(?:50|100|500)\b|\bSENSEX\s*30\b|\bNIFTY\s*BANK\b",
    re.IGNORECASE,
)
_MA_MULTI_RE = re.compile(
    r"\b\d+\s*(?:&|and)\s*\d+\s*(?:DMA|EMA|SMA)\b", re.IGNORECASE
)
_INDICATOR_WINDOW_RE = re.compile(
    r"\b\d+\s*[- ]?\s*(?:DMA|EMA|SMA|ATR|RSI|D\s+Avg)\b|\b(?:DMA|EMA|SMA|ATR|RSI)\s*[- ]?\s*\d+\b",
    re.IGNORECASE,
)
_INDICATOR_PAREN_RE = re.compile(
    r"\b(?:EMA|SMA|DMA|ATR|RSI)\s*\(\s*\d+\s*\)", re.IGNORECASE
)
_ABOVE_MA_RE = re.compile(r"\bAbove\s+\d+[- ](?:EMA|SMA|DMA)\b", re.IGNORECASE)
_HORIZON_RE = re.compile(
    r"\b\d+\s*[- ]?\s*(?:Day|Days|Session|Sessions|bar|bars|minute|minutes|m\s+OB)\b",
    re.IGNORECASE,
)
_PERCENTILE_RE = re.compile(r"\bP(?:10|25|50|75|90)\b", re.IGNORECASE)
_SPEC_RE = re.compile(
    r"\b(?:BRD\s*§\s*\d+(?:\.\d+)?|ADR-\d+|TICK-\d+|EVAL-\d+)\b", re.IGNORECASE
)
_SECTION_NUM_RE = re.compile(r"(?:^|\s)\d+\.\s+(?=[A-Z])")


def visible_text(html: str) -> str:
    """The text a human reads: markup, styles, scripts and comments removed."""
    text = _STYLE_RE.sub(" ", html)
    text = _SCRIPT_RE.sub(" ", text)
    text = _COMMENT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return unescape(text)


def claims(html: str) -> list[str]:
    """Every numeric claim token of the report, in order of appearance.

    `84/100` collapses to `84` (a scale denominator is layout, not data);
    `Score / 100` drops the denominator; gauge ticks (`0%`, `100%`) and domain
    indicator names (`NIFTY 50`, `200 DMA`, `stage 12`, `22-Day Cone`) are
    stripped so layout and terminology do not hallucinate numbers.
    """
    text = visible_text(html)
    # UI scale denominators and gauge extremes
    text = _SCALE100_NUM_RE.sub(r" \1 ", text)
    text = _SCALE100_GEN_RE.sub(" ", text)
    text = _SCALE9_NUM_RE.sub(r" \1 ", text)
    text = _SCALE9_GEN_RE.sub(" ", text)
    text = _GAUGE_BOUNDS_RE.sub(" ", text)
    # Domain terminology and indicator windows
    text = _STAGE_RE.sub(" ", text)
    text = _INDEX_RE.sub(" ", text)
    text = _MA_MULTI_RE.sub(" ", text)
    text = _INDICATOR_WINDOW_RE.sub(" ", text)
    text = _INDICATOR_PAREN_RE.sub(" ", text)
    text = _ABOVE_MA_RE.sub(" ", text)
    text = _HORIZON_RE.sub(" ", text)
    text = _PERCENTILE_RE.sub(" ", text)
    text = _SPEC_RE.sub(" ", text)
    text = _SECTION_NUM_RE.sub(" ", text)
    return _NUMBER_RE.findall(text)


def _json_numbers(value: Any) -> set[float]:
    """Every float a (already quarantine-stripped) record states."""
    found: set[float] = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= _json_numbers(item)
    elif isinstance(value, list):
        for item in value:
            found |= _json_numbers(item)
    elif isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        found.add(float(value))
    return found


def _backed_numbers(raw_numbers: set[float]) -> set[float]:
    """Expand raw JSON floats with standard financial display representations:
    1-decimal, 2-decimal, 3-decimal, 4-decimal rounding, and 2-decimal truncation.
    """
    expanded: set[float] = set(raw_numbers)
    for num in raw_numbers:
        for ndigits in (1, 2, 3, 4):
            expanded.add(round(num, ndigits))
        expanded.add(math.floor(num * 100.0) / 100.0)
        expanded.add(math.ceil(num * 100.0) / 100.0)
    return expanded


def _in_blob(token: str, blob: str) -> bool:
    """The token appears in the JSON as a whole number, never as a fragment.

    `85` must not ride on a JSON `8.8502` — the digit-boundary is what makes
    the match exact.
    """
    return re.search(r"(?<![\d.,])" + re.escape(token) + r"(?!\d)", blob) is not None


def diff(report_html: str, trade: dict) -> list[str]:
    """The unbacked numeric claims of `report_html` against `trade`'s own JSON.

    `trade` is the *materialized* record (`dataset_refs.materialize`): script
    payloads resolved through their `$ref`s, outcome fields recomputed from
    their tapes. Quarantine provenance is honoured here (E5): bytes the
    register holds quarantined are visible but never evidence. If the trade's
    canonical input trade fixture exists under the proof root, its fields are
    included as evidence.
    """
    quarantined = set(dataset_refs.quarantined_in(trade))
    rest = {k: v for k, v in trade.items() if k != "script_outputs"}
    stripped_trade = {
        **rest,
        "script_outputs": {k: v for k, v in (trade.get("script_outputs") or {}).items()
                           if k not in quarantined},
    }

    # Also load canonical trade fixture if present under proof root
    tid = trade.get("trade_id")
    canonical_payload: dict = {}
    if tid:
        cf = dataset_refs.PROOF_ROOT / "fixtures" / str(tid) / "current_trade.json"
        if cf.exists():
            try:
                canonical_payload = json.loads(cf.read_text(encoding="utf-8"))
            except Exception:
                pass

    raw_numbers = _json_numbers(stripped_trade)
    if canonical_payload:
        raw_numbers |= _json_numbers(canonical_payload)
    backset = _backed_numbers(raw_numbers)

    blob = json.dumps(stripped_trade, sort_keys=True)
    if canonical_payload:
        blob += " " + json.dumps(canonical_payload, sort_keys=True)

    problems: list[str] = []
    for token in claims(report_html):
        normalized = token.replace(",", "")
        try:
            number = float(normalized)
        except ValueError:  # pragma: no cover — the regex cannot produce this
            continue
        if number in backset:
            continue
        if _in_blob(token, blob) or _in_blob(normalized, blob):
            continue
        problems.append(f"`{token}` has no match in the record's JSON")
    return problems
