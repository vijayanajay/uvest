"""Daily screener orchestrator.

Runs the screen_* family in order (universe -> per-stock backtest ->
rank), captures each JSON contract to screen_*_out.json, and renders
screen_report.md — the input for the daily sentiment prompt
(prompt_daily.md).

    python scripts/run_screen.py [--top 20] [--horizon 10] [--hit 4.0]
        [--stop-atr 1.5] [--period 2y] [--long-only] [--sort hit] [--record]

--record additionally runs screen_record.py --record after ranking
(records today's top list; picks from screen_picks.json are honoured if
present — normally you run the prompt between rank and record).

The report states the honest caveats: the ranked list is a filter, not an
alpha claim; per-stock hit rates have ~zero out-of-sample persistence, so
the edge — if any — must be confirmed live by screen_record.py, and the
sentiment picks are the experiment.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PROJECT_ROOT, indian_currency, load_json, save_json  # noqa: E402

PYTHON = sys.executable

UNIVERSE_OUT = PROJECT_ROOT / "screen_universe_out.json"
STATS_OUT = PROJECT_ROOT / "screen_stats_out.json"
RANK_OUT = PROJECT_ROOT / "screen_rank_out.json"
REGIME_OUT = PROJECT_ROOT / "screen_regime_out.json"
QUALITY_OUT = PROJECT_ROOT / "screen_day_quality_out.json"
REGIME_CLASS_OUT = PROJECT_ROOT / "screen_regime_classify_out.json"
SCALP_VIAB_OUT = PROJECT_ROOT / "screen_scalp_viability_out.json"
GAP_ANALYSIS_OUT = PROJECT_ROOT / "screen_gap_analysis_out.json"
VOL_PROFILE_OUT = PROJECT_ROOT / "screen_volume_profile_out.json"
MEAN_REV_OUT = PROJECT_ROOT / "screen_mean_reversion_out.json"
SIGNALS_OUT = PROJECT_ROOT / "screen_signals_out.json"
READINESS_OUT = PROJECT_ROOT / "screen_entry_readiness_out.json"
COND_PROB_OUT = PROJECT_ROOT / "screen_conditional_probability_out.json"
DAY_STATS_OUT = PROJECT_ROOT / "screen_day_statistics_out.json"
DELIVERY_OUT = PROJECT_ROOT / "screen_delivery_volume_out.json"
REPORT_FILE = PROJECT_ROOT / "screen_report.md"

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

VERDICT_EMOJI = {"EDGE CONFIRMED": "🟢", "POSITIVE BUT UNCONFIRMED": "🟡", "NO EDGE": "🔴"}
RISK_EMOJI = {"LOW": "🟢", "MEDIUM": "🟠", "HIGH": "🔴"}


def run_step(script: str, out_file: Path, description: str, *extra: str) -> dict[str, Any]:
    result = subprocess.run(
        [PYTHON, script, *extra], cwd=PROJECT_ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
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


def _cell_sort(item: tuple[str, Any]) -> tuple[int, float]:
    hz, hit = item[0].split(":")
    return int(hz), float(hit)


REGIME_EMOJI = {"RISK-ON": "🟢", "CAUTIOUS": "🟡", "RISK-OFF": "🔴", "UNKNOWN": "⚪"}


def _fmt_range(r: dict[str, Any] | None) -> str:
    """Compact "median (IQR), P10/P90" string from a returns dict."""
    if not r or not r.get("n"):
        return "–"
    return f"{r['median']:+}% ({r['p25']:+}…{r['p75']:+}) · P10/P90 {r['p10']:+}/{r['p90']:+}%"


def render_report(universe: dict[str, Any], stats: dict[str, Any], rank: dict[str, Any],
                  regime: dict[str, Any], quality: dict[str, Any] | None = None,
                  signals: dict[str, Any] | None = None,
                  readiness: dict[str, Any] | None = None,
                  regime_class: dict[str, Any] | None = None,
                  scalp_viab: dict[str, Any] | None = None,
                  gap_analysis: dict[str, Any] | None = None,
                  vol_profile: dict[str, Any] | None = None,
                  mean_rev: dict[str, Any] | None = None,
                  day_stats: dict[str, Any] | None = None,
                  cond_prob: dict[str, Any] | None = None,
                  delivery: dict[str, Any] | None = None) -> str:
    params = rank.get("params", {})
    hz, hit = params.get("horizon", 3), params.get("hit", 4.0)
    lines = [
        "# 📡 DAILY SCREEN REPORT — 1–3 Day Directional Candidates",
        "",
        f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"universe {universe.get('count', '?')} ({universe.get('source', '?')}) · "
        f"scanned {stats.get('scanned', 0)} · skip {len(stats.get('skipped', []))}*",
        f"*Setup shape: stop = {params.get('stop_atr', 1.5)}×ATR(14) · costs {params.get('cost_rate', 0.0009)*100:.2f}% · "
        f"ranking cell {hz}-bar × {hit}% · top {params.get('top', 20)}*",
        "",
        "---",
        "",
    ]
    # Market regime header — the gate the daily prompt applies.
    ov = regime.get("overall", {})
    nif = regime.get("nifty", {})
    vix = regime.get("vix", {})
    brd = regime.get("breadth", {})
    label = ov.get("label", "UNKNOWN")
    emoji = REGIME_EMOJI.get(label, "⚪")
    regime_bits = [
        f"NIFTY {nif.get('trend', 'UNKNOWN')}",
        f"VIX {vix.get('regime', 'UNKNOWN')}",
        f"breadth {brd.get('pct', '?')}%" if brd.get("pct") is not None else "breadth n/a",
    ]
    lines.append("### 🌡️ Market regime (the gate)")
    lines.append("")
    lines.append(f"**{emoji} {label}** · " + " · ".join(regime_bits))
    if ov.get("reasons"):
        lines.append(f"*Risk-off flags: {'; '.join(ov['reasons'])}.*")
    if label == "RISK-OFF":
        lines.append("*Gate: max 1 pick, prefer 0, only EDGE CONFIRMED with LOW/MEDIUM risk — see prompt_daily.md.*")
    elif label == "CAUTIOUS":
        lines.append("*Gate: max 2 picks, no HIGH-risk rows (size stays >= the 30% floor — the regime lever is pick count).*")

    elif label == "UNKNOWN":
        lines.append("*Regime data unavailable — prompt falls back to neutral rules.*")
    lines += ["", "---", ""]

    # Day Quality section (with conditions momentum overlay)
    if quality and "error" not in quality:
        dq_score = quality.get("score", 0)
        dq_static = quality.get("static_score", dq_score)
        dq_verdict = quality.get("verdict", "UNKNOWN")
        dq_emoji = {"STRONG": "🟢", "TRADING": "🟡", "WASH": "🟠", "AVOID": "🔴"}.get(dq_verdict, "⚪")
        lines.append("### 📅 Day Quality — should I trade today?")
        lines.append("")
        lines.append(f"**{dq_emoji} {dq_verdict}** — Score: **{dq_score}/100** (static {dq_static} + momentum overlay)")
        factors = quality.get("factors", {})
        for fname, fkey in [("VIX", "vix"), ("FII/DII Flows", "fii_dii"), ("Breadth", "breadth"), ("Calendar", "calendar")]:
            f = factors.get(fkey, {})
            lines.append(f"* {fname}: {f.get('score', '?')}/max — {f.get('detail', '')}")
        # Conditions momentum overlay
        mom = quality.get("conditions_momentum", {})
        if mom and mom.get("raw_score") is not None:
            raw = mom["raw_score"]
            mom_emoji = "📈" if raw > 0 else "📉" if raw < 0 else "➡️"
            lines.append(f"* {mom_emoji} **Conditions momentum: {raw:+.0f}** ({mom.get('verdict', '?')})")
            for comp_name, comp in mom.get("components", {}).items():
                label = comp_name.replace("_", " ").title()
                lines.append(f"    - {label}: {comp.get('score', 0):+d} — {comp.get('detail', '')}")
        lines.append("")
        lines.append(f"*{quality.get('recommendation', '')}*")
        lines += ["", "---", ""]

    # Regime Classification — what kind of day is this?
    if regime_class and "error" not in regime_class:
        rc_regime = regime_class.get("regime", "UNKNOWN")
        rc_emoji = regime_class.get("emoji", "⚪")
        rc_score = regime_class.get("score", 0)
        rc_strategy = regime_class.get("strategy", "unknown")
        lines.append("### 🌊 Market Regime — what kind of day is this?")
        lines.append("")
        lines.append(f"**{rc_emoji} {rc_regime}** — Score: **{rc_score}/100** → Strategy: `{rc_strategy}`")
        for reason in regime_class.get("reasons", []):
            lines.append(f"* {reason}")
        # Scalping guidance
        guidance = regime_class.get("scalping_guidance", {})
        if guidance:
            lines.append("")
            lines.append("**Scalping guidance for this regime:**")
            for gk, gv in guidance.items():
                lines.append(f"* **{gk.replace('_', ' ').title()}:** {gv}")
        # Momentum overlay
        mom = regime_class.get("momentum", {})
        if mom.get("regime_momentum") != "STABLE":
            lines.append("")
            lines.append(f"* ⚡ Regime momentum: {mom.get('regime_momentum', '?')} (prev: {mom.get('previous_regime', '?')})")
        lines += ["", "---", ""]

    # Sector rotation summary
    sector_rs = rank.get("sector_rotation", {})
    if sector_rs:
        lines += [
            "### 🔄 Sector Rotation — which sectors are in favor?",
            "",
            "| Sector | Avg RS20 | Avg Momentum | Stocks | % Bullish | Direction |",
            "| :-- | --: | --: | --: | --: | :-- |",
        ]
        sorted_sectors = sorted(sector_rs.items(), key=lambda x: x[1].get("avg_rs_20", 0), reverse=True)
        for sector, sr in sorted_sectors:
            if sector == "OTHERS":
                continue
            d_emoji = "🟢" if sr["direction"] == "BULLISH" else "🔴"
            lines.append(
                f"| {sector} | {sr['avg_rs_20']:+.1f}% | {sr['avg_momentum']:.0f} | "
                f"{sr['n_stocks']} | {sr['pct_bullish']:.0f}% | {d_emoji} {sr['direction']} |"
            )
        lines += ["", "---", ""]

    lines += [
        "### 🎯 Does any horizon work statistically? (measured on today's universe)",
        "",
        "| cell | windows | stocks | hit-first % | win % | CI low | expectancy % | confirmed edges |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, c in sorted(rank.get("summary", {}).items(), key=_cell_sort):
        lines.append(
            f"| {key} | {c['n_windows']} | {c['n_stocks']} | {c['target_first_rate']} | "
            f"{c['win_rate']} | {c['ci_low']} | {c['avg_expectancy_pct']} | {c['confirmed_edges']} |"
        )
    lines += [
        "",
        "*Reading: short horizons (2-3 bar) show 1-3 day hit rates. Hit probability rises with horizon, "
        "but expectancy is the key — a high hit rate with negative expectancy is noise. Confirmed-edge "
        "counts are where the directional selection lives.*",
        "",
        "---",
        "",
        f"### 🏆 Top {params.get('top', 20)} — ranked by P(hit) at {hz}-bar × {hit}%",
        "",
        "| # | Symbol | Close | ATR% | Dir | Mom Score | RSI | Vol Ratio | Sector | R:R | P(hit) | Verdict | Risk |",
        "| :-- | :-- | --: | --: | :-- | --: | --: | --: | :-- | --: | --: | :-- | :-- |",
    ]
    for i, r in enumerate(rank.get("top", []), start=1):
        flags = " ".join(f"⚠{f}" for f in r.get("flags", []))
        mom = r.get("momentum_score", 50.0)
        rsi = r.get("rsi")
        vol = r.get("volume_ratio")
        sector = r.get("sector", "–")
        lines.append(
            f"| {i} | {r['symbol']} | {indian_currency(r['close'])} | {r['atr_pct']} | {r['direction']} "
            f"| {mom:.0f} | {rsi if rsi is not None else '–'} | {vol if vol is not None else '–'} | {sector} "
            f"| {r['rr']} | {r['p_hit']} | "
            f"{VERDICT_EMOJI.get(r['verdict'], '')} {r['verdict']} {flags} | {RISK_EMOJI.get(r['risk'], '')} {r['risk']} |"
        )
    lines += [
        "",
        "*P(hit) = % of similar historical setups that hit the target before the stop within the holding "
        "window (net of costs). Verdict: EDGE CONFIRMED = Wilson CI lower bound clears break-even; "
        "POSITIVE BUT UNCONFIRMED = above break-even but not statistically; NO EDGE = below break-even.*",
        "",
        "---",
        "",
        "### 🔎 Hit matrix for the top candidates (% target-first per horizon × target)",
        "",
        "| Symbol | 2D@1 | 2D@2 | 2D@3 | 2D@4 | 3D@1 | 3D@2 | 3D@3 | 3D@4 | 1W@2 | 1W@3 | 1W@4 |",
        "| :-- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for r in rank.get("top", [])[:10]:
        cells = r.get("cells", {})
        vals = []
        # Short horizons first (2D, 3D) — the 1-3 day scalping targets
        for h in (2, 3, 5):
            for hp in (1.0, 2.0, 3.0, 4.0):
                c = cells.get(f"{h}:{hp:.1f}")
                vals.append(f"{round(c['target_first'] / c['n'] * 100, 1)}" if c else "–")
        lines.append(f"| {r['symbol']} | " + " | ".join(vals) + " |")
    lines += ["", "---", ""]

    # Signal analysis — momentum, VWAP, entry zones for top candidates
    if signals and "error" not in signals:
        sig_list = signals.get("signals", [])
        if sig_list:
            lines += [
                "### 🎯 Signal Analysis — momentum, VWAP, and entry zones",
                "",
                "| Symbol | Score | Signal | RSI | MACD | Vol | VWAP | Entry Zone | R:R |",
                "| :-- | --: | :-- | --: | --: | --: | --: | --: | --: |",
            ]
            for s in sig_list:
                zone = s.get("entry_zone", {})
                lines.append(
                    f"| {s['symbol']} | {s['composite_score']:.0f} | {s['signal_direction']} "
                    f"| {s.get('rsi', '–')} | {s.get('macd_histogram', '–')} "
                    f"| {s.get('volume_ratio', '–')}x | {indian_currency(s.get('vwap', 0))} "
                    f"| {indian_currency(zone.get('ideal_entry', 0))} → {indian_currency(zone.get('target', 0))} "
                    f"| {zone.get('rr_at_ideal', 0)} |"
                )
            lines += ["", "---", ""]

    # Entry readiness — should I enter this stock TODAY?
    if readiness and "error" not in readiness:
        ready_list = readiness.get("readiness", [])
        if ready_list:
            lines += [
                "### ⚡ Entry Readiness — should I enter this stock TODAY?",
                "",
                "| Symbol | Score | Verdict | Open Pos | Close Str | Volume | Accel | Trend | Sector |",
                "| :-- | --: | :-- | --: | --: | --: | --: | :-- | :-- |",
            ]
            for r in ready_list:
                sig = r.get("signals", {})
                ready_emoji = {"READY": "🟢", "CONDITIONAL": "🟡", "NOT_READY": "🔴"}.get(r["verdict"], "⚪")
                lines.append(
                    f"| {r['symbol']} | {r['score']} | {ready_emoji} {r['verdict']} "
                    f"| {sig.get('opening_position', {}).get('score', '?')}/20 "
                    f"| {sig.get('close_strength', {}).get('score', '?')}/20 "
                    f"| {sig.get('volume_confirmation', {}).get('score', '?')}/20 "
                    f"| {sig.get('momentum_acceleration', {}).get('score', '?')}/20 "
                    f"| {sig.get('trend_alignment', {}).get('signal', '?')} "
                    f"| {r.get('sector', '?')} |"
                )
            lines += [
                "",
                "*Entry readiness measures whether a stock is ready to MOVE today, not just whether "
                "indicators are bullish. For 1-3 day trades, timing the entry is as important as "
                "picking the right stock. READY = enter at market open; CONDITIONAL = smaller size, "
                "wait for confirmation; NOT_READY = skip this one today.*",
                "",
                "---",
                "",
            ]

    # Gap Analysis — what did the overnight move mean?
    if gap_analysis and "error" not in gap_analysis:
        gap_stocks = gap_analysis.get("stocks", [])
        if gap_stocks:
            ga_sum = gap_analysis.get("summary", {})
            lines += [
                "### 🕳️ Gap Analysis — overnight opinion",
                "",
                f"*{ga_sum.get('actionable_gaps', 0)} actionable gaps · "
                f"{ga_sum.get('strong_follow_through', 0)} strong follow-through · "
                f"{ga_sum.get('reversal_likely', 0)} reversal likely*",
                "",
                "| Symbol | Gap | Size | Held? | Close Pos | Signal | Trend | Next Day Rate |",
                "| :-- | --: | :-- | :-- | :-- | :-- | :-- | --: |",
            ]
            for g in gap_stocks:
                gap = g.get("gap", {})
                if not gap.get("has_gap"):
                    continue
                sig_emoji = {"STRONG_FOLLOW_THROUGH": "🟢", "FOLLOW_THROUGH_LIKELY": "🟡",
                             "REVERSAL_LIKELY": "🔴"}.get(g.get("signal_type", ""), "⚪")
                held = "✅" if not gap.get("filled") else "❌"
                # Find the historical next-day rate for this gap type
                hist_key = f"gap_{gap['direction'].lower()}_{'held' if not gap.get('filled') else 'filled'}"
                hist_data = g.get("historical_rates", {}).get(hist_key, {})
                next_rate = hist_data.get("next_day_continuation", "–")
                lines.append(
                    f"| {g['symbol']} | {gap['gap_pct']:+.1f}% {gap['direction']} | {gap['size']} | "
                    f"{held} | {gap['close_position']} | {sig_emoji} {g.get('signal_type', '?')} | "
                    f"{'✅' if g.get('trend_aligned') else '⚠️'} | {next_rate}% |"
                )
            lines += ["", "---", ""]

    # Scalp Viability — can I actually get 0.5-1% from this?
    if scalp_viab and "error" not in scalp_viab:
        sv_stocks = scalp_viab.get("stocks", [])
        if sv_stocks:
            sv_sum = scalp_viab.get("summary", {})
            lines += [
                "### 📐 Scalp Viability — can I get 0.5-1% from this?",
                "",
                f"*{sv_sum.get('scalapable', 0)} scalpable · "
                f"{sv_sum.get('marginal', 0)} marginal · "
                f"{sv_sum.get('not_scalpable', 0)} not scalable*",
                "",
                "| Symbol | Score | Verdict | 1D@0.5% | 2D@1.0% | Median 2D | P10 | ATR% |",
                "| :-- | --: | :-- | --: | --: | --: | --: | --: |",
            ]
            for sv in sv_stocks:
                v = sv.get("viability", {})
                dist = sv.get("distribution", {})
                d1 = dist.get("1", {}).get("targets", {}).get("0.5%", {})
                d2 = dist.get("2", {}).get("targets", {}).get("1.0%", {})
                v_emoji = {"SCALPABLE": "🟢", "MARGINAL": "🟡", "NOT_SCALPABLE": "🔴"}.get(v.get("verdict", ""), "⚪")
                lines.append(
                    f"| {sv['symbol']} | {v.get('score', 0):.0f} | {v_emoji} {v.get('verdict', '?')} "
                    f"| {d1.get('net_hit_rate', '–')}% | {d2.get('net_hit_rate', '–')}% "
                    f"| {dist.get('2', {}).get('median', '–'):+.1f}% "
                    f"| {dist.get('2', {}).get('p10', '–'):+.1f}% | {sv.get('atr_pct', '–')} |"
                )
            lines += [
                "",
                "*Scalp viability measures whether a stock's daily behavior produces enough "
                "movement for 0.5-1% scalping after costs. A stock can be BULLISH but "
                "NOT_SCALPABLE if it doesn't move enough. 1D@0.5% = % of days with ≥0.5% move "
                "in the direction (net of costs). P10 = worst 10% of outcomes.*",
                "",
                "---",
                "",
            ]

    # Volume Profile — where did the big money transact?
    if vol_profile and "error" not in vol_profile:
        vp_stocks = vol_profile.get("stocks", [])
        if vp_stocks:
            vp_sum = vol_profile.get("summary", {})
            lines += [
                "### 🏗️ Volume Profile — institutional levels",
                "",
                f"*{vp_sum.get('strong_level', 0)} strong levels · "
                f"{vp_sum.get('favorable_level', 0)} favorable · "
                f"{vp_sum.get('unfavorable', 0)} unfavorable*",
                "",
                "| Symbol | Score | Verdict | POC | Distance | Position | Shape | Trend |",
                "| :-- | --: | :-- | --: | --: | :-- | :-- | :-- |",
            ]
            for vp in vp_stocks:
                profile = vp.get("profile", {})
                sc = vp.get("score", {})
                vp_emoji = {"STRONG_LEVEL": "🟢", "FAVORABLE_LEVEL": "🟡",
                            "NEUTRAL_LEVEL": "⚪", "UNFAVORABLE_LEVEL": "🔴"}.get(
                    sc.get("verdict", ""), "⚪")
                lines.append(
                    f"| {vp['symbol']} | {sc.get('score', 0):.0f} | {vp_emoji} {sc.get('verdict', '?')} "
                    f"| ₹{profile.get('poc', {}).get('price', 0):,.0f} "
                    f"| {profile.get('poc_distance_pct', 0):+.2f}% "
                    f"| {profile.get('position', '?')} "
                    f"| {profile.get('shape', '?')} "
                    f"| {vp.get('trend', '?')} |"
                )
            lines += [
                "",
                "*Volume Profile reveals where institutions transacted. POC = fair value. "
                "Price below POC in an uptrend = buying opportunity. Price above POC = overvalued. "
                "Shape: P = accumulation, b = distribution, D = balanced.*",
                "",
                "---",
                "",
            ]

    # Mean Reversion — who sold too much?
    if mean_rev and "error" not in mean_rev:
        mr_stocks = mean_rev.get("stocks", [])
        if mr_stocks:
            mr_sum = mean_rev.get("summary", {})
            lines += [
                "### 🔄 Mean Reversion — who sold too much?",
                "",
                f"*{mr_sum.get('strong_reversion', 0)} strong · "
                f"{mr_sum.get('reversion_setup', 0)} setup · "
                f"{mr_sum.get('weak_reversion', 0)} weak · "
                f"{mr_sum.get('not_a_dip', 0)} not a dip*",
                "",
                "| Symbol | Score | Verdict | Drawdown | RSI | Support | Trend | Entry | R:R |",
                "| :-- | --: | :-- | --: | --: | :-- | :-- | --: | --: |",
            ]
            for mr in mr_stocks:
                rev = mr.get("reversion", {})
                dd = mr.get("drawdown", {})
                sigs = mr.get("signals", {})
                sup = mr.get("support", {})
                ez = rev.get("entry_zone", {})
                mr_emoji = {"STRONG_REVERSION": "🟢", "REVERSION_SETUP": "🟡",
                            "WEAK_REVERSION": "🟠", "NOT_A_DIP": "⚪"}.get(
                    rev.get("verdict", ""), "⚪")
                lines.append(
                    f"| {mr['symbol']} | {rev.get('score', 0):.0f} | {mr_emoji} {rev.get('verdict', '?')} "
                    f"| {dd.get('drawdown_pct', 0):+.1f}% "
                    f"| {sigs.get('rsi', {}).get('value', '?')} "
                    f"| {sup.get('nearest_support', '?')} "
                    f"| {mr.get('trend', '?')} "
                    f"| ₹{ez.get('ideal_entry', 0):,.0f} "
                    f"| {ez.get('rr_at_ideal', 0)} |"
                )
            lines += [
                "",
                "*Mean reversion scores whether a dip is a BUYING OPPORTUNITY or a TRAP. "
                "The trend filter is crucial: dip in uptrend = gift; dip in downtrend = trap. "
                "Entry zone = where to place limit order (not always current price). "
                "RSI < 40 + near support + uptrend = prime reversion setup.*",
                "",
                "---",
                "",
            ]    # Day Statistics — calendar quantification
    if day_stats and "error" not in day_stats:
        ds = day_stats.get("day_score", {})
        ds_emoji = {"STATISTICAL_ADVANTAGE": "🟢", "STATISTICAL_NEUTRAL": "🟡",
                     "STATISTICAL_DISADVANTAGE": "🟠", "STATISTICAL_AVOID": "🔴"}.get(
            ds.get("verdict", ""), "⚪")
        lines += [
            "### 📅 Day Statistics — which days are better? (calendar quantification)",
            "",
            f"**{ds_emoji} {ds.get('verdict', '?')}** — Score: **{ds.get('score', '?')}/100**",
            "",
        ]
        # Day-of-week table
        dow = day_stats.get("day_of_week", {}).get("by_day", {})
        if dow:
            lines += [
                "**Day-of-week averages:**",
                "",
                "| Day | Avg Return | % Positive | Samples | Significant |",
                "| :-- | --: | --: | --: | :-- |",
            ]
            for day_name in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
                dd = dow.get(day_name, {})
                if dd.get("avg_return_pct") is not None:
                    sig = "*" if dd.get("significant") else ""
                    lines.append(
                        f"| {day_name} | {dd['avg_return_pct']:+.3f}% | {dd['pct_positive']:.0f}% "
                        f"| {dd['n']} | {sig} |"
                    )
            lines += [""]
        # Expiry effect
        exp = day_stats.get("expiry_week", {})
        drag = exp.get("expiry_drag_pct")
        if drag is not None:
            lines.append(f"*Expiry week drag: {drag:+.3f}% (monthly expiry weeks underperform by this amount)*")
            lines += [""]
        # Components
        for comp_name, comp in ds.get("components", {}).items():
            lines.append(f"* {comp_name.replace('_', ' ').title()}: {comp.get('points', 0)}/25 — {comp.get('detail', '')}")
        lines += ["", "---", ""]

    # Conditional Probability — on days LIKE today, does this stock work?
    if cond_prob and "error" not in cond_prob:
        cp_stocks = cond_prob.get("stocks", [])
        if cp_stocks:
            cp_sum = cond_prob.get("summary", {})
            today_st = cond_prob.get("today_state", {})
            lines += [
                "### 🎲 Conditional Probability — on days LIKE today, does this stock work?",
                "",
                f"*Today: NIFTY {'above' if today_st.get('nifty_above_dma200') else 'below' if today_st.get('nifty_above_dma200') is False else 'unknown'} 200-DMA, "
                f"VIX {today_st.get('vix_regime', 'unknown')}, "
                f"breadth {'bullish' if today_st.get('breadth_bullish') else 'bearish' if today_st.get('breadth_bullish') is False else 'unknown'}*",
                "",
                f"*{cp_sum.get('conditional_advantage', 0)} conditional advantage . "
                f"{cp_sum.get('conditional_neutral', 0)} neutral . "
                f"{cp_sum.get('conditional_disadvantage', 0)} disadvantage*",
                "",
                "| Symbol | Score | Verdict | Lift | Cond Hit Rate | Uncond Hit Rate | P10 | Median | N |",
                "| :-- | --: | :-- | --: | --: | --: | --: | --: | --: |",
            ]
            for cp in cp_stocks:
                cp_s = cp.get("conditional_probability", {})
                # Find the primary cell for display
                primary = cp_s.get("primary_cell", "")
                cells = cp.get("cells", {})
                cell = cells.get(primary, {})
                cond = cell.get("conditional", {})
                uncond = cell.get("unconditional", {})
                cp_emoji = {"CONDITIONAL_ADVANTAGE": "🟢", "CONDITIONAL_NEUTRAL": "🟡",
                             "CONDITIONAL_DISADVANTAGE": "🟠", "CONDITIONAL_AVOID": "🔴"}.get(
                    cp_s.get("verdict", ""), "⚪")
                lines.append(
                    f"| {cp['symbol']} | {cp_s.get('score', 0):.0f} | {cp_emoji} {cp_s.get('verdict', '?')} "
                    f"| {cell.get('lift', 1.0):.2f}x | "
                    f"{cond.get('hit_rate', '?')}% | {uncond.get('hit_rate', '?')}% "
                    f"| {cond.get('p10', '?')}% | {cond.get('median', '?')}% "
                    f"| {cond.get('n', '?')} |"
                )
            lines += [
                "",
                "*Lift > 1.0 = today's conditions FAVOR this stock (conditional hit rate exceeds unconditional). "
                "Lift < 1.0 = today's conditions DISFAVOR it. The conditional probability is the MOST IMPORTANT "
                "filter: it answers 'given today's VIX, regime, and breadth, does this stock's edge survive?'*",
                "",
                "---",
                "",
            ]

    # Delivery Volume — institutional activity proxy
    if delivery and "error" not in delivery:
        dv_stocks = delivery.get("stocks", [])
        if dv_stocks:
            dv_sum = delivery.get("summary", {})
            dv_src = delivery.get("data_sources", {})
            lines += [
                "### 🏛️ Delivery Volume — institutional activity proxy",
                "",
                f"*{dv_src.get('nse_actual', 0)} stocks with NSE delivery data, "
                f"{dv_src.get('yahoo_proxy', 0)} with Yahoo Finance proxies*",
                "",
                f"*{dv_sum.get('institutional_accumulation', 0)} institutional accumulation . "
                f"{dv_sum.get('mixed_signals', 0)} mixed . "
                f"{dv_sum.get('retail_churn_or_distribution', 0)} retail/distribution*",
                "",
                "| Symbol | Score | Verdict | Source | Detail |",
                "| :-- | --: | :-- | :-- | :-- |",
            ]
            for dv in dv_stocks[:10]:
                dv_emoji = {"INSTITUTIONAL_ACCUMULATION": "🟢", "MIXED_SIGNALS": "🟡",
                             "RETAIL_CHURN_OR_DISTRIBUTION": "🔴"}.get(
                    dv.get("verdict", ""), "⚪")
                # Get the top component detail
                top_detail = ""
                comps = dv.get("components", {})
                for ck in ["delivery_level", "obv", "delivery_proxy", "delivery_trend", "ad_line", "volume_trend"]:
                    if ck in comps:
                        top_detail = comps[ck].get("detail", "")
                        break
                lines.append(
                    f"| {dv['symbol']} | {dv.get('score', 0)} | {dv_emoji} {dv.get('verdict', '?')} "
                    f"| {dv.get('source', '?').split('(')[0].strip()} "
                    f"| {top_detail[:60]} |"
                )
            lines += [
                "",
                "*Delivery volume distinguishes institutional accumulation (high delivery ratio) from "
                "retail churn (low delivery ratio). High delivery + rising volume = genuine buying. "
                "Low delivery + high volume = speculation that will reverse.*",
                "",
                "---",
                "",
            ]

    # Range of returns — the distribution over all horizons for confirmed-edge BUY names.
    pool = rank.get("return_pool", {})
    lines += ["### 📈 Range of returns — EDGE CONFIRMED BUY names", "",]
    if pool.get("qualifying_stocks"):
        # Show 2D, 3D, 1W columns (the key horizons for 1-3 day trading)
        lines += [
            "| Symbol | Close | 2D median (P10/P90) | 3D median (P10/P90) | 1W median (P10/P90) |",
            "| :-- | --: | --: | --: | --: |",
        ]
        for s in pool.get("stocks", []):
            r2, r3, r5 = s["returns"].get("2"), s["returns"].get("3"), s["returns"].get("5")
            def _fmt_short(r):
                if not r or not r.get("n"):
                    return "–"
                return f"{r['median']:+.1f}% (P10 {r['p10']:+.1f}% / P90 {r['p90']:+.1f}%)"
            lines.append(
                f"| {s['symbol']} | {indian_currency(s['close'])} | {_fmt_short(r2)} | {_fmt_short(r3)} | {_fmt_short(r5)} |"
            )
        lines.append("")
        pool_bits = []
        for hkey, label in (("2", "2D"), ("3", "3D"), ("5", "1W"), ("10", "2W")):
            ph = pool.get("horizons", {}).get(hkey)
            if ph:
                pool_bits.append(f"{label}: mean {ph['mean']:+.2f}% · {ph['pct_positive']}% positive ({ph['n_windows']} windows)")
        if pool_bits:
            lines.append(f"*Pooled across {pool.get('qualifying_stocks')} confirmed-edge BUY names — " + "; ".join(pool_bits) + ".*")
        lines.append("")
        lines.append(
            "*Reading: 2D/3D medians are the primary scalping targets. "
            "P10 is the downside you should size for. "
            "A positive P10 means even the bad trades are small losses.*"
        )
    else:
        lines.append("_No EDGE CONFIRMED BUY names this screen — nothing qualifies as a 'solid possibility of a rise'._")
    lines += ["", "---", ""]

    # Regime-conditioned hit rates at the ranking cell.
    lines += [
        f"### 🔄 Regime-conditioned P(hit) — ranking cell {hz}-bar × {hit}%, NIFTY vs 200-DMA",
        "",
        "| Symbol | P(hit) | bull n | bull hit% | bear n | bear hit% |",
        "| :-- | --: | --: | --: | --: | --: |",
    ]
    any_regime = False
    for r in rank.get("top", [])[:10]:
        cell = r.get("cells", {}).get(f"{hz}:{hit:.1f}")
        reg = (cell or {}).get("regime") or {}
        if not reg:
            continue
        any_regime = True
        bull, bear = reg.get("bull"), reg.get("bear")
        bull_txt = f"{bull['n']} | {round(bull['target_first'] / bull['n'] * 100, 1)}" if bull else "– | –"
        bear_txt = f"{bear['n']} | {round(bear['target_first'] / bear['n'] * 100, 1)}" if bear else "– | –"
        lines.append(f"| {r['symbol']} | {r['p_hit']} | {bull_txt} | {bear_txt} |")
    lines.append("")
    if not any_regime:
        lines.append(
            "_No regime split available (NIFTY cache missing — run scripts/screen_regime.py first). "
            "The per-cell regime data lives in screen_stats_out.json once it is._"
        )
    else:
        lines.append(
            "*Reading: does the edge survive in both tapes? A row whose bull hit% is strong but whose "
            "bear hit% collapses is a regime-dependent name — in RISK-OFF the prompt must not trust it.*"
        )
    lines += ["", "---", ""]

    lines += [
        "### ⚠️ How to read this list (the honest caveats)",
        "",
        "* **It is a filter, not an alpha claim.** Per-stock backtested hit rates have ~zero "
        "out-of-sample persistence (measured corr ≈ 0 on a 60-name, 2-year test): the ranking "
        "selects *candidates with viable R:R and volatility*, it does not predict which will win.",
        "* **The sentiment layer is the experiment.** prompt_daily.md picks 1–3 of these on "
        "dated news + events; screen_record.py resolves ALL of the top list — the non-picked "
        "rows are the control group. If sentiment picks do not beat the list, the audit says so "
        "and the overlay should be dropped.",
        "* **Overlapping windows:** consecutive signal days are correlated, not independent "
        "trades. **Shape, not price level:** the backtest says nothing about today's price.",
        "* **SELL rows assume shortability** — check the F&O list before acting on one.",
        "",
        "---",
        "",
        "### ➡️ Next step",
        "",
        f"1. Run the daily prompt: paste `prompt_daily.md` together with `screen_report.md` "
        f"(top {params.get('top', 20)}) into the agent — it scores sentiment and writes `screen_picks.json`.",
        "2. Record the day + resolve old picks: `python scripts/screen_record.py --record`",
        "3. Read the learning report it prints: calibration per horizon, verdict accuracy, and "
        "the sentiment-vs-stats audit.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the daily screen and render screen_report.md.")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--horizon", type=int, default=3,
                        help="Ranking horizon in bars: 2/3/5/10/20 (default: 3 for 1-3 day scalping)")
    parser.add_argument("--hit", type=float, default=4.0)
    parser.add_argument("--stop-atr", type=float, default=1.5)
    parser.add_argument("--period", default="2y")
    parser.add_argument("--sort", choices=["hit", "expectancy", "edge", "momentum"], default="hit")
    parser.add_argument("--long-only", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--no-quality", action="store_true",
                        help="Skip day quality check (faster, but less informative)")
    parser.add_argument("--no-signals", action="store_true",
                        help="Skip signal analysis (faster, but no VWAP/pivot/entry zones)")
    parser.add_argument("--record", action="store_true",
                        help="Also record today's list (picks from screen_picks.json are honoured if present)")
    parser.add_argument("--limit", type=int, default=0, help="Only scan the first N universe symbols (smoke tests)")
    args = parser.parse_args()

    print("▶ Daily screen")
    universe = run_step("scripts/screen_universe.py", UNIVERSE_OUT, "universe")
    if "error" in universe:
        print(f"❌ {universe['error']}", file=sys.stderr)
        return 1

    # Market regime first: fetches NIFTY/VIX and writes the NIFTY cache that
    # the stats pass conditions its per-stock backtest on.
    regime = run_step("scripts/screen_regime.py", REGIME_OUT, "market regime")

    stats_args = ["--universe", str(UNIVERSE_OUT), "--stop-atr", str(args.stop_atr),
                  "--period", args.period]
    if args.limit:
        stats_args += ["--limit", str(args.limit)]
    if args.no_cache:
        stats_args.append("--no-cache")
    stats = run_step("scripts/screen_stats.py", STATS_OUT, "per-stock backtest", *stats_args)
    if "error" in stats:
        print(f"❌ {stats['error']}", file=sys.stderr)
        return 1

    # Day Quality — should I trade today?
    quality = None
    if not args.no_quality:
        quality = run_step("scripts/screen_day_quality.py", QUALITY_OUT, "day quality",
                           "--stats", str(STATS_OUT))
        if "error" not in quality:
            print(f"  · day quality: {quality.get('score', '?')}/100 → {quality.get('verdict', '?')}")

    # Regime classification — what kind of day is this?
    regime_class = run_step("scripts/screen_regime_classify.py", REGIME_CLASS_OUT,
                            "regime classification", "--stats", str(STATS_OUT))
    if "error" not in regime_class:
        print(f"  · regime: {regime_class.get('regime', '?')} ({regime_class.get('emoji', '?')}) "
              f"→ strategy: {regime_class.get('strategy', '?')}")

    rank_args = ["--stats", str(STATS_OUT), "--horizon", str(args.horizon),
                 "--hit", str(args.hit), "--top", str(args.top), "--sort", args.sort]
    if args.long_only:
        rank_args.append("--long-only")
    rank = run_step("scripts/screen_rank.py", RANK_OUT, "ranking", *rank_args)
    if "error" in rank:
        print(f"❌ {rank['error']}", file=sys.stderr)
        return 1

    # Signal analysis — momentum, VWAP, pivots for top candidates
    signals = None
    if not args.no_signals:
        signals = run_step("scripts/screen_signals.py", SIGNALS_OUT, "signal analysis",
                           "--rank", str(RANK_OUT), "--top", str(min(args.top, 10)))

    # Entry readiness — should I enter this stock TODAY?
    readiness = None
    if not args.no_signals:  # reuse the --no-signals flag (same dependency)
        readiness = run_step("scripts/screen_entry_readiness.py", READINESS_OUT, "entry readiness",
                             "--rank", str(RANK_OUT), "--top", str(min(args.top, 10)))

    # Scalp viability — can I actually get 0.5-1% from this stock?
    scalp_viab = run_step("scripts/screen_scalp_viability.py", SCALP_VIAB_OUT,
                          "scalp viability", "--stats", str(STATS_OUT),
                          "--top", str(min(args.top, 10)))
    if "error" not in scalp_viab:
        sv_sum = scalp_viab.get("summary", {})
        print(f"  · scalp viability: {sv_sum.get('scalapable', '?')} scalpable, "
              f"{sv_sum.get('marginal', '?')} marginal")

    # Gap analysis — what did the overnight move mean?
    gap_analysis = run_step("scripts/screen_gap_analysis.py", GAP_ANALYSIS_OUT,
                            "gap analysis", "--stats", str(STATS_OUT),
                            "--top", str(min(args.top, 10)))
    if "error" not in gap_analysis:
        ga_sum = gap_analysis.get("summary", {})
        print(f"  · gaps: {ga_sum.get('actionable_gaps', '?')} actionable, "
              f"{ga_sum.get('strong_follow_through', '?')} strong follow-through")

    # Volume Profile — where did the big money transact?
    vol_profile = run_step("scripts/screen_volume_profile.py", VOL_PROFILE_OUT,
                           "volume profile", "--stats", str(STATS_OUT),
                           "--top", str(min(args.top, 10)))
    if "error" not in vol_profile:
        vp_sum = vol_profile.get("summary", {})
        print(f"  · volume profile: {vp_sum.get('strong_level', '?')} strong levels, "
              f"{vp_sum.get('favorable_level', '?')} favorable")

    # Mean Reversion — who sold too much?
    mean_rev = run_step("scripts/screen_mean_reversion.py", MEAN_REV_OUT,
                        "mean reversion", "--stats", str(STATS_OUT),
                        "--top", str(min(args.top, 10)))
    if "error" not in mean_rev:
        mr_sum = mean_rev.get("summary", {})
        print(f"  · mean reversion: {mr_sum.get('strong_reversion', '?')} strong, "
              f"{mr_sum.get('reversion_setup', '?')} setup, "
              f"{mr_sum.get('stocks_in_dip', '?')} in dip")

    # Day Statistics -- calendar quantification (which days are better?)
    day_stats = run_step("scripts/screen_day_statistics.py", DAY_STATS_OUT, "day statistics")
    if "error" not in day_stats:
        ds = day_stats.get("day_score", {})
        print(f"  day statistics: {ds.get('score', '?')}/100 -> {ds.get('verdict', '?')}")

    # Conditional Probability -- on days LIKE today, does this stock work?
    cond_prob = run_step("scripts/screen_conditional_probability.py", COND_PROB_OUT,
                         "conditional probability", "--stats", str(STATS_OUT),
                         "--top", str(min(args.top, 10)))
    if "error" not in cond_prob:
        cp_sum = cond_prob.get("summary", {})
        print(f"  conditional probability: {cp_sum.get('conditional_advantage', '?')} advantage, "
              f"{cp_sum.get('conditional_neutral', '?')} neutral, "
              f"{cp_sum.get('conditional_disadvantage', '?')} disadvantage")

    # Delivery Volume -- institutional activity proxy
    delivery = run_step("scripts/screen_delivery_volume.py", DELIVERY_OUT,
                        "delivery volume", "--stats", str(STATS_OUT),
                        "--top", str(min(args.top, 10)))
    if "error" not in delivery:
        dv_sum = delivery.get("summary", {})
        print(f"  delivery volume: {dv_sum.get('institutional_accumulation', '?')} institutional, "
              f"{dv_sum.get('mixed_signals', '?')} mixed, "
              f"{dv_sum.get('retail_churn_or_distribution', '?')} retail/distribution")

    # Refresh the regime payload now that today's breadth is computed.
    regime = run_step("scripts/screen_regime.py", REGIME_OUT, "market regime (breadth)")
    ov = regime.get("overall", {})
    print(f"  · regime: {ov.get('label', 'UNKNOWN')} · "
          f"NIFTY {regime.get('nifty', {}).get('trend', '?')} · "
          f"VIX {regime.get('vix', {}).get('regime', '?')} · "
          f"breadth {regime.get('breadth', {}).get('pct', '?')}%")

    print("\nRendering report")
    report = render_report(universe, stats, rank, regime, quality, signals, readiness,
                           regime_class, scalp_viab, gap_analysis, vol_profile, mean_rev,
                           day_stats, cond_prob, delivery)
    REPORT_FILE.write_text(report, encoding="utf-8")
    print(report)

    if args.record:
        print("\nRecording today's list")
        rec = run_step("scripts/screen_record.py", PROJECT_ROOT / "screen_record_out.json",
                       "record", "--record")
        if "track_record" in rec:
            tr = rec["track_record"]
            print(f"  ✓ {tr['total_recorded']} rows recorded · {tr['open']} open · {tr['resolved']} resolved")
        for w in rec.get("recording", {}).get("warnings", []):
            print(f"  ⚠ {w}")

    print(f"\n📄 Report saved to {REPORT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
