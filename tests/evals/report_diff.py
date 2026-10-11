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
* A claim is **backed** iff the record's JSON carries the same number (exact
  float equality), or the exact token appears in the serialized record as a
  whole number (ISO date fragments, `1:1.68` ratios, digits inside key
  names). "Exact" is literal: a rounded `8.85` for a JSON `8.8502` is a
  discrepancy, not a nicety.
* Quarantined payloads (E5) never back anything: a number that lives only in
  a registered-quarantined artifact is unbacked, exactly as the breaker gate
  refuses those bytes as evidence.

`diff()` returns one string per unbacked claim (empty list = clean), and each
string names the offending token so the scorecard discrepancy names itself.
"""

from __future__ import annotations

import json
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
_SCALE100_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*/\s*100(?![\w])")


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
    every other ratio keeps both of its numbers.
    """
    text = _SCALE100_RE.sub(r" \1 ", visible_text(html))
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


def _in_blob(token: str, blob: str) -> bool:
    """The token appears in the JSON as a whole number, never as a fragment.

    `8.85` must not ride on a JSON `8.8502`, and `85` must not ride on a JSON
    `8.8502` either — the digit-boundary is what makes the match exact.
    """
    return re.search(r"(?<![\d.,])" + re.escape(token) + r"(?!\d)", blob) is not None


def diff(report_html: str, trade: dict) -> list[str]:
    """The unbacked numeric claims of `report_html` against `trade`'s own JSON.

    `trade` is the *materialized* record (`dataset_refs.materialize`): script
    payloads resolved through their `$ref`s, outcome fields recomputed from
    their tapes. Quarantine provenance is honoured here (E5): bytes the
    register holds quarantined are visible but never evidence.
    """
    quarantined = set(dataset_refs.quarantined_in(trade))
    rest = {k: v for k, v in trade.items() if k != "script_outputs"}
    stripped_trade = {
        **rest,
        "script_outputs": {k: v for k, v in (trade.get("script_outputs") or {}).items()
                           if k not in quarantined},
    }
    backset = _json_numbers(stripped_trade)
    blob = json.dumps(stripped_trade, sort_keys=True)

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
