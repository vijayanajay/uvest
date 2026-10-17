# TICK-010: Standalone Interactive HTML Visualizer

**Status:** FROZEN  
**Priority:** P1  
**Test Seam:** `tests/test_12_generate_html_report.py`  
**Target Script:** `scripts/12_generate_html_report.py`  
**Output Artifact:** `reports/report_{SYMBOL}_{DATE}.html`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 12)  

### 1. Goal & Context
Generate a self-contained, standalone interactive HTML dashboard (`reports/report_{SYMBOL}_{DATE}.html`) for every analyzed recommendation. Can be opened directly in any browser (`file:///...`) without running a web server.

### 2. Frozen Specification Reference
- `BRD.md Section 13`
- Prototype: `reports/sample_report.html` — **a regenerated artifact, not a mock** (since 2026-10-16): the stage-12 report of proof record `GB-2022-0001` (the benchmark's first record, `GO` / `clean_bullish`, carrying stage-4 evidence so the gauge is drawn). Refresh it with `python tests/evals/report_regen.py --prototype`; `TestTheTrackedPrototypeIsAFreshRender` fails if the committed bytes are not a byte-exact fresh render of that record, and it is graded like any other report (differ-clean, HTML-valid, no retired probability). Its legacy `instrument` disclosure is deliberate: the record says `FUT` while its pinned replay input is `CASH` (`authoring.legacy_instrument`), and the report renders the input it was actually produced from. Its header now carries a **real badge** (since 2026-10-17): `tests/evals/record_stage9_evidence.py` recorded `out_9_decision.json` on all 58 records, so `GB-2022-0001`'s header reads `GO_WEAK · Composite score 16.92/32.50 · Effective score % 52.06 · Calibrated P(win) 0.5582 · Expectancy 0.7863` straight out of the `$ref`'d artifact — every figure of it differ-graded like any other number in the report.
- Inputs: `current_trade.json`, `out_1_math.json` through `out_9_decision.json`
- Output: `reports/report_{SYMBOL}_{DATE}.html`

### 3. TDD Acceptance Criteria
- [ ] Generates valid HTML5 with 0 external resource errors (self-contained CSS and SVG charts).
- [x] Displays the verdict badge in the canonical 7-state vocabulary (pre-entry 🟢 GO / 🟡 GO_WEAK / ⏳ WAIT / 🔴 AVOID; post-entry 🔵 HOLD / 🟠 PRESSURE / ⛔ EXIT — BRD §10.4) alongside the composite score, the calibrated $P(\text{win})$, and expectancy. **Landed 2026-10-17 with real evidence:** `record_stage9_evidence.py` recorded `out_9_decision.json` on all 58 records, the artifact is `$ref`d from `script_outputs`, and the badge transcribes it — composite score as §13.2's `final/max` pair (the §10.4 waiver rule drops the maximum below 100, so the artifact's own pair is the only honest denominator; a maximum the artifact lacks is never invented), plus `effective_score_pct`, `p_win` and `expectancy` through `fmt`. Seam: `TestTheVerdictBadgeIsStage9Evidence`.
- [ ] Visualizes the 3 Core Predictions: Target-vs-Stop Race gauge (`TARGET_FIRST` / `STOP_FIRST` / `NEITHER`, each slice the artifact's own race probability — decision D, BRD §5.2/§13.2), 22-day Volatility Price Cone chart, and Optimal Limit Entry vs Univest Entry Range bar. **The gauge never states a probability as a directional (up/down) one**, which is what stage 12's seam grades (`tests/test_12_generate_html_report.py`, `directional_probability_problems`).
- [ ] Displays Institutional Flow Matrix (Bhavcopy Delivery % + Futures OI buildup).
- [ ] Displays Fundamental Health card (Piotroski, Altman Z, 0% Pledge check).
- [ ] Renders Decision Flip Table and Actionable Order Execution Ticket.

### 4. Implemented — the race-vocabulary audit (2026-10-16)

Decision D moved stage 4's event onto the **target-versus-stop race**, and the
presentation side had not followed it: this ticket still asked for a *directional*
gauge, BRD §13.2's dashboard mock-up still drew the three retired up/down slices
(78% / 14% / 8%), and the script titled that card after the retired event while
rendering two probabilities the engine now reads as `P(TARGET_FIRST)` at two
different geometries. **A probability the engine never answered was one
implementation away from being drawn**, which is the defect — not a wording
nit. The retirement itself is recorded once, in BRD §5.2, which is why this
section names the retired framing rather than quoting it.

Landed: `scripts/12_generate_html_report.py` renders the card as
**`Target-vs-Stop Race Gauge & 22-Day Volatility Price Cone`**, states the
trade's own race in a caption, and reads the artifact's fields under race
prose (`RACE_FIELD_LABELS`: `Race label`, `P(target first) · reference band`,
`P(target first) · this trade's target & stop`) — the *values* are untouched and
still `fmt`-exact, so the Zero-Hallucination gate keeps grading those bytes. The
specs were re-frozen with it (BRD §1.1, §4, §13.2, §15's worked example; TICK-004
§1/§2/§3/§4; this ticket; `actionplan.md`'s stage-4 checklist and Stage-12
development blueprint).

**The gauge itself (landed 2026-10-16, second pass).** `race_svg` draws §13.2's
radial speedometer: a semi-circular arc (radius 80, `viewBox "0 0 200 120"`)
sliced into the artifact's own classes — `STOP_FIRST` crimson, `NEITHER` amber,
`TARGET_FIRST` emerald — with the needle at **`P(TARGET_FIRST) × 180°` measured
from the left**, which is §13.2's own sentence, so the retired up/down probability
cannot come back in through the geometry. All geometry lives in tag attributes and
the gauge's **visible text carries no digits**: every figure a reader sees is
still an artifact value in the card's rows, which is what keeps the drawing
differ-clean (`report_diff.visible_text` proves it in the seam). A payload with
no `direction_probability` draws **no gauge at all** — a needle-less dial is a
picture of a measurement nobody took — and an out-of-range value is clipped to
the arc rather than drawn past it.

**The guard now covers every specification document (not just the three that
draw the report).** `TestNoSpecDocumentStatesAProbabilityAsADirection` scans
**79 tracked documents** — `BRD.md`, `prompt.md`, `AGENTS.md`, `actionplan.md`,
`README.md`, `README_tech.md`, `business_test_cases.md`, `docs/**.md`, and both
skill trees (`skills/**.md`, `.agents/skills/**.md`) — for the
retired gauge's name or a *direction-first probability* — a class probability
written as a direction, i.e. a direction word carrying the number rather than the
market measure doing so (the exact patterns are in the seam; this ticket describes
them rather than quoting them, so the guard does not flag its own record). The
measure-first vocabulary the catalog, the breaker and stage 2 legitimately speak —
breadth at a percentage, stage 2's trend enum, the `clean_bullish` curation class —
is explicitly not that. Exemptions are declared one line at a time in
`ATTESTED_USES` with a reason, and a stale exemption fails its own test. Two live
stale references fell out of the widening: `prompt.md`'s own "Three Core
Predictions" asked the agent for the three up/down class probabilities, and
evals.md twice called stage 4's field by the retired directional name — both
re-frozen. The only attested use left is evals.md's historical note recording what
the retired up/down model predicted while that event was the graded one.

The scope is **total rather than a glob list**, which the skills proved the need
for: `skills/html-visualizer/SKILL.md` and its `.agents/skills/` mirror still
prescribed the retired speedometer over the three up/down class probabilities —
the skill that *generates this report* — and a glob over `docs/` could not have
seen them. Both now state §13.2's race gauge, and
`test_every_tracked_document_is_either_scanned_or_excused` reads `git ls-files`
back against the scan set: a new document joins the guard by default, and leaving
one out means arguing for it in `DOCUMENTS_OUT_OF_SCOPE` (today: `.trail/` session
records and the generated `graft/` graph).

Seam: `tests/test_12_generate_html_report.py` — every race class (and the
`STRONG_` form the field carries) must render verbatim, and
**`directional_probability_problems`** fails if any report states a probability as a
directional one, or revives the retired gauge's name, anywhere outside stage 2's
licensed `trend_state` row (a trend classification the payload itself carries,
not a probability). Two of its tests
grade the *specs* (BRD §13.2's dashboard block, this ticket, TICK-004 §2), because
a stale spec is how the retired vocabulary comes back — both failed red before the
re-freeze. Verified over the real artifacts: 58/58 regenerated reports carry the
race gauge, 27 render a race label (`TARGET_FIRST` 3 / `STRONG_TARGET_FIRST` 15 /
`STOP_FIRST` 9), and **zero** occurrences of a directional probability remain
outside stage 2's trend row.
