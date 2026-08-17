"""End-to-end pipeline runner.

Executes the blueprint from BRD.md plus the learning loop:
    Step 1: Read `current_trade.json`
    Step 2: Run scripts/1_math_and_rr.py            -> script1_out.json
    Step 3: Run scripts/2_market_data.py            -> script2_out.json
    Step 4: Run scripts/3_historical_audit.py       -> script3_out.json
    Step 5: Run scripts/4_score_calculator.py       -> final score
    Step 6: Run scripts/5_track_and_resolve.py      -> track & resolve recos, stats
    Step 7: Render synthesized analysis to Markdown (stdout + decision_report.md)

Usage:
    python scripts/run_analysis.py [path/to/current_trade.json] [--no-track]
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
    OUT5_FILE,
    OUT6_FILE,
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
    ("scripts/6_backtest_setup.py", OUT6_FILE, "similar-setup backtest"),
]


def run_step(script: str, out_file: Path, description: str, *extra_args: str) -> dict[str, Any]:
    """Run one pipeline script, capture its JSON stdout, and persist it."""
    result = subprocess.run(
        [PYTHON, script, *extra_args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # a stray non-UTF-8 byte must never crash the orchestrator
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
                  audit_res: dict[str, Any], score_res: dict[str, Any],
                  track_res: dict[str, Any] | None = None,
                  backtest_res: dict[str, Any] | None = None) -> str:
    symbol = data["symbol"]
    expiry = data.get("expiry", "")
    inst = data.get("instrument_type", "")
    header_label = f"{symbol} {expiry} {inst}".strip()

    score = score_res["final_confidence_score"]
    max_possible = score_res.get("max_possible_score", 100)
    effective = score_res.get("effective_score", score)
    decision = score_res["decision"]
    if decision == "GO (EXECUTE FULL SIZE)":
        emoji = "🟢"
    elif decision == "CONDITIONAL GO (REDUCE SIZE 50%)":
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
    ]

    # ---- Recommendation identity (same reco re-pasted across days) ---------
    identity = (track_res or {}).get("identity")
    if identity:
        src = identity.get("source") or "univest"
        rec_on = (identity.get("recommended_on") or "")[:10]
        rec_label = f"reco dated {rec_on}" if rec_on else "reco date unknown"
        if identity.get("is_repeat"):
            lines.append(
                f"*🪪 Reco identity: **{symbol} {inst} {data.get('action', '')}** · source: {src} · "
                f"{rec_label} · analyzed **{identity.get('analysis_count', 1)}×** "
                f"(last {identity.get('last_analyzed_on') or 'today'}) — the same recommendation re-pasted "
                "on a later day; price/score refreshed, original reco date preserved.*"
            )
        else:
            lines.append(
                f"*🪪 Reco identity: **{symbol} {inst} {data.get('action', '')}** · source: {src} · "
                f"{rec_label} · first time this reco is analyzed.*"
            )
        lines.append("")

    lines += [
        f"### **FINAL CONFIDENCE SCORE: {score} / {max_possible}**"
        + (f" ({effective}% of available points)" if max_possible < 100 else ""),
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
    ]

    # ---- Expiry row (event-safety context) --------------------------------
    expiry_label = data.get("expiry")
    days = math_res.get("days_to_expiry")
    if days is not None:
        if days >= 5:
            expiry_impact = "✅ ≥5 days to expiry — clean window"
        elif days >= 3:
            expiry_impact = f"⚠️ **Only {days} days to expiry** — expiry-week whipsaw risk"
        elif days < 0:
            expiry_impact = f"❌ **Expiry already passed ({abs(days)} day(s) ago)** — stale recommendation"
        else:
            expiry_impact = f"❌ **Only {days} day(s) to expiry** — expiry-day risk; decision capped at CONDITIONAL GO"
        lines.append(
            f"| **Expiry** | {expiry_label or 'n/a'} | **{days} day(s) away** | {expiry_impact} |"
        )
    else:
        lines.append(
            f"| **Expiry** | {expiry_label or 'n/a'} | — | ⏭️ Waived — no expiry supplied, event-safety check unscored |"
        )
    lines += ["", "---", ""]

    # ---- Historical audit section ----------------------------------------
    audit_section = ["### 📊 Univest Historical Performance Audit", ""]
    if score_res.get("empirical_mode"):
        audit_section += [
            f"* **`{symbol} {inst}`:** the ticker-level track record is **superseded in empirical mode** — "
            "the historical-edge check is scored from the measured similar-setup backtest below "
            "(a real probability, not a rubric opinion). Ticker outcomes still accumulate in the "
            "learning loop for future calibration.",
        ]
    elif audit_res.get("status") == "NO_HISTORICAL_DATA_FOR_TICKER":
        audit_section += [
            f"* **`{symbol} {inst}`:** No resolved recommendations in the track record yet.",
            "* **Verdict:** Historical edge **waived** — no points granted, no penalty assumed. "
            "The check activates once real outcomes accumulate for this ticker.",
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
        source = "real tracked record" if audit_res.get("data_source") == "tracked" else "bootstrap seed data"
        audit_section += [
            f"* **Ticker Historical Win Rate:** {audit_res['historical_win_rate']}% ({audit_res['ticker_sample_size']} Trades sampled · {source})",
            f"* **Avg Win:** +{indian_currency(audit_res['avg_win_inr'])} | **Avg Loss:** -{indian_currency(audit_res['avg_loss_inr'])}",
            f"* **Historical Expectancy:** **{exp_str} / trade**",
            f"* **Verdict:** {verdict}",
        ]
    lines += audit_section + ["", "---", ""]

    # ---- Similar-setup backtest (empirical success probability) ------------
    if backtest_res and "error" not in backtest_res:
        s = backtest_res.get("sample", {})
        ov = s.get("overall", {})
        by_trend = s.get("by_trend", {})
        trend_state = backtest_res.get("trend_state")
        lines += ["### 📈 Similar-Setup Backtest (what history says about this shape)", ""]
        if ov.get("n"):
            sig = (f"{backtest_res.get('action')} · R:R {backtest_res.get('rr')} · "
                   f"stop {backtest_res.get('stop_atr_ratio')}×ATR · "
                   f"window {backtest_res.get('window_bars')} trading bar(s)"
                   + (" (from days-to-expiry)" if backtest_res.get("window_origin") == "expiry" else ""))
            avg = ov.get("avg_pnl_inr", 0)
            net = ov.get("net_pnl_inr", 0)
            avg_str = f"+{indian_currency(avg)}" if avg >= 0 else f"-{indian_currency(abs(avg))}"
            net_str = f"+{indian_currency(net)}" if net >= 0 else f"-{indian_currency(abs(net))}"
            lines.append(f"* **Setup signature:** {sig}")
            lines.append(
                f"* **Overall:** {ov['n']} similar setups replayed · "
                f"**{ov['success_rate']}%** hit target before stop · "
                f"**{ov['win_rate']}%** (95% CI {ov.get('ci_low')}–{ov.get('ci_high')}%) resolved as wins "
                f"(incl. window-end settlement) · "
                f"{ov['target_first']} target-first / {ov['stop_first']} stop-first / "
                f"{ov['settled_at_window_end']} settled · avg P&L {avg_str} · net P&L (all replays) {net_str}"
            )
            matched = by_trend.get(trend_state, {}) if trend_state else {}
            if matched.get("n"):
                lines.append(
                    f"* **{trend_state} trend context (matches this reco):** {matched['n']} setups · "
                    f"**{matched['success_rate']}%** hit target before stop · "
                    f"**{matched['win_rate']}%** (95% CI {matched.get('ci_low')}–{matched.get('ci_high')}%) wins"
                )
            other_key = "BEARISH" if trend_state == "BULLISH" else "BULLISH"
            other = by_trend.get(other_key, {})
            if other.get("n"):
                lines.append(
                    f"* **Opposite trend context ({other_key}):** {other['n']} setups · "
                    f"{other['success_rate']}% hit target first · {other['win_rate']}% wins"
                )
            for note in backtest_res.get("notes", []):
                lines.append(f"  ⓘ {note}")

            emp = score_res.get("empirical")
            if score_res.get("empirical_mode") and emp:
                if emp["ci_low"] > emp["break_even_win_rate"]:
                    verdict = "above break-even and the CI lower bound clears it — the edge is statistically confirmed."
                elif emp["win_rate"] > emp["break_even_win_rate"]:
                    verdict = "above break-even but the CI lower bound does not clear it — positive but NOT confirmed."
                else:
                    verdict = "not above break-even — no confirmed edge."
                lines.append(
                    f"* **Break-even check:** this setup needs to win **{emp['break_even_win_rate']}%** of the time "
                    f"(net of costs) to break even. The empirical win rate scored is **{emp['win_rate']}%** "
                    f"(95% CI {emp['ci_low']}–{emp['ci_high']}%, n={emp['n']}, bucket {emp['bucket']}) — {verdict}"
                )
            lines.append(
                "* **How to read this:** the win rate is a measured probability from comparable setups, "
                "not a rubric opinion — but the sample is correlated (overlapping windows), so treat it as "
                "evidence, not certainty. In empirical mode this probability REPLACES the ticker-track-record "
                "check and the trend/event-safety checks (their signals are embedded in the backtest) are waived."
            )
        else:
            lines.append("* No comparable setups found in the available history.")
        lines += ["", "---", ""]
    elif backtest_res and "error" in backtest_res:
        lines += ["### 📈 Similar-Setup Backtest", "",
                  f"* ⚠️ Backtest unavailable: {backtest_res['error']}", "", "---", ""]

    # ---- Self-tracked track record & learning loop -------------------------
    if track_res and "error" not in track_res:
        tr = track_res.get("track_record", {})
        da = track_res.get("decision_accuracy", {})
        rtr = track_res.get("resolved_this_run", {})
        lines += ["### 📈 Self-Tracked Track Record & Learning Loop", ""]

        if track_res.get("resolution_status") == "offline":
            lines.append("* ⚠️ **Prices could not be refreshed this run (offline)** — open recommendations remain pending; nothing was resolved.")
        if track_res.get("resolved_this_run", {}).get("count"):
            res_lines = []
            for d in rtr.get("details", []):
                sign = "+" if d["pnl_inr"] >= 0 else "-"
                res_lines.append(f"{d['symbol']} → {d['outcome']} {sign}{indian_currency(abs(d['pnl_inr']))}")
            lines.append(
                f"* **Resolved this run:** {rtr['count']} (✅ {rtr['wins']} wins / ❌ {rtr['losses']} losses) — "
                + "; ".join(res_lines)
            )

        exp = tr.get("net_expectancy_per_trade", 0)
        exp_str = f"+{indian_currency(exp)}" if exp >= 0 else f"-{indian_currency(abs(exp))}"
        net = tr.get("net_pnl_inr", 0)
        net_str = f"+{indian_currency(net)}" if net >= 0 else f"-{indian_currency(abs(net))}"
        lines.append(
            f"* **Recommendations tracked:** {track_res.get('total_tracked', 0)} total · "
            f"{track_res.get('open_count', 0)} open · {track_res.get('resolved_count', 0)} resolved"
        )
        if tr.get("sample_size"):
            lines.append(
                f"* **Real track-record expectancy:** **{exp_str} / trade** "
                f"(win rate {tr['win_rate']}% over {tr['sample_size']} resolved) · net P&L {net_str}"
            )
        else:
            lines.append("* **Real track record:** no resolved recommendations yet — keep analyzing, outcomes will accumulate here.")

        if da.get("approved_resolved"):
            hit = da["approved_hit_rate"]
            lines.append(f"* **Decision accuracy:** approved (GO/COND) hit rate **{hit}%** ({da['approved_wins']}/{da['approved_resolved']})")
        if da.get("rejected_resolved"):
            missed = da.get("missed_profit_inr", 0)
            avoided = da.get("avoided_losses_inr", 0)
            lines.append(
                f"* **What you missed / saved on NO-GO calls:** {da['rejected_wins_missed']} later won "
                f"(missed ≈ {indian_currency(missed)}) · {da['rejected_losses_avoided']} later lost "
                f"(avoided ≈ {indian_currency(avoided)})"
            )

        lines += [
            "* **How the loop improves decisions:** every analyzed reco is recorded as OPEN; future runs re-check it "
            "against live prices to mark WIN/LOSS. Script 3's audit uses this real record (seed data is only a "
            "bootstrap until real resolutions exist), so the historical-edge check gets sharper with every run.",
            "* **P&L is net of modeled transaction costs** (~0.09% of notional for FUT, ~0.25% for OPT, see "
            "`common.estimate_costs`). Recos that never hit target or stop are settled at the last close on/before "
            "expiry once data covers it — nothing stays OPEN past its contract.",
        ]

        # ---- Score calibration (is the confidence honest?) -----------------
        cal = track_res.get("calibration", {}) if isinstance(track_res, dict) else {}
        if cal and cal.get("resolved_tracked"):
            total = cal.get("total", {})
            lines += ["", "### 🎯 Score Calibration (is the confidence honest?)", ""]
            lines.append(
                f"* **Tracked resolved recos:** {total['n']} · overall win rate "
                f"**{total['win_rate']}%** (95% CI {total['ci_low']}–{total['ci_high']}% · "
                f"smoothed {total['smoothed_win_rate']}%)"
            )
            short_names = {
                "GO (EXECUTE FULL SIZE)": "GO",
                "CONDITIONAL GO (REDUCE SIZE 50%)": "CONDITIONAL",
                "NO-GO (REJECT)": "NO-GO",
            }
            for label, b in cal.get("buckets", {}).items():
                short = short_names.get(label, label)
                if b["n"] < 5:
                    lines.append(
                        f"* **{short}:** {b['n']} resolved ({b['wins']}W/{b['losses']}L) · "
                        f"win rate {b['win_rate']}% · insufficient sample (<5) for a reliable estimate"
                    )
                else:
                    lines.append(
                        f"* **{short}:** win rate **{b['win_rate']}%** ({b['wins']}/{b['n']}) · "
                        f"95% CI {b['ci_low']}–{b['ci_high']}% · smoothed {b['smoothed_win_rate']}%"
                    )
            lines.append(
                "* **What this tells you:** if GO recos historically won ~60%, \"GO\" is a quality label, "
                "not a probability — size accordingly. The decision bands are re-tunable from this data as it accumulates."
            )
            lines += ["", "---", ""]
        else:
            lines += ["", "### 🎯 Score Calibration (is the confidence honest?)", "",
                      "* No resolved tracked recommendations yet — calibration activates once outcomes accumulate. "
                      "Until then treat the score as a rubric opinion, not a measured probability.", "", "---", ""]

    # ---- Rejection reasons ------------------------------------------------
    lines += ["### ❌ Rejection Reasons & Flags", ""]
    details = score_res.get("rejection_details") or []
    hard_details = [d for d in details if not d.get("waived")]
    if hard_details:
        for i, d in enumerate(hard_details, start=1):
            lines.append(f"{i}. **{d['category']} (Score: {d['earned']}/{d['max']}):** {d['reason']}")
    else:
        lines.append("No rejection flags raised — all checks passed.")
    lines += ["", "---", ""]

    # ---- Waived checks (no free points) ------------------------------------
    waived_details = [d for d in details if d.get("waived")]
    if waived_details:
        lines += ["### ⏭️ Waived Checks (no points granted, no penalty)", ""]
        for i, d in enumerate(waived_details, start=1):
            lines.append(f"{i}. **{d['category']} (0/{d['max']}):** {d['reason']}")
        lines += ["", "---", ""]

    # ---- What would change the decision (flip table) -----------------------
    flip = score_res.get("flip_table") or {}
    flip_rows = flip.get("rows") or []
    if flip_rows:
        lines += ["### 🔀 What Would Change the Decision", ""]
        max_possible = score_res.get("max_possible_score", 100)
        dir_words = {"below": "at or below", "above": "at or above"}
        for r in flip_rows:
            kind = r.get("kind")
            gain = r["gain"]
            after_txt = f"+{gain} → {r['score_after']}/{max_possible} → **{r['decision_after']}**"
            if kind == "price":
                act = f"Enter {dir_words.get(r.get('direction'), 'at')} {indian_currency(r['price'])}"
                tag = ""
            elif kind == "range":
                act = f"Enter within {indian_currency(r['range_min'])} – {indian_currency(r['range_max'])}"
                tag = ""
            elif kind == "structural":
                act = r.get("detail", "")
                tag = " (adjusts the trade, not the entry price)"
            elif kind == "market":
                act = r.get("detail", "")
                tag = " (market condition — not triggerable by price)"
            else:
                act = r.get("detail", "")
                tag = " (not triggerable by price or timing)"
            mark = "⚠ " if not r.get("achievable", True) else ""
            lines.append(f"* {mark}**{r['label']}** ({r['earned']}/{r['max']}): {act} → {after_txt}{tag}")
        for c in flip.get("conflicts", []):
            lines.append(f"* ⛔ **Conflict:** {c}")
        bc = flip.get("best_case")
        if bc:
            lines.append(
                f"* 🎯 **Best case by entry timing alone:** {bc.get('zone', indian_currency(bc['price']))} → "
                f"{bc['score']}/{max_possible} → **{bc['decision']}**. {bc.get('note', '')}"
            )
        lines.append(f"* *{flip.get('note', '')}*")
        lines += ["", "---", ""]

    # ---- Actionable recommendation ----------------------------------------
    lines += ["### 💡 Agent Actionable Recommendation", ""]
    has_caveats = bool([d for d in (score_res.get("rejection_details") or []) if not d.get("waived")])
    primary = {
        "GO (EXECUTE FULL SIZE)": (
            "**EXECUTE THE TRADE** at market with full size — every discipline, volatility and edge check passed."
            if not has_caveats else
            "**EXECUTE THE TRADE** at market with full size — the score is strong, but review the caveats below before sizing in."
        ),
        "CONDITIONAL GO (REDUCE SIZE 50%)": "**EXECUTE AT HALF SIZE (50%)** — the setup is viable but has at least one sub-optimal component. Reduce size, keep the stop-loss.",
    }.get(decision, "**REJECT THE TRADE.** Do not place an order at the live price.")
    lines.append(f"* **Primary Recommendation:** {primary}")

    if outside and live < range_min:
        lines.append(
            f"* **Alternative Execution:** The live price is **below** the recommended range (favorable). "
            "If the setup thesis is intact, consider a limit order inside the range rather than chasing."
        )
    if not market_available:
        lines.append("* **Data caveat:** Live market data could not be fetched — volatility/trend scores were waived. Re-run once connectivity is restored.")

    act = flip.get("activation") if flip_rows else None
    if act:
        order = "LIMIT BUY" if (math_res.get("action") or "BUY").upper() == "BUY" else "LIMIT SELL"
        lines.append(f"* **Standing-order trigger:** {act.get('note', '')} Run `python scripts/watch.py` to monitor the live price against this trigger.")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    argv = sys.argv[1:]
    no_track = "--no-track" in argv
    positional = [a for a in argv if not a.startswith("--")]
    input_path = Path(positional[0]) if positional else INPUT_FILE
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

    print("  Steps 2-5: Running modular script suite")
    run_step("scripts/1_math_and_rr.py", OUT1_FILE, "risk/reward math")
    run_step("scripts/2_market_data.py", OUT2_FILE, "live market context")
    run_step("scripts/3_historical_audit.py", OUT3_FILE, "historical audit")
    run_step("scripts/6_backtest_setup.py", OUT6_FILE, "similar-setup backtest")

    print("  Step 6: Scoring decision")
    score_res = run_step("scripts/4_score_calculator.py", OUT4_FILE, "confidence score")
    if "error" in score_res:
        print(f"❌ {score_res['error']}", file=sys.stderr)
        return 1

    print("  Step 7: Recording track record & resolving open recos")
    track_args = ["--score", str(score_res["final_confidence_score"]), "--decision", score_res["decision"]]
    if no_track:
        track_args.append("--no-track")
    track_res = run_step("scripts/5_track_and_resolve.py", OUT5_FILE, "track & resolve", *track_args)

    print("  Step 8: Rendering Decision Report\n")
    report = render_report(data, load_json(OUT1_FILE), load_json(OUT2_FILE), load_json(OUT3_FILE),
                           score_res, track_res, load_json(OUT6_FILE))
    REPORT_FILE.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n📄 Report saved to {REPORT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
