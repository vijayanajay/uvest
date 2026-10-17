#!/usr/bin/env python3
"""UVEST stage 4 — LightGBM 1-Month Direction & Volatility Cone (TICK-004).

Sole owner of the 22-session forward direction and range engine (BRD §4, §5.2,
§9.1 row 4). Three inputs, two of them shared with stages that already exist:

* `--input current_trade.json` — the canonical payload (symbol, live_price,
  stoploss, target, action).
* `--ohlcv-fixture PATH` — the **same** recorded daily-OHLCV file stage 2
  reads: the stock's own history supplies the technical, volatility,
  mean-reversion and gap features.
* `--context-fixture PATH` — the **same** `uvest-market-context/1` bundle
  stage 7 classifies (ADR-010 decision 4). It supplies exactly two features,
  the 20-session beta to NIFTY 50 and the 20-session correlation to the
  trade's NSE sectoral index, resolved through the shared `_sector_map`.

There is deliberately **no second fetcher and no second cache**: the runner
hands both stages the one bundle it fetched (or the one fixture it was given),
so the no-lookahead truncation at T_rec travels with a single artifact rather
than three, and `fetch_market_context.py` stays the only live source.

Emits the frozen BRD §9 inventory row 4 payload:

    prob_target_hit_22d, price_cone_p10_p50_p90, mae_projected,
    mfe_projected, shap_drivers, direction_label, waived

**Model provenance — sourced, disclosed.** `models/lgbm_direction_v1.txt` (a
LightGBM booster) and its companion `models/lgbm_direction_v1.calibration.json`
are trained offline by `tests/evals/train_direction_model.py` from the committed
*sourced* corpus in `tests/fixtures/history/` — real yfinance daily bars per
symbol-month, plus `indices_daily.json`, the index series the free tier-1 source
actually serves (`tests/evals/build_index_corpus.py` builds it and discloses the
tickers it does not). Rows are point-in-time and the trainer imports this
module's own `feature_vector`, so a feature can never mean one thing in training
and another at inference; validation is BRD §5.2's purged group time-series CV,
and the out-of-fold metrics are written into the artifact's own
`provenance.cv` block rather than asserted here. The trained universe is the
subset of the NSE F&O universe whose sectoral index the free tier-1 source
serves historically — disclosed per symbol in `corpus.symbols_excluded`, never
hidden. The authored stand-in this replaced was retired 2026-10-14.

**The cone's pools are the trade's own sector's, when they are deep enough.**
`CONE_SPEC`'s levels are quantiles of *observed* excursions, so which
observations enter the mixture decides the envelope — and one pooled block mixes
a bank's 22-session dispersion with an IT stock's and an energy major's while
the coverage gate grades one specific trade. The calibration therefore ships one
block per sectoral index beside the pooled block the trainer has always written,
and `samples_for` answers with the trade's sector's block when every class of
every measure holds at least `MIN_SECTOR_ATOMS_PER_CLASS` atoms, the pooled block
otherwise. A thin or missing block is a fallback (disclosed on stderr, never in
the frozen payload); a calibration without sector blocks behaves exactly as
before.

`direction_probability` and `prob_target_hit_22d` both state the
**target-versus-stop race** — decision D (2026-10-16) moved evals dimension 4
onto that event — at two different geometries, and reporting either as the
other would grade a question the engine never answered:

* `direction_probability` is the booster's own class probability that the
  **reference-band** race resolves target-first: ±3% around the entry
  (`RACE_BAND_PCT`), the band the corpus rows' labels were decided at and
  frozen in the calibration as `race_band_pct`.
* `prob_target_hit_22d` is **this trade's** race — its own target and stop —
  read through the class-weighted excursion pools as the product
  `P(best high ≥ target) × P(worst low > −stop)`. That product is at most the
  true first-touch probability (the two events are positively associated), so
  it ships as a **disclosed lower bound**, never a tuned-up one; the
  calibration carries the disclosure in `race_estimator`. A stop at or above
  the market (`stop_pct ≤ 0`) wins no race and scores 0.0.

**Waiver policy (BRD §10.4).** Any absent, short, malformed or unmapped input
— model artifact, OHLCV, bundle, series, common date window, or an unsupported
`action` — waives the whole payload: every metric `null`, `waived: true`,
reasons on stderr, exit 0. A waived stage caps the eventual verdict at
🟡 `GO_WEAK`. The stage never fabricates a probability to avoid a waiver.

`shap_drivers` are the booster's own exact tree SHAP contributions
(`pred_contrib=True`) for the winning class — no `shap` dependency, no
approximation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _recorder import add_record_arg, record_artifact, resolve_bundle_dir  # noqa: E402
from _sector_map import SECTOR_INDEX_MAP  # noqa: E402  (scripts dir spliced above)

ERR_PARSE = "ERR_PARSE"

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "uvest-market-context/1"
CALIBRATION_SCHEMA = "uvest-ml-calibration/1"

NSEI = "^NSEI"

REQUIRED_FIELDS = ["symbol", "live_price", "stoploss", "target"]

DEFAULT_MODEL = REPO_ROOT / "models" / "lgbm_direction_v1.txt"
DEFAULT_CALIBRATION = REPO_ROOT / "models" / "lgbm_direction_v1.calibration.json"

HORIZON = 22                 # BRD §4: the 1-month (22-session) forward window
MIN_SESSIONS = 200           # 200-session SMA is a feature; below it, waive
CROSS_ASSET_WINDOW = 20      # BRD §5.2: 20D beta / 20D sectoral correlation
# A market proxy with essentially no daily variance has no estimable beta:
# the OLS slope divides by it and explodes (an authored, near-flat index
# returns a beta of ~10 where a real NIFTY window sits near 1). Below this
# floor the feature is 0.0 — "no information" — never a spuriously large
# number read as conviction. 1e-6 is a daily σ of 0.1%; real NIFTY windows
# run 0.6-1.0%.
MIN_MARKET_VARIANCE = 1e-6

RACE_BAND_PCT = 3.0          # reference band (decision D): +3% target, −3% stop
STRONG_TARGET_PROB = 0.60    # P(TARGET_FIRST) ≥ this reads STRONG_TARGET_FIRST
SHAP_TOP_K = 5

# Frozen feature order. The trainer and this stage share `feature_vector`, so
# this tuple is the single definition of "what the model sees" — reordering it
# without retraining would silently scramble the booster's inputs.
FEATURES = (
    "ret_1", "ret_5", "ret_22", "rsi_14", "ema20_slope_5",
    "sma50_dist", "sma200_dist", "vol_14", "vol_ratio_14_60",
    "bollinger_bw_20", "zscore_20", "beta_20_nifty", "corr_20_sector",
)
CLASSES = ("TARGET_FIRST", "STOP_FIRST", "NEITHER")
# Decision D (2026-10-16): the booster's classes are the reference-band race's
# own outcomes — which ±3% level the forward window printed first — so the
# mixture weights the excursion pools by *race* shape instead of by terminal
# direction. Every doc that described a 1-month direction label moves with it.
# The 22-session price cone, frozen as the envelope of the **path** (BRD §5.2,
# ADR-011 decision 7): the lower quartiles are inverted from the mixture of
# observed *adverse* excursions, the upper ones from the mixture of *favourable*
# excursions, and only the median comes from the terminal returns. Every
# observed row satisfies mae ≤ terminal ≤ mfe and the three pools share one
# weight vector, so this envelope contains the terminal interval and is monotone
# in level by construction. Inverting all five levels from the terminal returns
# (the earlier construction) answered "how far will the close travel" while the
# coverage gate asks how far the *price* travelled: measured over the corpus it
# held 72.3% of rows' closes but only 49.5% of their paths.
CONE_SPEC = (("p10", "mae", 0.10), ("p25", "mae", 0.25),
             ("p50", "return", 0.50), ("p75", "mfe", 0.75),
             ("p90", "mfe", 0.90))
# Which calibration measure each cone token reads. Named once, here, because
# both the stage and the trainer's cross-validation invert a cone from a
# calibration-shaped `samples` block; a second mapping would let the measured
# envelope drift from the shipped one.
CONE_MEASURES = {"return": "forward_return_pct", "mae": "mae_pct", "mfe": "mfe_pct"}
# How deep a sector block must be before the cone reads it instead of the
# pooled one. A `P10`/`P90` pair is read off a mixture of observed excursions, so
# each class needs enough atoms to stand for a tail; below this the sector's own
# block cannot carry a quantile and the pooled block answers instead. Frozen and
# disclosed, not tuned on the folds.
MIN_SECTOR_ATOMS_PER_CLASS = 30
PAYLOAD_KEYS = [
    "prob_target_hit_22d", "price_cone_p10_p50_p90", "mae_projected",
    "mfe_projected", "shap_drivers", "direction_label",
    "direction_probability", "waived",
]


def die(code: str, exit_code: int, hint: str = "") -> None:
    print(f"{code}: {hint}" if hint else code, file=sys.stderr)
    sys.exit(exit_code)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_payload(path: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(ERR_PARSE, 1, f"input file not found: {path}")
    except json.JSONDecodeError as exc:
        die(ERR_PARSE, 1, f"input is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        die(ERR_PARSE, 1, "input must be a JSON object")
    missing = [k for k in REQUIRED_FIELDS if payload.get(k) is None]
    if missing:
        die(ERR_PARSE, 1, f"missing required field(s): {', '.join(missing)}")
    return payload


def load_ohlcv(path: str | None) -> list[dict] | None:
    """Recorded daily OHLCV, or None when the evidence cannot be used.

    The stage-2/3/5/6/7 convention: an omitted path, an unreadable or
    malformed file, or too few sessions all mean *this evidence is absent* —
    a data gap to disclose, never a traceback.
    """
    if not path:
        return None
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    sessions = raw.get("sessions") or []
    if len(sessions) < MIN_SESSIONS:
        return None
    try:
        for row in sessions:
            float(row["close"])
            str(row["date"])
    except (KeyError, TypeError, ValueError):
        return None
    return sessions


def load_context(path: str | None) -> tuple[dict | None, str | None]:
    """Read the shared evidence bundle → (bundle, reason). Exactly one is set."""
    if not path:
        return None, "fixture:not-provided"
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "fixture:unreadable"
    if not isinstance(raw, dict):
        return None, "fixture:unreadable"
    if raw.get("schema") != SCHEMA:
        return None, "fixture:schema"
    if not isinstance(raw.get("series"), dict):
        return None, "fixture:unreadable"
    return raw, None


def load_model(model_path: str | Path, calibration_path: str | Path):
    """(booster, calibration) — or (None, None) with a reason when unusable.

    Either artifact missing means the stage waives: a booster with no
    calibration has no cone, and a calibration with no booster has no
    probabilities; half a model is not evidence.
    """
    try:
        calibration = json.loads(
            Path(calibration_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None, "model:calibration-absent"
    if not isinstance(calibration, dict):
        return None, None, "model:calibration-absent"
    if calibration.get("schema") != CALIBRATION_SCHEMA:
        return None, None, "model:calibration-schema"
    if list(calibration.get("features") or []) != list(FEATURES) \
            or list(calibration.get("classes") or []) != list(CLASSES):
        return None, None, "model:calibration-features"
    if int(calibration.get("horizon") or 0) != HORIZON:
        return None, None, "model:calibration-horizon"
    # The reference band is the label rule the corpus was labeled at; a band
    # the stage would not label at makes every shipped class a lie about its
    # own training data, so it is required — absent, malformed or foreign.
    band = calibration.get("race_band_pct")
    if isinstance(band, bool) or not isinstance(band, (int, float)) \
            or float(band) != RACE_BAND_PCT:
        return None, None, "model:calibration-fields"
    if not Path(model_path).exists():
        return None, None, "model:booster-absent"
    try:
        import lightgbm  # imported here so the module stays import-cheap
        booster = lightgbm.Booster(model_file=str(model_path))
    except Exception:  # noqa: BLE001 — any loader failure is absent evidence
        return None, None, "model:booster-unreadable"
    return booster, calibration, None


# --------------------------------------------------------------------------
# Features (shared verbatim with tests/evals/train_direction_model.py)
# --------------------------------------------------------------------------

def _sma(values: list[float], period: int) -> float:
    return sum(values[-period:]) / period


def _ema_last(values: list[float], period: int) -> float:
    """α = 2/(N+1), seeded with the SMA of the first N values (stage-2 rule)."""
    alpha = 2.0 / (period + 1)
    out = sum(values[:period]) / period
    for value in values[period:]:
        out = alpha * value + (1 - alpha) * out
    return out


def _stdev(values: list[float]) -> float:
    """Population σ (÷ N), the convention stage 2's Bollinger bands freeze."""
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def _returns(closes: list[float], count: int) -> list[float]:
    window = closes[-(count + 1):]
    return [window[i] / window[i - 1] - 1.0 for i in range(1, len(window))]


def _rsi(closes: list[float], period: int = 14) -> float:
    gains = losses = 0.0
    for i in range(len(closes) - period, len(closes)):
        change = closes[i] - closes[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    if losses == 0.0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)


def _beta(stock: list[float], market: list[float]) -> float:
    """OLS slope of the stock's returns on the market's, 0.0 below the floor."""
    n = len(stock)
    m_stock, m_market = sum(stock) / n, sum(market) / n
    cov = sum((s - m_stock) * (m - m_market) for s, m in zip(stock, market)) / n
    var = sum((m - m_market) ** 2 for m in market) / n
    if var < MIN_MARKET_VARIANCE:
        return 0.0
    return cov / var


def _corr(a: list[float], b: list[float]) -> float:
    n = len(a)
    m_a, m_b = sum(a) / n, sum(b) / n
    cov = sum((x - m_a) * (y - m_b) for x, y in zip(a, b)) / n
    var_a = sum((x - m_a) ** 2 for x in a) / n
    var_b = sum((y - m_b) ** 2 for y in b) / n
    if not var_a or not var_b:
        return 0.0
    return cov / (var_a ** 0.5 * var_b ** 0.5)


def common_return_windows(stock: dict[str, float], market: dict[str, float],
                          sector_by_date: dict[str, float]):
    """(stock, market, sector) return windows over the last N common sessions.

    Returns None when the three series share fewer than `window` sessions —
    an alignment gap, and a gap waives rather than guesses.
    """
    dates = sorted(set(stock) & set(market) & set(sector_by_date))
    if len(dates) < CROSS_ASSET_WINDOW + 1:
        return None
    dates = dates[-(CROSS_ASSET_WINDOW + 1):]
    stock_closes = [stock[d] for d in dates]
    market_closes = [market[d] for d in dates]
    sector_closes = [sector_by_date[d] for d in dates]
    return (
        [stock_closes[i] / stock_closes[i - 1] - 1.0 for i in range(1, len(dates))],
        [market_closes[i] / market_closes[i - 1] - 1.0 for i in range(1, len(dates))],
        [sector_closes[i] / sector_closes[i - 1] - 1.0 for i in range(1, len(dates))],
    )


def feature_vector(closes: list[float], stock_r: list[float],
                   market_r: list[float], sector_r: list[float]) -> dict:
    """The frozen 13-feature vector, in `FEATURES` order (unrounded).

    Raises ValueError when the evidence is too short — the caller waives.
    """
    if len(closes) < MIN_SESSIONS:
        raise ValueError(f"need >= {MIN_SESSIONS} sessions, got {len(closes)}")
    if min(len(stock_r), len(market_r), len(sector_r)) < CROSS_ASSET_WINDOW:
        raise ValueError("cross-asset window too short")

    vol_14 = _stdev(_returns(closes, 14)) * 100.0
    vol_60 = _stdev(_returns(closes, 60)) * 100.0
    band_mid = _sma(closes, 20)
    band_sigma = _stdev(closes[-20:])
    last = closes[-1]

    return {
        "ret_1": (last / closes[-2] - 1.0) * 100.0,
        "ret_5": (last / closes[-6] - 1.0) * 100.0,
        "ret_22": (last / closes[-23] - 1.0) * 100.0,
        "rsi_14": _rsi(closes),
        "ema20_slope_5": (_ema_last(closes, 20) / _ema_last(closes[:-5], 20) - 1.0)
        * 100.0,
        "sma50_dist": (last / _sma(closes, 50) - 1.0) * 100.0,
        "sma200_dist": (last / _sma(closes, 200) - 1.0) * 100.0,
        "vol_14": vol_14,
        "vol_ratio_14_60": (vol_14 / vol_60) if vol_60 else 0.0,
        "bollinger_bw_20": (4.0 * band_sigma / band_mid * 100.0) if band_mid else 0.0,
        "zscore_20": ((last - band_mid) / band_sigma) if band_sigma else 0.0,
        "beta_20_nifty": _beta(stock_r, market_r),
        "corr_20_sector": _corr(stock_r, sector_r),
    }


def forward_outcome(sessions: list[dict], index: int) -> tuple[float, float, float]:
    """(return %, MAE %, MFE %) of the 22 sessions after `index`.

    The excursions are the path's *extremes*: MAE is the window's worst low
    against the entry (non-positive) and MFE its best high (non-negative), with
    the return read from the closing session — the realization the coverage
    gate reads forward off the bars, and the one the corpus vintages carry as
    `open/high/low/close` (BRD §5.2, excursion realization). Measuring the
    closes instead would build a strictly narrower envelope than the quantity
    being graded: a close path never leaves the bar path that contains it.

    The row-by-row invariant still holds — $\\text{mae} \\le \\text{terminal}
    \\le \\text{mfe}$, since a minimum of lows is at or below the last close and
    a maximum of highs at or above it — which is what `CONE_SPEC`'s monotonicity
    rests on.
    """
    entry = float(sessions[index]["close"])
    window = sessions[index + 1:index + 1 + HORIZON]
    if len(window) < HORIZON:
        raise ValueError("not enough forward sessions")
    terminal = (float(window[-1]["close"]) / entry - 1.0) * 100.0
    worst_low = (min(float(s["low"]) for s in window) / entry - 1.0) * 100.0
    best_high = (max(float(s["high"]) for s in window) / entry - 1.0) * 100.0
    return terminal, min(0.0, worst_low), max(0.0, best_high)


def close_path_outcome(sessions: list[dict], index: int) -> tuple[float, float, float]:
    """The same window measured on the *closes* instead of the bars.

    Not a second way to build the cone — the atoms are `forward_outcome`'s — but
    the labelled alternative its choice is measured against: the trainer's CV
    block reports the shipped envelope's containment of the close path beside its
    containment of the bar path, so what the realization costs (or buys) is a
    number in the artifact rather than an argument in a comment (BRD §5.2).
    """
    entry = float(sessions[index]["close"])
    window = sessions[index + 1:index + 1 + HORIZON]
    if len(window) < HORIZON:
        raise ValueError("not enough forward sessions")
    relative = [(float(s["close"]) / entry - 1.0) * 100.0 for s in window]
    return relative[-1], min(0.0, min(relative)), max(0.0, max(relative))


def first_touch_outcome(bars: list[dict], stop: float,
                         target: float) -> tuple[str, str]:
    """`(outcome, basis)` — the frozen race scan: first touch wins.

    Bars are scanned in order and the first level printed decides — the trade
    is flat the moment either level prints. Daily bars cannot order touches
    *within* a bar, so a bar carrying both levels resolves to the worse one
    (LOSS).

    This is the **single** implementation of the race, and it is the one the
    benchmark's `$derive` rule, the curator's seed scan, the catalog
    scaffolder's tape comparison and the migration tool's row scan all use:
    `tests/evals/dataset_refs.resolution_of` aliases this function, so a
    corpus label and a benchmark trade's recorded resolution can never scan
    differently under one name (decision D, 2026-10-16).
    """
    for bar in bars:
        hit_stop, hit_target = bar["low"] <= stop, bar["high"] >= target
        if hit_stop and hit_target:
            return "LOSS", "both_hit_conservative_loss"
        if hit_stop:
            return "LOSS", "stop_first"
        if hit_target:
            return "WIN", "target_first"
    return "TIME", "horizon_end"


def classify_outcome(entry: float, window: list[dict]) -> str:
    """The reference-band race label: `TARGET_FIRST` / `STOP_FIRST` / `NEITHER`.

    `entry` is the row's own session close and `window` its next `HORIZON`
    sessions; the levels are `RACE_BAND_PCT` either side of the entry, read as
    a trade's target and stop. The scan is `first_touch_outcome`, so the label
    means exactly what the benchmark's recorded `resolution_outcome` means —
    a WIN at the reference band is `TARGET_FIRST`, a LOSS `STOP_FIRST`, and a
    window that prints neither level inside the horizon `NEITHER`.
    """
    target = float(entry) * (1.0 + RACE_BAND_PCT / 100.0)
    stop = float(entry) * (1.0 - RACE_BAND_PCT / 100.0)
    outcome, _basis = first_touch_outcome(window, stop, target)
    return {"WIN": "TARGET_FIRST", "LOSS": "STOP_FIRST",
            "TIME": "NEITHER"}[outcome]


# --------------------------------------------------------------------------
# Inference
# --------------------------------------------------------------------------

def predict(booster, features: dict):
    """(probabilities by class, exact tree-SHAP contributions for the winner)."""
    row = [[features[name] for name in FEATURES]]
    probabilities = dict(zip(CLASSES, (float(p) for p in booster.predict(row)[0])))
    winner = max(CLASSES, key=lambda cls: probabilities[cls])
    class_index = CLASSES.index(winner)
    contributions = booster.predict(row, pred_contrib=True)[0]
    block = len(FEATURES) + 1  # one contribution per feature + the base value
    slice_ = contributions[class_index * block:(class_index + 1) * block]
    drivers = [
        {
            "feature": name,
            "value": round(float(features[name]), 6),
            "contribution": round(float(slice_[i]), 6),
        }
        for i, name in enumerate(FEATURES)
    ]
    drivers.sort(key=lambda item: (-abs(item["contribution"]), item["feature"]))
    return probabilities, winner, drivers[:SHAP_TOP_K]


def _pooled(samples: dict, probabilities: dict) -> list[tuple[float, float]]:
    """(value, weight) pairs of the class-probability-weighted mixture.

    Pooling the *observed* per-class samples is exact — no vincentized
    approximation of a mixture's quantiles — and because the weights sum to 1
    the pooled step distribution is a proper distribution whose quantile
    function is monotone in the level by construction.
    """
    pairs: list[tuple[float, float]] = []
    for cls in CLASSES:
        values = samples.get(cls) or []
        if not values:
            continue
        weight = float(probabilities.get(cls, 0.0)) / len(values)
        pairs.extend((float(v), weight) for v in values)
    pairs.sort(key=lambda item: item[0])
    return pairs


def mixture_quantile(pairs: list[tuple[float, float]], level: float) -> float:
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= level - 1e-12:
            return value
    return pairs[-1][0]


def mixture_prob_at_least(pairs: list[tuple[float, float]], threshold: float) -> float:
    return sum(weight for value, weight in pairs if value >= threshold)


def race_probability(probabilities: dict, samples: dict, target_pct: float,
                     stop_pct: float) -> float:
    """P(target hit before the stop) — the conservative product (decision D).

    Both factors are read off the same class-weighted pools the cone reads:
    `P(best high ≥ target)` from the favourable-excursion atoms and
    `P(worst low > −stop)` from the adverse ones. Their product **understates**
    the first-touch race whenever a window reaches the target while also
    breaching the stop later (or vice versa) — the two events are positively
    associated — so this is a disclosed lower bound on the race, not an
    estimate tuned to look like one. A `stop_pct ≤ 0` means the stop sits at or
    beyond the entry: the race is lost on print, whatever the target does.
    """
    if stop_pct <= 0:
        return 0.0
    reached = mixture_prob_at_least(_pooled(samples["mfe_pct"], probabilities),
                                    target_pct)
    held = mixture_prob_at_least(_pooled(samples["mae_pct"], probabilities),
                                 -float(stop_pct))
    return min(1.0, max(0.0, reached * held))


def direction_label(probabilities: dict) -> str:
    """STRONG_TARGET_FIRST at P(TARGET_FIRST) >= 0.60; else the winning class."""
    if probabilities["TARGET_FIRST"] >= STRONG_TARGET_PROB:
        return "STRONG_TARGET_FIRST"
    return max(CLASSES, key=lambda cls: probabilities[cls])


def _sector_block_usable(block) -> bool:
    """Can this sector's block carry the cone's quantiles on its own?

    Every measure's every class must hold at least
    `MIN_SECTOR_ATOMS_PER_CLASS` observations: the level below that is a guess
    about a tail, and a class with no atoms would silently drop weight out of
    the mixture rather than widen it.
    """
    if not isinstance(block, dict):
        return False
    for measure in CONE_MEASURES.values():
        per_class = block.get(measure)
        if not isinstance(per_class, dict):
            return False
        for cls in CLASSES:
            values = per_class.get(cls)
            if not isinstance(values, list) \
                    or len(values) < MIN_SECTOR_ATOMS_PER_CLASS:
                return False
    return True


def samples_for(calibration: dict, sector_ticker: str | None) -> dict:
    """The pools the cone is built from: the trade's own sector, or pooled.

    The cone's levels are quantiles of the *observed* 22-session excursions, so
    which observations enter the mixture decides the envelope, and one pooled
    block mixes a bank's dispersion with an IT stock's and an energy major's
    while gate 5 grades one specific trade. The calibration ships both — a block
    per sector, plus the pooled block the trainer has always written — and the
    trade's sector's block answers when it is deep enough
    (`_sector_block_usable`). Otherwise the pooled block answers, exactly as it
    did before the blocks existed: a thin or missing block is a disclosed
    fallback, never a waiver.
    """
    blocks = calibration.get("samples_by_sector") or {}
    block = blocks.get(sector_ticker) if sector_ticker else None
    if _sector_block_usable(block):
        return block
    return calibration["samples"]


def cone_levels_pct(samples: dict, probabilities: dict) -> dict:
    """The 22-session cone's levels, in percent of the entry (see `CONE_SPEC`).

    `samples` is a calibration-shaped block (measure → class → observed values)
    and `probabilities` the class mixture to weight it with. Lower quartiles come
    from the mixture of adverse excursions, the median from terminal returns,
    the upper quartiles from favourable excursions; because every training row
    contributes one atom to all three pools with the same weight and
    `mae ≤ terminal ≤ mfe` holds row by row, the levels are monotone and always
    contain the terminal interval — no clamping, no assertion needed. Percent
    space is what the trainer's cross-validation measures, so it stops here and
    the price form is a multiplication.
    """
    return {
        key: mixture_quantile(_pooled(samples[CONE_MEASURES[measure]], probabilities),
                              level)
        for key, measure, level in CONE_SPEC
    }


def cone_envelope(samples: dict, probabilities: dict, live_price: float) -> dict:
    """The same cone as ₹ prices around the entry (2 dp)."""
    return {key: round(live_price * (1.0 + level / 100.0), 2)
            for key, level in cone_levels_pct(samples, probabilities).items()}


def stoploss_safely_below_mae(live_price: float, stoploss: float,
                              mae_projected: float) -> bool:
    """Is the recommended stop deeper than the projected adverse excursion?

    A stop inside the expected MAE is a stop the *noise* takes out, so it is
    reported (stderr) rather than silently accepted. Long setups only.
    """
    return stoploss <= live_price + mae_projected


def _waived_payload(reasons: list[str]) -> dict:
    payload = {key: None for key in PAYLOAD_KEYS}
    payload["waived"] = True
    return payload, reasons


def compute(payload: dict, sessions: list[dict] | None, bundle: dict | None,
            bundle_reason: str | None, model) -> tuple[dict, list[str]]:
    booster, calibration, model_reason = model
    symbol = str(payload.get("symbol") or "").strip().upper()
    action = str(payload.get("action") or "BUY").strip().upper()
    sector_ticker = SECTOR_INDEX_MAP.get(symbol)

    reasons: list[str] = []
    if model_reason:
        reasons.append(model_reason)
    if action != "BUY":
        reasons.append(f"action:unsupported:{action}")
    if sector_ticker is None:
        reasons.append(f"symbol:unmapped:{symbol}")
    if sessions is None:
        reasons.append("ohlcv:absent")
    if bundle_reason:
        reasons.append(bundle_reason)

    market_closes = sector_closes = None
    if bundle is not None:
        series = bundle["series"]
        for ticker, slot in ((NSEI, "market"), (sector_ticker, "sector")):
            if ticker is None:
                continue
            entry = series.get(ticker)
            if not isinstance(entry, dict) or not isinstance(entry.get("values"), list):
                reasons.append(f"series:{ticker}:absent")
            elif slot == "market":
                market_closes = entry["values"]
            else:
                sector_closes = entry["values"]

    if reasons:
        return _waived_payload(reasons)

    try:
        stock_by_date = {str(r["date"]): float(r["close"]) for r in sessions}
        market_by_date = {str(p["date"]): float(p["value"]) for p in market_closes}
        sector_by_date = {str(p["date"]): float(p["value"]) for p in sector_closes}
        windows = common_return_windows(stock_by_date, market_by_date, sector_by_date)
        if windows is None:
            return _waived_payload(["align:insufficient"])
        closes = [float(r["close"]) for r in sessions]
        features = feature_vector(closes, *windows)
        probabilities, _winner, drivers = predict(booster, features)
    except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError):
        # Evidence that does not compute is a data gap, not a crash.
        return _waived_payload(["fixture:unreadable"])

    live_price = float(payload["live_price"])
    sample_sets = samples_for(calibration, sector_ticker)
    if sample_sets is not calibration["samples"]:
        # Disclosed on stderr because the payload contract is frozen (eight
        # keys, TICK-004 §2) and because which pools answered is a property of
        # the calibration, not of the trade.
        print(f"[info] stage 4: cone pools from {sector_ticker} "
              f"({sum(len(v) for v in sample_sets['mae_pct'].values())} "
              "observed excursion atom(s))", file=sys.stderr)
    cone = cone_envelope(sample_sets, probabilities, live_price)
    # The projected excursions stay the mixtures' medians in ₹ around the entry.
    mae = _pooled(sample_sets["mae_pct"], probabilities)
    mfe = _pooled(sample_sets["mfe_pct"], probabilities)
    mae_projected = round(live_price * mixture_quantile(mae, 0.50) / 100.0, 2)
    mfe_projected = round(live_price * mixture_quantile(mfe, 0.50) / 100.0, 2)
    target_return_pct = (float(payload["target"]) / live_price - 1.0) * 100.0
    stop_pct = (live_price - float(payload["stoploss"])) / live_price * 100.0
    prob_target = round(race_probability(probabilities, sample_sets,
                                         target_return_pct, stop_pct), 4)

    result = {
        "prob_target_hit_22d": prob_target,
        "price_cone_p10_p50_p90": cone,
        "mae_projected": mae_projected,
        "mfe_projected": mfe_projected,
        "shap_drivers": drivers,
        "direction_label": direction_label(probabilities),
        "direction_probability": round(probabilities["TARGET_FIRST"], 4),
        "waived": False,
    }
    return result, []


def main() -> int:
    parser = argparse.ArgumentParser(
        description="UVEST Stage 4: LightGBM 1-Month Direction & Volatility Cone")
    parser.add_argument("--input", default="current_trade.json",
                        help="Path to canonical current_trade.json")
    parser.add_argument("--ohlcv-fixture", default=None,
                        help="Recorded daily-OHLCV file for the stock (the same "
                             "evidence stage 2 reads; omit for the waiver path)")
    parser.add_argument("--context-fixture", default=None,
                        help="The shared uvest-market-context/1 bundle (the same "
                             "one stage 7 classifies; omit for the waiver path)")
    parser.add_argument("--model", default=None,
                        help="LightGBM booster artifact (default: the committed "
                             "models/lgbm_direction_v1.txt)")
    parser.add_argument("--calibration", default=None,
                        help="Model calibration artifact (default: the committed "
                             "models/lgbm_direction_v1.calibration.json)")
    add_record_arg(parser)

    args = parser.parse_args()

    payload = load_payload(args.input)
    sessions = load_ohlcv(args.ohlcv_fixture)
    bundle, bundle_reason = load_context(args.context_fixture)
    model = load_model(args.model or DEFAULT_MODEL,
                       args.calibration or DEFAULT_CALIBRATION)
    result, reasons = compute(payload, sessions, bundle, bundle_reason, model)

    output_str = json.dumps(result, indent=2) + "\n"

    bundle_dir = resolve_bundle_dir(args.record, payload.get("symbol") or "UNSPECIFIED",
                                    payload.get("recommended_date"))
    if bundle_dir is not None:
        record_artifact(bundle_dir, 4, output_str)

    if result["waived"]:
        print(f"[waived] stage 4: {', '.join(reasons)}", file=sys.stderr)
    elif not stoploss_safely_below_mae(float(payload["live_price"]),
                                       float(payload["stoploss"]),
                                       result["mae_projected"]):
        print("[warn] stage 4: recommended stoploss sits inside the projected "
              f"22-session MAE ({result['mae_projected']:+.2f} INR)",
              file=sys.stderr)

    sys.stdout.write(output_str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
