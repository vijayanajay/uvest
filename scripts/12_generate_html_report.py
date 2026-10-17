#!/usr/bin/env python3
"""UVEST stage 12 — Standalone Interactive HTML Visualizer (TICK-010).

Renders the self-contained, zero-dependency dark-mode dashboard of BRD §13
from a trade's own artifacts and writes it to the canonical path
`reports/report_{SYMBOL}_{DATE}.html` (BRD §9.1 stage 12 — the artifact is
written by the script itself, never a stdout redirect; stdout carries only a
JSON status line).

**The zero-hallucination rendering rule (evals.md §3.1):** every number in
the report's *visible text* is a value read from the inputs —
`current_trade.json` and whichever `out_*.json` payloads are supplied —
rendered exactly. The script never computes, rounds, or derives a displayed
figure: a rounded `sl_atr_ratio` is a *different* number than the JSON
states, and an unbacked figure is a hallucination by the frozen rule. The
eval gate proves this byte for byte (`tests/evals/report_diff.py` grades
every produced report against its record). SVG geometry lives in tag
attributes, which are not visible text, so charts add no claims.

Every supplied payload renders: stages 1, 2 and 9 get their own sections, and
stages 3–11 get a card of the scalar fields the payload states, rendered
verbatim (never recomputed, never summarised). A card's **prose labels** follow
the vocabulary its artifact's fields now speak, not a field name that outlived
its decision: stage 4's card renders the **target-versus-stop race**
(`TARGET_FIRST` / `STOP_FIRST` / `NEITHER`, `RACE_FIELD_LABELS`) and draws §13.2's
radial race gauge from the artifact's own `P(TARGET_FIRST)` (`race_svg`), because
decision D (2026-10-16) moved that engine's event off the retired up/down one —
a probability the engine never answered must not be rendered under a
directional label, and no card here states a probability as bullish or bearish.
A payload that was NOT supplied
renders an explicit "awaiting stage N" placeholder — absence is the only honest
value (E4, scenario-manifest-schema.md) — and a supplied payload is never
silently dropped, because a stage's evidence a report cannot show is evidence
the reader cannot check. Nothing here is networked, timed, or random: same
inputs, same bytes.

Usage:
    python scripts/12_generate_html_report.py \
        --input current_trade.json \
        [--payload out_1_math.json] [--payload out_2_market.json] ... \
        [--out-dir reports]
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import math
import sys
from pathlib import Path

ERR_INPUT = "ERR_INPUT"

# Display order of the pipeline payloads; absent ones render placeholders.
PAYLOAD_STAGES: list[tuple[str, int, str]] = [
    ("out_3_dtw.json", 3, "DTW Historical Pattern Overlay"),
    ("out_4_ml.json", 4, "Target-vs-Stop Race Gauge & 22-Day Volatility Price Cone"),
    ("out_5_flow.json", 5, "Institutional Flow Matrix"),
    ("out_6_fundamental.json", 6, "Fundamental Health"),
    ("out_7_regime.json", 7, "Sector & Macro Barometer"),
    ("out_8_dhan.json", 8, "Precision Intraday & Depth"),
    ("out_9_decision.json", 9, "Decision Engine"),
]

# Stage 4's own keys, read under the frozen race vocabulary. Decision D moved
# the engine's event onto the target-versus-stop race (BRD §5.2, 2026-10-16):
# the classes are TARGET_FIRST / STOP_FIRST / NEITHER, so a card that still
# labelled `prob_target_hit_22d` a "Direction probability" would present the
# race as the retired up/down event — and a gauge whose slices read
# Bullish/Neutral/Bearish would state a probability the engine never answered.
# Prose only: every value stays the payload's own, rendered by `fmt`.
RACE_FIELD_LABELS = {
    "direction_label": "Race label",
    "direction_probability": "P(target first) · reference band",
    "prob_target_hit_22d": "P(target first) · this trade's target & stop",
}

RACE_CAPTION = (
    "Race: this trade's own target against its own stop — whichever level price "
    "touches first decides, and NEITHER means neither was touched inside the "
    "horizon. The label and both probabilities are the stage-4 artifact's own "
    "values, stated at their own geometries."
)

# The one card whose vocabulary is a decision (D), not a field name.
PAYLOAD_CAPTIONS = {"out_4_ml.json": RACE_CAPTION}

# BRD §13.2's radial speedometer, in the geometry the blueprint freezes: a
# semi-circular arc (radius 80, `viewBox "0 0 200 120"`) sliced into the
# artifact's own three race classes, needle at `P(TARGET_FIRST) × 180°` measured
# from the left — §13.2's own sentence, which is why the retired gauge's
# `P(Bullish)` cannot be the input. Geometry lives in attributes only: the
# gauge's **visible text carries no digits**, and every figure a reader sees is
# still an artifact value in the card's rows, so the drawing cannot state a
# claim the record does not carry.
RACE_GAUGE = {
    "view_box": "0 0 200 120",
    "centre": (100.0, 100.0),
    "radius": 80.0,
    "stroke": 14.0,
    "needle": 62.0,
    "slices": (("STOP_FIRST", 0.0, 60.0, "#da3633"),
               ("NEITHER", 60.0, 120.0, "#d29922"),
               ("TARGET_FIRST", 120.0, 180.0, "#2ea043")),
}


def _num(value: float) -> str:
    """A coordinate, trimmed — geometry is not a claim, so it need not be exact."""
    return f"{round(float(value), 2):g}"


def needle_svg_angle(probability) -> float:
    """§13.2's `theta = P(TARGET_FIRST) × 180°`, mapped into SVG's angles.

    The blueprint measures from the left (0° = the `STOP_FIRST` end, 180° = the
    `TARGET_FIRST` end); SVG measures from the right with y down, so the two are
    `180 − theta`. A probability outside [0, 1] is clipped rather than drawn past
    the arc — the needle's position is geometry, and an out-of-range artifact is
    a stage-4 problem, not a report that lies about angles.
    """
    number = float(probability)
    clipped = min(1.0, max(0.0, number))
    return 180.0 - 180.0 * clipped


def _point(angle_deg: float, radius: float) -> tuple[float, float]:
    cx, cy = RACE_GAUGE["centre"]
    radians = math.radians(angle_deg)
    return cx + radius * math.cos(radians), cy - radius * math.sin(radians)


def race_svg(payload: dict) -> str:
    """The race gauge for a stage-4 payload; `""` when it carries no probability.

    Absence draws nothing at all: a gauge with no needle would be a picture of a
    probability nobody measured, and the card's own rows already state the
    absence (E4).
    """
    probability = payload.get("direction_probability")
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        return ""
    radius = RACE_GAUGE["radius"]
    marks = []
    for label, start, end, color in RACE_GAUGE["slices"]:
        x0, y0 = _point(180.0 - start, radius)
        x1, y1 = _point(180.0 - end, radius)
        marks.append(
            f'<path d="M {_num(x0)},{_num(y0)} A {_num(radius)},{_num(radius)} '
            f'0 0 1 {_num(x1)},{_num(y1)}" fill="none" stroke="{color}" '
            f'stroke-width="{_num(RACE_GAUGE["stroke"])}" stroke-linecap="butt">'
            f'<title>{esc(label)}</title></path>')
    angle = needle_svg_angle(probability)
    nx, ny = _point(angle, RACE_GAUGE["needle"])
    cx, cy = RACE_GAUGE["centre"]
    marks.append(
        f'<circle cx="{_num(cx)}" cy="{_num(cy)}" r="4" fill="#e6edf3"/>'
        f'<line x1="{_num(cx)}" y1="{_num(cy)}" x2="{_num(nx)}" y2="{_num(ny)}" '
        f'stroke="#e6edf3" stroke-width="2" stroke-linecap="round"/>')
    legend = " · ".join(f'<span style="color:{color}">{esc(label)}</span>'
                        for label, _s, _e, color in RACE_GAUGE["slices"])
    return (
        f'<svg viewBox="{RACE_GAUGE["view_box"]}" width="100%" height="120" '
        'role="img" aria-label="Target-vs-Stop race gauge">'
        + "".join(marks) + "</svg>"
        f'<div class="sub">{legend} — needle: the artifact\'s own '
        'P(target first)</div>')


def _card_visual(name: str, payload: dict) -> str:
    """The bespoke visual a card draws, if any (BRD §13.2)."""
    return race_svg(payload) if name == "out_4_ml.json" else ""

# Canonical 7-state verdict vocabulary (BRD §10.4, ADR-006).
VERDICT_CLASS = {
    "GO": "v-go", "GO_WEAK": "v-weak", "WAIT": "v-wait", "AVOID": "v-avoid",
    "HOLD": "v-hold", "PRESSURE": "v-pressure", "EXIT": "v-exit",
}

CSS = """
:root { --bg:#0d1117; --panel:#161b22; --line:#30363d; --fg:#e6edf3;
        --muted:#8b949e; --green:#2ea043; --red:#da3633; --amber:#d29922;
        --blue:#1f6feb; --orange:#d29922; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:14px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif; }
main { max-width:960px; margin:0 auto; padding:24px; }
header { display:flex; flex-wrap:wrap; gap:12px; align-items:center;
         justify-content:space-between; border-bottom:1px solid var(--line);
         padding-bottom:16px; margin-bottom:20px; }
h1 { font-size:20px; margin:0; letter-spacing:.5px; }
h2 { font-size:14px; text-transform:uppercase; letter-spacing:1px;
     color:var(--muted); margin:0 0 10px; }
.sub { color:var(--muted); font-size:12px; }
.badge { display:inline-block; padding:6px 14px; border-radius:20px;
         font-weight:700; border:1px solid var(--line); }
.v-go { background:rgba(46,160,67,.15); color:var(--green); }
.v-weak { background:rgba(210,153,34,.15); color:var(--amber); }
.v-wait { background:rgba(139,148,158,.15); color:var(--muted); }
.v-avoid { background:rgba(218,54,51,.15); color:var(--red); }
.v-hold { background:rgba(31,111,235,.15); color:var(--blue); }
.v-pressure { background:rgba(210,153,34,.15); color:var(--orange); }
.v-exit { background:rgba(218,54,51,.25); color:var(--red); }
.v-pending { background:transparent; color:var(--muted); font-weight:400; }
.card { background:var(--panel); border:1px solid var(--line);
        border-radius:8px; padding:16px; margin-bottom:16px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
        gap:16px; }
.kv { display:flex; justify-content:space-between; padding:4px 0;
      border-bottom:1px dashed var(--line); }
.kv:last-child { border-bottom:none; }
.k { color:var(--muted); } .val { font-family:ui-monospace,Consolas,monospace; }
.warn { color:var(--red); font-weight:700; }
.ok { color:var(--green); }
.pending { color:var(--muted); font-style:italic; padding:12px 0; }
svg text { font-family:ui-monospace,Consolas,monospace; }
"""


def die(code: str, exit_code: int, hint: str = "") -> None:
    print(f"{code}: {hint}" if hint else code, file=sys.stderr)
    sys.exit(exit_code)


def fmt(value) -> str:
    """Render a JSON number so the report's token IS that number.

    Frozen exactness contract (`tests/evals/report_diff.py`): rounding to
    fewer digits would state a different number than the JSON carries, so a
    value only collapses to two decimals when that is the very same float;
    otherwise it is printed at full precision.
    """
    number = float(value)
    if number.is_integer():
        return f"{number:,.0f}"
    two = f"{number:,.2f}"
    if float(two.replace(",", "")) == number:
        return two
    return repr(number)


def esc(value) -> str:
    return html_mod.escape(str(value), quote=True)


def kv(label: str, value) -> str | None:
    """One key/value row, or None when the value is honestly absent."""
    if value is None:
        return None
    if isinstance(value, bool):
        text = "YES" if value else "NO"
    elif isinstance(value, (int, float)):
        text = f"₹{fmt(value)}" if "inr" in label.lower() else fmt(value)
    else:
        text = esc(value)
    return f'<div class="kv"><span class="k">{esc(label)}</span><span class="val">{text}</span></div>'


def rows_of(pairs: list[tuple[str, object]]) -> str:
    rendered = [kv(label, value) for label, value in pairs]
    return "".join(r for r in rendered if r)


def zone_svg(trade: dict) -> str:
    """The execution zone bar; geometry lives in attributes, never in text."""
    lo = trade.get("entry_range_min")
    hi = trade.get("entry_range_max")
    live = trade.get("live_price")
    stop = trade.get("stoploss")
    target = trade.get("target")
    points = [v for v in (lo, hi, live, stop, target) if isinstance(v, (int, float))]
    if not points:
        return ""
    pad = 90.0
    span = max(points) - min(points)
    if span <= 0:
        span = 1.0
    lo_edge, hi_edge = min(points) - pad, max(points) + pad

    def x(v: float) -> float:
        return round(40.0 + (v - lo_edge) / (hi_edge - lo_edge) * 700.0, 2)

    marks = []
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
        marks.append(
            f'<rect x="{x(lo)}" y="18" width="{round(x(hi) - x(lo), 2)}" height="24" '
            f'fill="rgba(46,160,67,.30)" stroke="#2ea043" rx="4">'
            f'<title>Limit entry zone</title></rect>')
    for value, color, label in ((stop, "#da3633", "SL"), (target, "#2ea043", "TGT"),
                                (live, "#d29922", "LIVE")):
        if isinstance(value, (int, float)):
            marks.append(
                f'<line x1="{x(value)}" y1="10" x2="{x(value)}" y2="50" '
                f'stroke="{color}" stroke-width="2"/>')
    captions = []
    for value, label in ((stop, "Stop"), (lo, "Zone low"), (hi, "Zone high"),
                         (target, "Target")):
        if isinstance(value, (int, float)):
            captions.append(f'<div class="kv"><span class="k">{label}</span>'
                            f'<span class="val">₹{fmt(value)}</span></div>')
    return (
        '<svg viewBox="0 0 780 60" width="100%" height="60" role="img" '
        'aria-label="Execution zone bar">' + "".join(marks) + "</svg>"
        f'<div class="grid">{"<div>" + "".join(captions) + "</div>"}</div>')


def verdict_block(payloads: dict[str, dict]) -> str:
    decision = payloads.get("out_9_decision.json") or {}
    verdict = decision.get("verdict")
    if verdict in VERDICT_CLASS:
        badge = f'<span class="badge {VERDICT_CLASS[verdict]}">{esc(verdict)}</span>'
        # §13.2's header draws `Score: 84/100`: stage 9's own final over its own
        # maximum — the waiver rule (§10.4) drops `max_possible_score` below the
        # nominal 100 whenever a source is missing or waived, so the artifact's
        # pair is the only honest denominator — plus the effective % the P(win)
        # calibration read from. Both halves are the artifact's numbers through
        # `fmt`, which is what keeps this header differ-clean; a score row whose
        # denominator the artifact does not carry is not drawn at all.
        pairs: list[tuple[str, object]] = []
        final, maximum = decision.get("final_score"), decision.get("max_possible_score")
        has_final = (isinstance(final, (int, float))
                     and not isinstance(final, bool))
        has_maximum = (isinstance(maximum, (int, float))
                       and not isinstance(maximum, bool))
        if has_final and has_maximum:
            pairs.append(("Composite score", f"{fmt(final)}/{fmt(maximum)}"))
        elif has_final:
            # A score the artifact carries is shown; a maximum it does not carry
            # is never invented to make the row look complete.
            pairs.append(("Composite score", fmt(final)))
        pairs += [("Effective score %", decision.get("effective_score_pct")),
                  ("Calibrated P(win)", decision.get("p_win")),
                  ("Expectancy", decision.get("expectancy"))]
        extras = rows_of(pairs)
        return badge + f'<div class="sub">{extras or "no score carried"}</div>'
    return ('<span class="badge v-pending">verdict pending stage-9 evidence</span>'
            '<div class="sub">decision engine has not run for this record</div>')


def payload_rows(payload: dict, labels: dict[str, str] | None = None) -> str:
    """A supplied payload's own scalar fields, rendered verbatim.

    The generic card for the stages without a bespoke visual yet: labels are
    prose, values are the payload's exact JSON numbers (`fmt`), so the card adds
    no claim the record's JSON does not already carry — which is what keeps it
    differ-clean (`tests/evals/report_diff.py`) while the specialized gauges of
    TICK-010 §13.2 are still being built. `labels` overrides a key's prose where
    the field's *meaning* was re-frozen and its name was not (stage 4's race:
    see `RACE_FIELD_LABELS`) — the value is untouched either way.
    """
    pairs: list[tuple[str, object]] = []
    for key, value in payload.items():
        label = (labels or {}).get(key) or key.replace("_", " ").strip().capitalize()
        if isinstance(value, bool) or isinstance(value, (int, float)) or isinstance(value, str):
            pairs.append((label, value))
        elif isinstance(value, list):
            rendered = ", ".join(str(item) for item in value)
            pairs.append((label, rendered or None))
    return rows_of(pairs) or '<div class="pending">payload carries no scalar fields</div>'


def _card_labels(name: str) -> dict[str, str] | None:
    """The prose-label overrides a payload's card renders under."""
    return RACE_FIELD_LABELS if name == "out_4_ml.json" else None


def render(trade: dict, payloads: dict[str, dict]) -> str:
    symbol = esc(trade.get("symbol", "?"))
    exchange = esc(trade.get("exchange", ""))
    instrument = esc(trade.get("instrument_type", trade.get("instrument", "")))
    action = esc(trade.get("action", ""))
    day = esc(trade.get("recommended_date", trade.get("recommendation_date", "")))

    math_p = payloads.get("out_1_math.json") or {}
    market_p = payloads.get("out_2_market.json") or {}

    execution = rows_of([
        ("Entry price", trade.get("entry_price")),
        ("Limit zone low", trade.get("entry_range_min")),
        ("Limit zone high", trade.get("entry_range_max")),
        ("Live price", trade.get("live_price")),
        ("Target", trade.get("target")),
        ("Stop loss", trade.get("stoploss")),
        ("Lot size", trade.get("lot_size")),
        ("Capital (INR)", trade.get("user_capital_inr")),
        ("Quantity", math_p.get("quantity")),
        ("Risk (INR)", math_p.get("risk_inr")),
        ("Reward (INR)", math_p.get("reward_inr")),
        ("Raw R:R", math_p.get("raw_rr")),
        ("Live R:R", math_p.get("live_rr")),
        ("Outside range %", math_p.get("outside_range_pct")),
        ("Capital allocation %", math_p.get("capital_allocation_pct")),
    ])
    breaker_rows = ""
    if math_p.get("inverted_rr_flag") is True:
        breaker_rows += ('<div class="kv"><span class="warn">INVERTED R:R hard '
                         'breaker tripped (BRD §10.2)</span></div>')
    if market_p.get("is_tight_sl") is True:
        breaker_rows += ('<div class="kv"><span class="warn">TIGHT-STOP hard '
                         'breaker tripped (SL vs ATR)</span></div>')
    elif market_p.get("is_tight_sl") is False:
        breaker_rows += ('<div class="kv"><span class="ok">Tight-stop breaker '
                         'clear at the recorded ATR</span></div>')
    market = rows_of([
        ("As of", market_p.get("as_of")),
        ("ATR(14)", market_p.get("atr_14")),
        ("SL / ATR", market_p.get("sl_atr_ratio")),
        ("EMA(20)", market_p.get("ema_20")),
        ("Above 50-EMA", market_p.get("above_50_ema")),
        ("Above 200-SMA", market_p.get("above_200_sma")),
        ("Trend state", market_p.get("trend_state")),
        ("Bollinger upper", market_p.get("bollinger_upper")),
        ("Bollinger lower", market_p.get("bollinger_lower")),
        ("Bandwidth %", market_p.get("bollinger_bandwidth")),
    ])
    if market_p.get("waived") is True:
        market = '<div class="pending">stage 2 waived: OHLCV unavailable or too short</div>'
    elif market:
        # Breaker verdicts live on the execution ticket only — the order is
        # what they gate (BRD §10.2); repeating them here would show the same
        # claim twice in one document.
        market = f'<div class="grid"><div>{market}</div></div>'
    else:
        market = '<div class="pending">awaiting stage-2 evidence</div>'

    def card(name: str, stage: int, title: str) -> str:
        caption = PAYLOAD_CAPTIONS.get(name)
        head = f'<div class="card"><h2>{esc(title)} (stage {stage})</h2>'
        if caption:
            head += f'<div class="sub">{esc(caption)}</div>'
        visual = _card_visual(name, payloads[name])
        return f'{head}{visual}{payload_rows(payloads[name], _card_labels(name))}</div>'

    supplied_cards = "".join(
        card(name, stage, title)
        for name, stage, title in PAYLOAD_STAGES
        if name in payloads and stage != 9)
    pending_cards = "".join(
        f'<div class="card"><h2>{esc(title)}</h2>'
        f'<div class="pending">awaiting stage-{stage} evidence</div></div>'
        for name, stage, title in PAYLOAD_STAGES
        if name not in payloads and stage != 9)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UVEST Validation Report: {symbol} {instrument} {day}</title>
<style>{CSS}</style>
</head>
<body><main>
<header>
  <div>
    <h1>{symbol} · {instrument} · {action}</h1>
    <div class="sub">{exchange} · recommended {day} · generated by UVEST stage 12</div>
  </div>
  {verdict_block(payloads)}
</header>
<section class="card"><h2>Execution Ticket</h2>{zone_svg(trade)}{execution}{breaker_rows}</section>
<section class="card"><h2>Market Context (stage 2)</h2>{market}</section>
<div class="grid">{supplied_cards}</div>
<div class="grid">{pending_cards}</div>
</main></body></html>
"""


# The trade fields stage 12 cannot render without: `symbol`, and the date the
# artifact name is built from (`report_{SYMBOL}_{DATE}.html`, TICK-010).
# Module-level so the chain contract (tests/test_chain_contract.py) reads the
# same list the gate uses, instead of a copy that could drift from it.
REQUIRED_FIELDS = ("symbol", "recommended_date")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="UVEST stage 12 — standalone HTML report (TICK-010).")
    parser.add_argument("--input", required=True,
                        help="Path to current_trade.json.")
    parser.add_argument("--payload", action="append", default=[],
                        help="A stage output JSON to render (repeatable).")
    parser.add_argument("--out-dir", default="reports",
                        help="Directory for report_{SYMBOL}_{DATE}.html.")
    args = parser.parse_args(argv)

    try:
        trade = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(ERR_INPUT, 2, f"unreadable trade input: {exc}")
    if not isinstance(trade, dict):
        die(ERR_INPUT, 2, "trade input must be a JSON object")
    for field in REQUIRED_FIELDS:
        if not trade.get(field):
            die(ERR_INPUT, 2, f"trade input is missing {field}")

    payloads: dict[str, dict] = {}
    for raw in args.payload:
        path = Path(raw)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            die(ERR_INPUT, 2, f"unreadable payload {raw}: {exc}")
        if not isinstance(doc, dict):
            die(ERR_INPUT, 2, f"payload is not a JSON object: {raw}")
        payloads[path.name] = doc

    symbol = str(trade["symbol"]).upper()
    day = str(trade["recommended_date"]).replace("-", "")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / f"report_{symbol}_{day}.html"

    text = render(trade, payloads)
    raw = text.encode("utf-8")
    artifact.write_bytes(raw)
    print(json.dumps({"artifact": artifact.as_posix(), "bytes": len(raw)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
