"""End-to-end pipeline runner.

Executes the exact blueprint from BRD.md:
    Step 1: Read `current_trade.json`
    Step 2: Run scripts/1_math_and_rr.py          -> script1_out.json
    Step 3: Run scripts/2_market_data.py          -> script2_out.json
    Step 4: Run scripts/3_historical_audit.py     -> script3_out.json
    Step 5: Run scripts/4_score_calculator.py     -> final score
    Step 6: Render synthesized analysis to Markdown (stdout + decision_report.md)

Usage:
    python scripts/run_analysis.py [path/to/current_trade.json]
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    INPUT_FILE,
    OUT1_FILE,
    OUT2_FILE,
    OUT3_FILE,
    OUT4_FILE,
    PROJECT_ROOT,
    REPORT_FILE,
    indian_currency,
    load_json,
    save_json,
)

PYTHON = sys.executable

# Windows consoles default to cp1252; force UTF-8 so emoji/report glyphs render.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

# (script, output_file, description)
PIPELINE = [
    ("scripts/1_math_and_rr.py", OUT1_FILE, "risk/reward math"),
    ("scripts/2_market_data.py", OUT2_FILE, "live market context"),
    ("scripts/3_historical_audit.py", OUT3_FILE, "historical audit"),
]


def run_step(script: str, out_file: Path, description: str) -> dict[str, Any]:
    """Run one pipeline script, capture its JSON stdout, and persist it."""
    result = subprocess.run(
        [PYTHON, script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"error": f"Script produced no valid JSON: {result.stdout.strip()}"}

    save_json(out_file, payload)
    if result.returncode != 0 or "error" in payload:
        print(f"  ⚠ {script} [{description}]: {payload.get('error', 'exit code %d' % result.returncode)}")
    else:
        print(f"  ✓ {script} [{description}]")
    return payload


def render_report(data: dict[str, Any], math_res: dict[str, Any], market_res: dict[str, Any],
                  audit_res: dict[str, Any], score_res: dict[str, Any]) -> str:
    symbol = data["symbol"]
    expiry = data.get("expiry", "")
    inst = data.get("instrument_type", "")
    header_label = f"{symbol} {expiry} {inst}".strip()

    score = score_res["final_confidence_score"]
    decision = score_res["decision"]
    if score >= 80:
        emoji = "🟢"
    elif score >= 65:
        emoji = "🟡"
    else:
        emoji = "🔴"

    market_available = "error" not in market_res

    # ---- Quantitative table rows -----------------------------------------
    live = float(data["live_price"])
    entry = float(data["entry_price"])
    sl = float(data["stoploss"])
    target = float(data["target"])
    lot = float(data["lot_size"])
    lot_label = str(int(lot)) if lot == int(lot) else f"{lot:,.0f}"
    range_min = math_res.get("entry_range_min", entry)
    range_max = math_res.get("entry_range_max", entry)
    outside = math_res.get("outside_entry_range", False)

    entry_impact = "✅ Inside recommended range"
    if outside:
        if live > range_max:
            entry_impact = f"⚠️ **Outside Range** (+{indian_currency(live - range_max)} slippage)"
        else:
            entry_impact = f"⚠️ **Outside Range** ({indian_currency(range_min - live)} below lower bound)"

    live_rr = math_res.get("live_rr", 0.0)
    raw_rr = math_res.get("raw_rr", 0.0)
    rr_impact = "✅ Reward exceeds risk" if live_rr >= 1.5 else (
        "⚠️ Sub-optimal reward profile" if live_rr >= 1.0 else "❌ **Negative Symmetry** (risking more than potential reward)"
    )

    if market_available:
        atr = market_res.get("current_atr")
        atr_ratio = market_res.get("atr_sl_ratio")
        vol_impact = "✅ **Pass** (SL is ~{:.1f}x ATR; safe from daily noise)".format(atr_ratio) \
            if not market_res.get("is_tight_sl", False) else "❌ **Fail** (SL tighter than 1x ATR — noise stopout risk)"
        trend = "✅ Above 50 EMA (BULLISH)" if market_res.get("above_50_ema", False) else "❌ Below 50 EMA (BEARISH)"
        vol_cell = f"Daily ATR: {indian_currency(atr)}" if atr else "n/a"
    else:
        vol_impact = "⚠️ Market data unavailable — check waived"
        trend = "⚠️ Market data unavailable — check waived"
        vol_cell = "n/a"

    lines = [
        f"# 🛡️ TRADE VALIDATION REPORT: {header_label}",
        "",
        f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"Action: {data.get('action', 'n/a')} · Lot size: {lot_label} · Margin: {indian_currency(data.get('margin', 0))}*",
        "",
        f"### **FINAL CONFIDENCE SCORE: {score} / 100**",
        f"### **DECISION: {emoji} {decision}**",
        "",
        "---",
        "",
        "### 🔍 Quantitative & Risk Analysis",
        "",
        "| Parameter | Recommended Value | Current Live Value | Impact / Analysis |",
        "| :--- | :--- | :--- | :--- |",
        f"| **Entry Price** | {indian_currency(entry)} (Range {indian_currency(range_min)} – {indian_currency(range_max)}) | **{indian_currency(live)}** | {entry_impact} |",
        f"| **Stoploss** | {indian_currency(sl)} | {indian_currency(sl)} | Risk: **{indian_currency(math_res.get('live_risk_per_share', 0))} / share** ({indian_currency(math_res.get('risk_per_lot_inr', 0))} / lot) |",
        f"| **Target** | {indian_currency(target)} | {indian_currency(target)} | Reward: **{indian_currency(math_res.get('live_reward_per_share', 0))} / share** ({indian_currency(math_res.get('reward_per_lot_inr', 0))} / lot) |",
        f"| **Real Risk:Reward** | 1 : {raw_rr:.2f} (at entry) | **1 : {live_rr:.2f} (live)** | {rr_impact} |",
        f"| **Volatility Check** | {vol_cell} | SL distance: {indian_currency(math_res.get('live_risk_per_share', 0))} | {vol_impact} |",
        f"| **Trend Context** | 50 EMA: {indian_currency(market_res.get('ema_50', 0)) if market_available else 'n/a'} | Close: {indian_currency(market_res.get('close_price', 0)) if market_available else 'n/a'} | {trend} |",
        "",
        "---",
    ]

    # ---- Historical audit section ----------------------------------------
    audit_section = ["### 📊 Univest Historical Performance Audit", ""]
    if audit_res.get("status") == "NO_HISTORICAL_DATA_FOR_TICKER":
        audit_section += [
            f"* **`{symbol} {inst}`:** No historical recommendations found in `univest_history.db`.",
            f"* **Verdict:** Treated as neutral (benchmark win rate {audit_res.get('win_rate', 0.65) * 100:.0f}%) — no track record to add or subtract edge.",
        ]
    else:
        edge = audit_res["edge_is_positive"]
        exp = audit_res["net_expectancy_per_trade"]
        exp_str = f"+{indian_currency(exp)}" if exp >= 0 else f"-{indian_currency(abs(exp))}"
        verdict = (
            f"Positive expectancy of **{exp_str} / trade** — "
            "Univest signals for this setup have a durable mathematical edge."
            if edge else
            "Due to bad R:R on Univest's signals for this ticker, taking these trades over time results in "
            "**negative overall profitability despite a >50% win rate.**"
        )
        audit_section += [
            f"* **Ticker Historical Win Rate:** {audit_res['historical_win_rate']}% ({audit_res['ticker_sample_size']} Trades sampled)",
            f"* **Avg Win:** +{indian_currency(audit_res['avg_win_inr'])} | **Avg Loss:** -{indian_currency(audit_res['avg_loss_inr'])}",
            f"* **Historical Expectancy:** **{exp_str} / trade**",
            f"* **Verdict:** {verdict}",
        ]
    lines += audit_section + ["", "---", ""]

    # ---- Rejection reasons ------------------------------------------------
    lines += ["### ❌ Rejection Reasons & Flags", ""]
    details = score_res.get("rejection_details") or []
    if details:
        for i, d in enumerate(details, start=1):
            lines.append(f"{i}. **{d['category']} (Score: {d['earned']}/{d['max']}):** {d['reason']}")
    else:
        lines.append("No rejection flags raised — all checks passed.")
    lines += ["", "---", ""]

    # ---- Actionable recommendation ----------------------------------------
    lines += ["### 💡 Agent Actionable Recommendation", ""]
    has_caveats = bool(score_res.get("rejection_details"))
    primary = {
        "GO (EXECUTE FULL SIZE)": (
            "**EXECUTE THE TRADE** at market with full size — every discipline, volatility and edge check passed."
            if not has_caveats else
            "**EXECUTE THE TRADE** at market with full size — the score is strong, but review the caveats below before sizing in."
        ),
        "CONDITIONAL GO (REDUCE SIZE 50%)": "**EXECUTE AT HALF SIZE (50%)** — the setup is viable but has at least one sub-optimal component. Reduce size, keep the stop-loss.",
    }.get(decision, "**REJECT THE TRADE.** Do not place an order at the live price.")
    lines.append(f"* **Primary Recommendation:** {primary}")

    if outside and decision != "GO (EXECUTE FULL SIZE)" and live > range_max:
        # Compute a limit price that re-aligns R:R to >= 1.5
        alt_entry = (target + 1.5 * sl) / 2.5
        alt_risk = alt_entry - sl
        alt_reward = target - alt_entry
        alt_rr = alt_reward / alt_risk if alt_risk > 0 else 0
        lines.append(
            f"* **Alternative Execution (If required):** Do not place a Market Order. Place a **LIMIT BUY at "
            f"{indian_currency(alt_entry)}** (re-aligns R:R to 1:{alt_rr:.2f} with "
            f"{indian_currency(alt_risk * lot)} risk vs {indian_currency(alt_reward * lot)} target)."
        )
    if outside and live < range_min:
        lines.append(
            f"* **Alternative Execution:** The live price is **below** the recommended range (favorable). "
            "If the setup thesis is intact, consider a limit order inside the range rather than chasing."
        )
    if not market_available:
        lines.append("* **Data caveat:** Live market data could not be fetched — volatility/trend scores were waived. Re-run once connectivity is restored.")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else INPUT_FILE
    if not input_path.exists():
        print(f"❌ Input file not found: {input_path}", file=sys.stderr)
        print("   Copy current_trade.json and edit it, or paste a raw trade into input.txt.", file=sys.stderr)
        return 1

    # Blueprint Step 1: promote the trade details into current_trade.json
    # so every downstream script reads one canonical input.
    if input_path.resolve() != INPUT_FILE.resolve():
        INPUT_FILE.write_text(input_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  Step 1: wrote {input_path.name} -> current_trade.json  ✓")

    data = load_json(INPUT_FILE)
    print(f"▶ Analyzing trade: {data.get('symbol', '?')} {data.get('expiry', '')} {data.get('instrument_type', '')}")
    print("  Step 1: Read current_trade.json  ✓")

    print("  Step 2-4: Running modular script suite")
    run_step("scripts/1_math_and_rr.py", OUT1_FILE, "risk/reward math")
    run_step("scripts/2_market_data.py", OUT2_FILE, "live market context")
    run_step("scripts/3_historical_audit.py", OUT3_FILE, "historical audit")

    print("  Step 5: Scoring decision")
    score_res = run_step("scripts/4_score_calculator.py", OUT4_FILE, "confidence score")
    if "error" in score_res:
        print(f"❌ {score_res['error']}", file=sys.stderr)
        return 1

    print("  Step 6: Rendering Decision Report\n")
    report = render_report(data, load_json(OUT1_FILE), load_json(OUT2_FILE), load_json(OUT3_FILE), score_res)
    REPORT_FILE.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n📄 Report saved to {REPORT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
