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
                  regime: dict[str, Any]) -> str:
    params = rank.get("params", {})
    hz, hit = params.get("horizon", 10), params.get("hit", 4.0)
    lines = [
        "# 📡 DAILY SCREEN REPORT — 2–4% Hit Candidates",
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
        "*Reading: hit probability rises with horizon, but expectancy is negative for every cell — "
        "the blind 2–4% move is not an edge. Confirmed-edge counts are where the cross-sectional "
        "selection lives; the daily prompt picks only from there.*",
        "",
        "---",
        "",
        f"### 🏆 Top {params.get('top', 20)} — ranked by P(hit) at {hz}-bar × {hit}%",
        "",
        "| # | Symbol | Close | ATR% | Dir | R:R | Stop% | P(hit) | Win% (CI) | Exp% | Verdict | Risk |",
        "| :-- | :-- | --: | --: | :-- | --: | --: | --: | --: | --: | :-- | :-- |",
    ]
    for i, r in enumerate(rank.get("top", []), start=1):
        flags = " ".join(f"⚠{f}" for f in r.get("flags", []))
        lines.append(
            f"| {i} | {r['symbol']} | {indian_currency(r['close'])} | {r['atr_pct']} | {r['direction']} "
            f"| {r['rr']} | {r['stop_pct']} | {r['p_hit']} | {r['win_rate']} "
            f"({r['ci_low']}–{r['ci_high']}) | {r['expectancy_pct']} | "
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
        "| Symbol | 1W@2 | 1W@3 | 1W@4 | 2W@2 | 2W@3 | 2W@4 | 4W@2 | 4W@3 | 4W@4 |",
        "| :-- | --: | --: | --: | --: | --: | --: | --: | --: | --: |",
    ]
    for r in rank.get("top", [])[:10]:
        cells = r.get("cells", {})
        vals = []
        for h in (5, 10, 20):
            for hp in (2.0, 3.0, 4.0):
                c = cells.get(f"{h}:{hp:.1f}")
                vals.append(f"{round(c['target_first'] / c['n'] * 100, 1)}" if c else "–")
        lines.append(f"| {r['symbol']} | " + " | ".join(vals) + " |")
    lines += ["", "---", ""]

    # Range of returns — the distribution over 1/2/4 weeks for the names the
    # prompt may actually buy (EDGE CONFIRMED + BUY).
    pool = rank.get("return_pool", {})
    lines += [
        "### 📈 Range of returns (1W / 2W / 4W) — EDGE CONFIRMED BUY names",
        "",
    ]
    if pool.get("qualifying_stocks"):
        lines += [
            "| Symbol | Close | 1W median (IQR) · P10/P90 | 2W median (IQR) · P10/P90 | 4W median (IQR) · P10/P90 |",
            "| :-- | --: | --: | --: | --: |",
        ]
        for s in pool.get("stocks", []):
            r5, r10, r20 = s["returns"].get("5"), s["returns"].get("10"), s["returns"].get("20")
            lines.append(
                f"| {s['symbol']} | {indian_currency(s['close'])} | {_fmt_range(r5)} | {_fmt_range(r10)} | {_fmt_range(r20)} |"
            )
        lines.append("")
        pool_bits = []
        for hkey, label in (("5", "1W"), ("10", "2W"), ("20", "4W")):
            ph = pool.get("horizons", {}).get(hkey)
            if ph:
                pool_bits.append(f"{label}: mean {ph['mean']:+.2f}% · {ph['pct_positive']}% positive ({ph['n_windows']} windows)")
        if pool_bits:
            lines.append(f"*Pooled across {pool.get('qualifying_stocks')} confirmed-edge BUY names — " + "; ".join(pool_bits) + ".*")
        lines.append("")
        lines.append(
            "*Reading: these are the raw buy-and-hold moves of every similar historical window "
            "(net of costs) — the *range* the stock actually travels over each horizon, separate "
            "from the managed stop/target geometry. P10 is the drawdown you should size for.*"
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
    parser.add_argument("--horizon", type=int, default=10)
    parser.add_argument("--hit", type=float, default=4.0)
    parser.add_argument("--stop-atr", type=float, default=1.5)
    parser.add_argument("--period", default="2y")
    parser.add_argument("--sort", choices=["hit", "expectancy", "edge"], default="hit")
    parser.add_argument("--long-only", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
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

    rank_args = ["--stats", str(STATS_OUT), "--horizon", str(args.horizon),
                 "--hit", str(args.hit), "--top", str(args.top), "--sort", args.sort]
    if args.long_only:
        rank_args.append("--long-only")
    rank = run_step("scripts/screen_rank.py", RANK_OUT, "ranking", *rank_args)
    if "error" in rank:
        print(f"❌ {rank['error']}", file=sys.stderr)
        return 1

    # Refresh the regime payload now that today's breadth is computed.
    regime = run_step("scripts/screen_regime.py", REGIME_OUT, "market regime (breadth)")
    ov = regime.get("overall", {})
    print(f"  · regime: {ov.get('label', 'UNKNOWN')} · "
          f"NIFTY {regime.get('nifty', {}).get('trend', '?')} · "
          f"VIX {regime.get('vix', {}).get('regime', '?')} · "
          f"breadth {regime.get('breadth', {}).get('pct', '?')}%")

    print("\nRendering report")
    report = render_report(universe, stats, rank, regime)
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
