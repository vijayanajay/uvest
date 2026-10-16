# TICK-004: LightGBM 1-Month Direction & Volatility Cone

**Status:** FROZEN  
**Priority:** P1  
**Test Seam:** `tests/test_4_ml_direction.py`  
**Target Script:** `scripts/4_ml_direction.py`  
**Output Artifact:** `out_4_ml.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 4)

### 1. Goal & Context

Stage 4 is the **Machine Learning Directional and Volatility Range Engine** (BRD §5.2, §9.1 row 4). It consumes `current_trade.json`, daily OHLCV from stage 2, and historical features to deliver:
1. **Directional Probability:** LightGBM classifier prediction ($P(\text{Bullish})$, $P(\text{Neutral})$, $P(\text{Bearish})$) calibrated to achieve Brier score $\le 0.18$.
2. **22-Day Volatility Price Cone:** Statistical expected price envelope ($[P_{10}, P_{25}, P_{50}, P_{75}, P_{90}]$), projected Max Adverse Excursion (MAE), and Max Favorable Excursion (MFE).
3. **Feature Attribution:** Top SHAP drivers explaining the model prediction.

**Deliberately out of scope (do not build here):**
- FastDTW geometric pattern matching → stage 3 (`scripts/3_dtw_pattern.py`).
- Institutional order flow matrix → stage 5 (`scripts/5_institutional_flow.py`).
- Composite scoring & verdict → stage 9 (`scripts/9_decision_engine.py`).

### 2. Frozen Specification Reference

- `BRD.md` §4 (The Three Core Predictions: Direction & Expected Range).
- `BRD.md` §5.2 (LightGBM 1-Month Direction Engine).
- `BRD.md` §9 Script Inventory: `prob_target_hit_22d`, `price_cone_p10_p50_p90`, `mae_projected`, `shap_drivers`.
- `BRD.md` §10.4 Waiver Rule: If model weights or features are missing, returns `waived: true` with all metrics `null` and exit code 0. A waived stage caps the eventual verdict at 🟡 `GO_WEAK` (BRD §10.4).
- `BRD.md` §15.3 — **S2 (Calibration:** $|\text{predicted } P_{\text{win}} - \text{realized win frequency}| \le 10\text{pp}$ per verdict bucket) and **S7 (Marginal Band Honesty)**. These are the authoritative gates; `docs/agents/evals.md` §3 dimensions 4–5 (Brier $\le 0.18$, cone coverage $\ge 80\%$) remain this stage's own thresholds.

**Canonical output contract** (mirrors `BRD.md` §9 inventory):

| Key | Type | Meaning |
| :--- | :--- | :--- |
| `prob_target_hit_22d` | float \| null | Calibrated probability of reaching target before stop |
| `price_cone_p10_p50_p90` | dict \| null | Percentile price envelope of the 22-session **path** — the sessions' lows and highs, the realization evals dimension 5 grades: `p10`/`p25` from the excursion mixture of adverse excursions (worst low vs the entry), `p50` from terminal returns, `p75`/`p90` from favourable excursions (best high) |
| `mae_projected` | float \| null | Projected Maximum Adverse Excursion in ₹ |
| `mfe_projected` | float \| null | Projected Maximum Favorable Excursion in ₹ |
| `shap_drivers` | list[dict] \| null | Top 5 predictive feature contributions |
| `direction_label` | str \| null | `STRONG_BULLISH` \| `BULLISH` \| `NEUTRAL` \| `BEARISH` |
| `direction_probability` | float \| null | Calibrated $P(\text{direction up})$: $P(\text{BULL}) + \varphi \cdot P(\text{NEUTRAL})$, the event evals dimension 4 grades |
| `waived` | bool | `true` if model/features unavailable |

### 3. TDD Acceptance Criteria

- [x] Consumes canonical `current_trade.json` and evaluates 22-session forward trajectory.
- [x] States the graded direction event explicitly: `direction_probability` = $P(\text{BULL}) + \varphi \cdot P(\text{NEUTRAL})$ with $\varphi$ read from the calibration — never `prob_target_hit_22d`, which answers the target-versus-stop race.
- [x] Price cone envelope contains $P_{10} \le P_{25} \le P_{50} \le P_{75} \le P_{90}$ monotonically.
- [x] The envelope is the **path** the coverage gate grades: its lower quartiles are the adverse-excursion mixture's and its upper quartiles the favourable-excursion mixture's, so it contains the terminal interval rather than approximating it (BRD §5.2 cone definition, 2026-10-14).
- [x] The path's atoms are the forward sessions' **extremes** — `mae_pct` the window's worst low against the entry, `mfe_pct` its best high, both read from the corpus's `open/high/low/close` vintages by one `forward_outcome` implementation the trainer imports — the same realization the coverage gate reads off the forward bars (BRD §5.2 excursion realization, 2026-10-15).
- [x] Validates whether recommended stoploss is safely positioned below projected MAE.
- [x] Supports offline deterministic execution via recorded fixtures.
- [x] On missing model artifacts, executes waiver path: `waived: true`, null fields, exit 0.

### 4. Implemented (2026-10-14)

**Evidence — one bundle, two consumers (ADR-010 decision 4).** `--context-fixture`
takes the *same* `uvest-market-context/1` artifact stage 7 classifies; the stock's own
history arrives as `--ohlcv-fixture`, the same file stage 2 reads. The runner fetches
live context once (ahead of the first consumer) and hands that one bundle to both, so
there is no second fetcher and no second cache. The sectoral ticker comes from the
shared `SECTOR_INDEX_MAP`, extracted to `scripts/_sector_map.py` when this stage became
the map's second consumer.

**Frozen conventions.** 13 features in a frozen order, computed by one
`feature_vector` that the trainer (`tests/evals/train_direction_model.py`) and this
stage share, so a feature cannot mean one thing in training and another at inference.
Close-based features only (the committed history carries closes); `beta_20_nifty`
returns 0.0 below a documented market-variance floor, because a near-flat index has no
estimable slope and a spuriously large beta would read as conviction. Labels are
`BULL` / `NEUTRAL` / `BEAR` at the strict ±3% bands; `STRONG_BULLISH` at
$P(\text{BULL}) \ge 0.60$.The cone is the class-probability-weighted mixture of the observed **path** — `p10`/`p25` inverted exactly from the pooled adverse excursions, `p75`/`p90` from the pooled favourable excursions, `p50` from the pooled terminal returns — so it contains the terminal interval and stays monotone in level by construction (each observed row has $\text{mae} \le \text{terminal} \le \text{mfe}$ and the three pools share one weight vector) — and MAE/MFE are that mixture's medians in ₹ around the entry. The path is measured on the sessions' **extremes**, not their closes: `forward_outcome` takes the window's worst low and its best high against the entry, so the atoms are the same objects the coverage gate compares against the forward bars, and a close-based pool would build a strictly narrower envelope than the quantity being graded. A terminal-return interval is *not* this quantity: measured over the corpus it covers the path in 49.5% of rows against 72.3% for the close, which is why the envelope is built from the excursions the artifact already carries. **Which observations enter the pools is part of the frozen definition too** (ADR-011 decision 13, 2026-10-15): the calibration ships one block per sectoral index beside the pooled block, and `samples_for` answers with the trade's own sector's when every class of every measure holds at least `MIN_SECTOR_ATOMS_PER_CLASS` (30) atoms, the pooled block otherwise — a thin block is a disclosed fallback (stderr), never a waiver, and the 8-key payload does not grow. Measured over the 10,869-row corpus, 99.72% of out-of-fold rows read their own sector's block and the shipped envelope's out-of-fold bar-path containment is 76.02% against 77.10% pooled: more faithful, slightly narrower, both published (`docs/agents/evals.md` §2). `prob_target_hit_22d` is the mixture's completion mass at the recommended
target; the target-vs-stop race is reported by the MAE side, and a stop inside the
projected MAE raises a stderr warning rather than passing silently.

**Provenance — sourced (2026-10-14).** The booster + calibration are trained offline
by `tests/evals/train_direction_model.py` from the committed sourced corpus: real
yfinance daily bars per symbol-month (`tests/fixtures/history/`) plus
`indices_daily.json`, the index panel, built and disclosed series by series by
`tests/evals/build_index_corpus.py` — which source served it, and what each source said
about the ones neither serves (ADR-011 decision 12: Yahoo where it has the history,
NSE's own daily index snapshot for the sectors it does not, each series naming its
source, a day the archive could not be reached failing the build rather than reading as
an absent session). Rows are
point-in-time (a row is skipped when the index has no session of its own on the row's
date, so features never carry a stale market window) **and their forward label window
must lie inside one committed vintage** — the vintages are not consecutive, so a window
taken off the merged series can span a calendar gap (ADR-011 decision 11; the dropped
rows are counted in `provenance.corpus.label_window`) — **and their 200-session feature
history must end inside one gapless run of the merged series** (overlapping vintages
chain; a hole no fetch covers ends a run — ADR-011 decision 14, the dropped rows
counted in `provenance.corpus.feature_window`). Features come from this stage's
own `feature_vector`, and validation is BRD §5.2's purged group time-series CV: five
contiguous session blocks, each symbol purged against the block's date span *on its own
session calendar* (a symbol halted through the fold is still purged — its two-session
label window covers the fold), plus a 5-session embargo tail. The calibration carries
`origin: sourced`, the corpus, the excluded symbols and the out-of-fold metrics in its
own `provenance` block; the authored one-stock stand-in and its builder were retired in
the same change. The model file is LF-pinned in `.gitattributes`: LightGBM's parser
rejects a CRLF rewrite.

**Waivers (BRD §10.4).** Absent model/calibration, absent or short OHLCV, absent /
unreadable / mis-schema'd bundle, a missing series, no common date window, an unmapped
symbol or a non-`BUY` action each waive the whole payload — every metric `null`, reasons
on stderr, exit 0. Nothing is fabricated to avoid a waiver.

**Training seam.** `tests/test_train_direction_model.py` (34 tests): a synthetic
sourced-shaped corpus is trained end-to-end (artifacts, provenance, determinism), the
row builder is proven point-in-time by equivalence against a corpus cut short of a
synthetic regime change (with a vacuity guard proving the change was real), the label
rule is asserted on a two-vintage corpus with a real calendar gap (exactly the windows
the gap invalidates are dropped, on both sides of it) *and* over every row the committed
corpus builds (one vintage contains each row's whole window; the corpus is shown to have
a real gap so the seam cannot go vacuous), the sector pools are asserted to hold only
their own sector's rows and to be the union of the pooled block, and the shipped
artifact's block disclosure is checked against the blocks it describes. The
split is asserted both on a hand-built calendar (exact test/purged/train membership,
including the halted-symbol group case) and as a property over real rows (no training
row's label window reaches its fold, nothing inside the embargo tail, every row tested
exactly once, folds contiguous and ordered). `tests/test_build_index_corpus.py` (21
tests) holds the corpus contract — no thin series, no silently dropped ticker, no
unordered or duplicated sessions, sourced provenance — plus the two-source rules: the
official name map covers the whole panel, the fallback is asked only about what the
first source missed, neither-source series stay absent naming both reasons, and a day
the archive could not be reached fails the fetch without ever being cached as empty.

**Seam.** `tests/test_4_ml_direction.py` (48 tests): the exact 8-key contract, cone
selection between a trade's own sector's pools and the pooled fallback (a deep block is
read, a thin/incomplete/absent one defers — end to end through the shipped booster, the
payload's eight keys unmoved), cone
monotonicity, the shared-bundle read proven by dropping each series, the frozen
conventions — including the excursion realization: the window's bars bracket its
closes, T_rec's own wicks stay out of it, and the close-path reading agrees on the
terminal and only widens downward — the $P(\text{up})$ bridge and its refused
calibrations, every waiver arm, artifact provenance (the CV block names the
realization each coverage figure covered), one CLI invocation and `--record`
identity.
