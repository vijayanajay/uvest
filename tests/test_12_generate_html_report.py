"""Seam — stage 12 writes the canonical report the differ grades (TICK-010).

The BRD §9.1 contract names the artifact (`reports/report_{SYMBOL}_{DATE}.html`,
written by the script itself), and the Zero-Hallucination gate grades those
bytes against the record's own JSON (`tests/evals/report_diff.py`). What is
frozen here:

1. The CLI writes the canonical filename and prints a JSON status line.
2. Output is byte-deterministic and genuinely standalone (no external
   resources, charts as inline SVG).
3. **The acceptance property:** every golden record gets a report at its
   canonical path, and every one of them is differ-clean — the gate grades
   them 100.0%. This is the runner-level proof that the visualizer cannot
   hallucinate a number. The same artifacts must satisfy the HTML-validity
   gate (HTML5 doctype, zero external resources, self-contained styling,
   inline SVG): the artifact is the evidence, not a curator's flag (ADR-007).
4. Absent stage payloads render honest placeholders, and an unusable input
   exits non-zero instead of writing a half-truth.
"""

from __future__ import annotations

import html as html_mod
import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
STAGE12 = REPO_ROOT / "scripts" / "12_generate_html_report.py"
STAGE4 = REPO_ROOT / "scripts" / "4_ml_direction.py"
BRD = REPO_ROOT / "BRD.md"
TICK010 = REPO_ROOT / "docs" / "tickets" / "TICK-010-html-visualizer.md"
TICK004 = REPO_ROOT / "docs" / "tickets" / "TICK-004-lightgbm-cone.md"
DATASET_FILE = TESTS_DIR / "evals" / "golden_dataset.json"
MANIFEST_FILE = TESTS_DIR / "proof" / "scenario_manifest.json"

sys.path.insert(0, str(TESTS_DIR / "evals"))
sys.path.insert(0, str(TESTS_DIR))

import report_diff  # noqa: E402  (sibling eval modules, spliced above)
import report_regen  # noqa: E402
import run_evals  # noqa: E402


def synthetic_trade() -> dict:
    return {
        "symbol": "INFY",
        "exchange": "NSE",
        "instrument_type": "CASH",
        "action": "BUY",
        "entry_price": 1500.0,
        "entry_range_min": 1498.5,
        "entry_range_max": 1502.25,
        "live_price": 1500.0,
        "target": 1560.0,
        "stoploss": 1462.5,
        "lot_size": 1,
        "recommended_date": "2026-01-05",
        "user_capital_inr": 500000.0,
    }


def synthetic_payloads(root: Path) -> list[Path]:
    math = root / "out_1_math.json"
    math.write_text(json.dumps({"quantity": 1, "risk_inr": 37.5, "reward_inr": 60.0,
                                "raw_rr": 1.6, "live_rr": 1.6,
                                "inverted_rr_flag": False}), encoding="utf-8")
    market = root / "out_2_market.json"
    market.write_text(json.dumps({"atr_14": 15.25, "sl_atr_ratio": 2.459016,
                                  "is_tight_sl": False, "trend_state": "BULLISH",
                                  "waived": False}), encoding="utf-8")
    return [math, market]


def run_stage12(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(STAGE12), *args],
                          capture_output=True, check=False, cwd=REPO_ROOT)


def load_stage(path: Path, name: str):
    """A stage script as a module (the sibling tests' convention)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The card's vocabulary is a decision, not a field name (decision D)
# ---------------------------------------------------------------------------

# Stage 4's keys outlived the event they were named for: decision D (2026-10-16)
# moved dimension 4 onto the target-versus-stop race, so `direction_label`
# carries TARGET_FIRST / STOP_FIRST / NEITHER and `direction_probability` is
# P(target first) at the reference band — not an up/down probability. A report
# that still drew a Bullish/Neutral/Bearish speedometer would state a
# probability the engine never answered, which is a claim no payload backs.
# `bullish`/`bearish` is the property; `Direction gauge` / `Direction probability`
# are the retired *names* — for the two documents that specify what the report
# draws, either one is the same defect. The dimension's own name (BRD §4's
# "Direction (1-Month Swing)") is deliberately out of this pattern's scope: it
# names the prediction, not a probability.
DIRECTIONAL_WORD_RE = re.compile(
    r"\b(bullish|bearish)\b|Direction (?:gauge|probability)", re.IGNORECASE)
_MARKET_SECTION_RE = re.compile(
    r'<section class="card"><h2>Market Context \(stage 2\)</h2>.*?</section>',
    re.DOTALL)


def directional_probability_problems(html: str) -> list[str]:
    """Every place the document states a probability as bullish/bearish.

    Exactly one carve-out, licensed by the payload itself: stage 2's
    `trend_state`, an EMA-stack trend classification (TICK-013 §2 rules it
    BULLISH / BEARISH / MIXED) that renders as a string value inside the Market
    Context section. It is not a probability and it is not stage 4's event.
    Anywhere else — a gauge slice, a probability label, a stage-4 field — a
    direction word is the retired vocabulary coming back, and the offending
    span is named so the failure diagnoses itself.
    """
    rest = _MARKET_SECTION_RE.sub(" ", html)
    return [f"…{rest[max(0, m.start() - 70):m.end() + 50].strip()}…"
            for m in DIRECTIONAL_WORD_RE.finditer(rest)]


def race_payload(label: str, probability: float = 0.62) -> dict:
    return {"prob_target_hit_22d": 0.4571,
            "price_cone_p10_p50_p90": {"p10": 1470.0, "p50": 1530.0,
                                      "p90": 1590.0},
            "mae_projected": -37.4, "mfe_projected": 61.2, "shap_drivers": [],
            "direction_label": label, "direction_probability": probability,
            "waived": False}


class TestTheReportRendersTheRaceNotADirection(unittest.TestCase):
    """Stage 12's stage-4 card speaks decision D's vocabulary, verbatim.

    The artifact's own label is what a reader must see: the card may rename a
    field's *prose*, and it may not rename the class, and it may never present
    `direction_probability` / `prob_target_hit_22d` as an up/down probability.
    """

    @classmethod
    def setUpClass(cls):
        cls.stage12 = load_stage(STAGE12, "stage12_race_seam")
        cls.stage4 = load_stage(STAGE4, "stage4_race_seam")
        cls.classes = tuple(cls.stage4.CLASSES)

    def render(self, label: str, probability: float = 0.62) -> str:
        return self.stage12.render(synthetic_trade(), {
            "out_2_market.json": {"atr_14": 15.25, "trend_state": "BEARISH",
                                  "waived": False},
            "out_4_ml.json": race_payload(label, probability)})

    def test_every_race_class_renders_verbatim_and_no_direction_word_appears(self):
        self.assertEqual(self.classes, ("TARGET_FIRST", "STOP_FIRST", "NEITHER"),
                         "stage 4's frozen classes moved — this seam grades them")
        for label in (*self.classes, "STRONG_TARGET_FIRST"):
            with self.subTest(label=label):
                html = self.render(label)
                self.assertIn(f'<span class="val">{label}</span>', html,
                              "the artifact's own race label must render verbatim")
                self.assertEqual(directional_probability_problems(html), [])
                self.assertIn("Target-vs-Stop Race Gauge", html)
                self.assertNotIn("Direction Gauge", html)
                # Both geometries render, each at its own value.
                self.assertIn('<span class="val">0.4571</span>', html)
                self.assertIn('<span class="val">0.62</span>', html)
                for prose in self.stage12.RACE_FIELD_LABELS.values():
                    self.assertIn(html_mod.escape(prose, quote=True), html,
                                  "a race field fell back to its retired name")
                self.assertNotIn("Direction probability", html)

    def test_the_checker_catches_the_retired_directional_gauge(self):
        """Negative control — otherwise a green seam proves nothing."""
        retired = ('<!doctype html><style>a{}</style><svg></svg>'
                   '<div class="card"><h2>Direction Gauge</h2>'
                   '<div class="kv"><span class="k">Bullish</span>'
                   '<span class="val">78%</span></div>'
                   '<div class="kv"><span class="k">Bearish</span>'
                   '<span class="val">8%</span></div></div>')
        problems = directional_probability_problems(retired)
        self.assertEqual(len(problems), 3, problems)  # gauge name + both slices
        licensed = ('<section class="card"><h2>Market Context (stage 2)</h2>'
                    '<div class="kv"><span class="k">Trend state</span>'
                    '<span class="val">BEARISH</span></div></section>')
        self.assertEqual(directional_probability_problems(licensed), [],
                         "stage 2's trend classification is not a probability")
        self.assertEqual(len(directional_probability_problems(licensed + retired)), 3)


# BRD §13.2's numbers for the race gauge, read off the frozen mock-up rather
# than restated from the code: a change here is a spec decision, and it should
# fail until the spec moves with it.
BLUEPRINT_VIEW_BOX = "0 0 200 120"
BLUEPRINT_RADIUS = 80
BLUEPRINT_SLICES = (("STOP_FIRST", "#da3633", 0.0, 60.0),
                    ("NEITHER", "#d29922", 60.0, 120.0),
                    ("TARGET_FIRST", "#2ea043", 120.0, 180.0))


class TestTheRaceGaugeGeometry(unittest.TestCase):
    """BRD §13.2's radial speedometer, built from the artifact's own race.

    The retired gauge took its needle from `P(Bullish)`; this one takes it from
    the stage-4 artifact's own `direction_probability` (`P(TARGET_FIRST)` at the
    reference band), which is what the blueprint's own sentence says. Geometry
    lives in tag attributes, so the drawing itself claims no number: the gauge's
    **visible text carries no digits at all** (`report_diff.visible_text`), and
    every figure a reader sees is still an artifact value in the card's rows.
    """

    @classmethod
    def setUpClass(cls):
        cls.stage12 = load_stage(STAGE12, "stage12_gauge_seam")

    def gauge(self, probability):
        return self.stage12.race_svg(race_payload("TARGET_FIRST", probability)
                                     if probability is not None
                                     else {"direction_label": "NEITHER"})

    def test_the_blueprint_geometry_is_what_is_drawn(self):
        svg = self.gauge(0.62)
        self.assertIn(f'viewBox="{BLUEPRINT_VIEW_BOX}"', svg)
        for label, color, _start, _end in BLUEPRINT_SLICES:
            self.assertIn(color, svg, f"slice {label} left the gauge")
        self.assertEqual(len(re.findall(r"<path\b", svg)), 3,
                         "one arc per race class")
        self.assertIn(f'A {BLUEPRINT_RADIUS},{BLUEPRINT_RADIUS} 0 0 1', svg,
                      "the blueprint's radius 80 is the arc radius")
        self.assertIn("Target-vs-Stop race gauge", svg)

    def test_the_needle_is_the_artifacts_own_probability(self):
        """theta = P(TARGET_FIRST) x 180 degrees, measured from the left."""
        self.assertEqual(self.stage12.needle_svg_angle(0.0), 180.0,
                         "P=0 points at the STOP_FIRST end")
        self.assertEqual(self.stage12.needle_svg_angle(0.5), 90.0)
        self.assertEqual(self.stage12.needle_svg_angle(1.0), 0.0,
                         "P=1 points at the TARGET_FIRST end")
        self.assertIn('x2="100" y2="38"', self.gauge(0.5),
                      "the vertical needle for P=0.5")
        length = self.stage12.RACE_GAUGE["needle"]
        for probability in (0.0, 0.25, 0.5, 0.75, 1.0):
            with self.subTest(probability=probability):
                svg = self.gauge(probability)
                # The needle ends on the arc above the centre, sweeping from the
                # STOP_FIRST end (x = 100 - 62) to the TARGET_FIRST end (+62).
                x2 = float(re.search(r'x2="([-\d.]+)"', svg).group(1))
                y2 = float(re.search(r'y2="([-\d.]+)"', svg).group(1))
                self.assertAlmostEqual(
                    math.hypot(x2 - 100.0, y2 - 100.0), length, delta=0.02,
                    msg="the needle must keep the blueprint's length "
                        "(coordinates are rounded to 2dp)")
                self.assertLessEqual(y2, 100.0 + 0.01, "the needle stays on the arc")

    def test_an_out_of_range_probability_is_clipped_not_drawn_past_the_arc(self):
        for probability, expected in ((1.4, 0.0), (-0.2, 180.0)):
            with self.subTest(probability=probability):
                self.assertEqual(self.stage12.needle_svg_angle(probability), expected)

    def test_the_gauge_text_claims_no_number_of_its_own(self):
        seen = report_diff.visible_text(self.gauge(0.62)).strip()
        self.assertNotRegex(seen, r"\d",
                            "the gauge's visible text must state no figure — every "
                            "number is the artifact's, in the card's rows")

    def test_a_card_without_a_probability_draws_no_gauge(self):
        self.assertEqual(self.gauge(None), "")
        html = self.stage12.render(synthetic_trade(), {
            "out_4_ml.json": {"direction_label": "NEITHER",
                              "direction_probability": None, "waived": True}})
        head = html.index("<h2>Target-vs-Stop Race Gauge")
        self.assertNotIn('aria-label="Target-vs-Stop race gauge"', html[head:],
                         "a gauge without a needle is a picture of a measurement "
                         "nobody took")
        self.assertIn("NEITHER", html, "the label still renders without a gauge")

    def test_the_report_draws_the_gauge_inside_the_stage_4_card(self):
        html = self.stage12.render(synthetic_trade(), {
            "out_4_ml.json": race_payload("TARGET_FIRST", 0.62)})
        head = html.index("<h2>Target-vs-Stop Race Gauge")
        self.assertIn("<svg", html[head:head + 2000],
                      "the gauge is the card's visual, not a separate section")
        self.assertEqual(directional_probability_problems(html), [])


# ---------------------------------------------------------------------------
# The header badge is stage 9's own verdict (BRD §13.2, TICK-010 §3)
# ---------------------------------------------------------------------------

RECORDED_DECISION = (REPO_ROOT / "tests" / "proof" / "fixtures" / "GB-2022-0001"
                     / "recorded" / "out_9_decision.json")
RECORDED_CANONICAL = (REPO_ROOT / "tests" / "proof" / "fixtures" / "GB-2022-0001"
                      / "current_trade.json")


def decision_payload(**overrides) -> dict:
    """The stage-9 keys a badge may read: the artifact's own, nothing else."""
    doc = {"symbol": "INFY", "final_score": 64.0, "max_possible_score": 85.0,
           "effective_score_pct": 75.29, "decision": "GO", "verdict": "GO",
           "p_win": 0.5709, "expectancy": 0.4178,
           "circuit_breakers_tripped": [], "what_would_flip_me": None,
           "avoid_reason": None, "confidence_deficit": None}
    doc.update(overrides)
    return doc


class TestTheVerdictBadgeIsStage9Evidence(unittest.TestCase):
    """BRD §13.2's header: the canonical badge with score, P(win), expectancy.

    Stage 9 is the sole owner of the verdict (BRD §10, §9.1 row 9), so the badge
    is a *transcription* of `out_9_decision.json`: every figure it shows is the
    artifact's own value through `fmt`, and a report may not draw a verdict the
    engine never emitted. The pending branch stays honest when the payload is
    absent — a badge the engine did not produce would be the defect.
    """

    @classmethod
    def setUpClass(cls):
        cls.stage12 = load_stage(STAGE12, "stage12_badge_seam")

    def header(self, payloads: dict) -> str:
        html = self.stage12.render(synthetic_trade(), payloads)
        return html.split("</header>")[0]

    def test_every_pre_entry_verdict_renders_its_class_and_its_three_figures(self):
        for verdict in ("GO", "GO_WEAK", "WAIT", "AVOID"):
            with self.subTest(verdict=verdict):
                doc = decision_payload(verdict=verdict, decision=verdict)
                head = self.header({"out_9_decision.json": doc})
                cls = self.stage12.VERDICT_CLASS[verdict]
                self.assertIn(f'<span class="badge {cls}">{verdict}</span>', head)
                text = report_diff.visible_text(head)
                self.assertIn(f"{self.stage12.fmt(doc['final_score'])}/"
                              f"{self.stage12.fmt(doc['max_possible_score'])}", text,
                              "the composite score is final/max as §13.2 draws it")
                self.assertIn(self.stage12.fmt(doc["effective_score_pct"]), text)
                self.assertIn(self.stage12.fmt(doc["p_win"]), text)
                self.assertIn(self.stage12.fmt(doc["expectancy"]), text)

    def test_the_badge_reads_no_key_the_artifact_does_not_carry(self):
        """A score is shown; a denominator the artifact lacks is never invented."""
        doc = decision_payload(final_score=42.0)
        del doc["max_possible_score"]
        text = report_diff.visible_text(self.header({"out_9_decision.json": doc}))
        self.assertIn("42", text, "the score the artifact carries must still show")
        self.assertNotIn("42/", text,
                         "an empty or fabricated denominator is a claim the "
                         "artifact never made")

    def test_a_report_without_a_decision_payload_keeps_the_pending_badge(self):
        """Only stage 9's payload can light the badge; others must not."""
        head = self.header({
            "out_1_math.json": {"quantity": 1, "risk_inr": 37.5,
                                "reward_inr": 60.0, "raw_rr": 1.6,
                                "live_rr": 1.6, "inverted_rr_flag": False},
            "out_4_ml.json": race_payload("TARGET_FIRST", 0.62),
        })
        self.assertIn("verdict pending stage-9 evidence", head)
        self.assertNotIn('class="badge v-go"', head)

    def test_the_recorded_decision_artifact_is_what_the_badge_shows(self):
        """The recorded artifact (evidence, not a mock) drives the real header."""
        self.assertTrue(RECORDED_DECISION.exists(),
                        f"no recorded stage-9 evidence: {RECORDED_DECISION} — "
                        "run tests/evals/record_stage9_evidence.py --apply")
        doc = json.loads(RECORDED_DECISION.read_text(encoding="utf-8"))
        cls = self.stage12.VERDICT_CLASS.get(doc["verdict"])
        self.assertIsNotNone(cls, f"stage 9 emitted a non-canonical verdict: {doc['verdict']}")
        # The record's own canonical trade — a synthetic one would put numbers
        # in the header that this record's JSON cannot back.
        canonical = json.loads(RECORDED_CANONICAL.read_text(encoding="utf-8"))
        head = self.stage12.render(
            canonical, {"out_9_decision.json": doc}).split("</header>")[0]
        self.assertIn(f'<span class="badge {cls}">{doc["verdict"]}</span>', head)
        self.assertEqual(directional_probability_problems(head), [],
                         "the badge may not state a retired directional probability")
        # Every figure in the header must be backed by the record's own JSON.
        materialized, _refs, _derives = run_evals.dataset_refs.materialize(
            json.loads(DATASET_FILE.read_text(encoding="utf-8")))
        record = next(t for t in materialized["trades"]
                      if t["trade_id"] == "GB-2022-0001")
        self.assertEqual(report_diff.diff(head, record), [],
                         "a badge figure must be the artifact's, or it hallucinates")


def brd_dashboard_mock(brd: str) -> str:
    """BRD §13.2's fenced dashboard mock-up — the spec stage 12 implements."""
    head = brd.find("### 13.2 Visual Components in the HTML Dashboard")
    if head < 0:
        return ""
    start = brd.find("```", head)
    end = brd.find("```", start + 3) if start >= 0 else -1
    return brd[start:end] if start >= 0 and end > start else ""


# ---------------------------------------------------------------------------
# The vocabulary guard, widened to every specification document
# ---------------------------------------------------------------------------
#
# A line of a specification is a violation when it either names the retired
# gauge (`Direction gauge` / `Direction probability`) or states a probability as
# a direction - a direction word and a probability expression on the same line
# (`P(Bullish)`, `Bullish: 78%`, `Bearish 8%`).
#
# The paired half is what keeps the guard precise enough to run over prose.
# `bullish` is ordinary market vocabulary - breadth, basis, financing demand,
# stage 2's `trend_state` enum, the `clean_bullish` curation class - and a frozen
# breaker definition may say "breadth < 30% bullish" and mean exactly that. What
# is not ordinary is the **retired event stated as a probability**, which is what
# decision D moved off stage 4.
SPEC_DOCUMENTS = [
    REPO_ROOT / "BRD.md",
    REPO_ROOT / "prompt.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "actionplan.md",
    REPO_ROOT / "README.md",
    REPO_ROOT / "README_tech.md",
    REPO_ROOT / "business_test_cases.md",
    REPO_ROOT / "docs" / "understanding.md",
    *sorted((REPO_ROOT / "docs" / "agents").glob("*.md")),
    *sorted((REPO_ROOT / "docs" / "tickets").glob("*.md")),
    # The skills are specifications too, and the one that draws this report is
    # the likeliest place for the retired gauge to come back: `html-visualizer`
    # prescribed a `Direction Speedometer` over `P(Bullish)` long after the
    # engine stopped answering that question. `.agents/skills/` is its mirror.
    *sorted((REPO_ROOT / "skills").glob("**/*.md")),
    *sorted((REPO_ROOT / ".agents" / "skills").glob("**/*.md")),
    # The one generated Markdown artifact the owner reads: gate 8's divergence
    # adjudication. In scope rather than excused, because a generator is still a
    # writer of specification prose - if `adjudicate_divergences.py` ever emitted
    # the retired event, the report would be where a reader met it first. Its
    # own test byte-compares the file against a fresh build, so a vocabulary fix
    # has to land in the generator, not in the report.
    REPO_ROOT / "reports" / "adjudication_go_divergences.md",
]
# Documents that are deliberately out of scope, each with the reason it is not a
# specification. This list is not a convenience: `test_every_tracked_document_is
# _either_scanned_or_excused` reads it against `git ls-files`, so a new document
# joins the guard by default and leaving one out has to be argued here.
DOCUMENTS_OUT_OF_SCOPE = {
    # Session notes and the learnings log: a *record* of decisions taken (this
    # change's own note names the retired vocabulary many times over), not
    # something anyone implements from.
    ".trail/": "session records, not specifications",
    # The generated context graph: written by `graft build`, never authored.
    "graft/": "generated by graft, never authored",
}


def tracked_markdown() -> list[str]:
    """Every tracked `.md`, repo-relative — the completeness seam's own input."""
    git = shutil.which("git")
    if not git:
        raise unittest.SkipTest("git executable not available")
    proc = subprocess.run([git, "ls-files", "*.md"], cwd=REPO_ROOT,
                          capture_output=True, check=False)
    if proc.returncode != 0:
        raise unittest.SkipTest("not a git work tree")
    return sorted(line.strip() for line in
                  proc.stdout.decode("utf-8", "replace").splitlines()
                  if line.strip())

RETIRED_NAME_RE = re.compile(r"direction (?:gauge|probability)", re.IGNORECASE)
# Both halves are *direction-first*: a value attributed to a direction reads
# `P(Bullish)`, `Bullish: 78%`, `Neutral 14%`. The measure-first form
# (`breadth < 30% bullish`, `basis > 7% reflects bullish demand`) is the market
# vocabulary this guard must leave alone, and it is why the percentage arm
# requires the word to come first.
STATES_AS_PROBABILITY_RE = re.compile(
    r"\bP\s*\(\s*(?:\\?text\{)?\s*(?:[Bb]ull|[Bb]ear|[Uu]p\b|[Dd]own\b)"
    r"|(?:[Bb]ullish|[Bb]earish|[Nn]eutral)\s*[:=]\s*\d+(?:\.\d+)?"
    r"|(?:[Bb]ullish|[Bb]earish|[Nn]eutral)\s*[:=]?\s*\d+(?:\.\d+)?\s*\\?%")

# Attested uses: a line that may carry the vocabulary because its meaning is not
# the retired event. Each entry is (document, an anchor on the very line, why).
# Nothing here is a blanket file-wide exemption, and every entry is proved to
# still exist by `test_every_attested_use_still_exists` - a stale exemption is a
# hole in the guard.
ATTESTED_USES = [
    ("docs/agents/evals.md", "confidently bullish",
     "records what the retired up/down model predicted while that event was the "
     "graded one - history, kept as history"),
]


def spec_vocabulary_problems(rel_path: str, text: str) -> list[str]:
    """Every line of a specification that states a probability as a direction."""
    attested = [(anchor, why) for path, anchor, why in ATTESTED_USES
                if path == rel_path]
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        retired_name = RETIRED_NAME_RE.search(line) is not None
        stated_as_probability = (DIRECTIONAL_WORD_RE.search(line) is not None
                                and STATES_AS_PROBABILITY_RE.search(line) is not None)
        if not (retired_name or stated_as_probability):
            continue
        if any(anchor in line for anchor, _why in attested):
            continue
        problems.append(f"{rel_path}:{number}: {line.strip()[:100]}")
    return problems


class TestNoSpecDocumentStatesAProbabilityAsADirection(unittest.TestCase):
    """Every specification document, not only the three that draw the report.

    The defect this closes was a *specification* one: decision D re-froze the
    engine's event and left the presentation documents - and evals.md's own
    description of what the dataset carries - speaking the retired event. A
    guard over one file would have moved the hole, not closed it.
    """

    def test_no_spec_document_states_a_probability_as_a_direction(self):
        problems: list[str] = []
        for path in SPEC_DOCUMENTS:
            rel = path.relative_to(REPO_ROOT).as_posix()
            problems += spec_vocabulary_problems(rel, path.read_text(encoding="utf-8"))
        self.assertEqual(problems, [],
                         "the retired vocabulary came back:\n  "
                         + "\n  ".join(problems))

    def test_every_tracked_document_is_either_scanned_or_excused(self):
        """The scan set is *total*: a new document cannot fall outside it.

        Widening this guard once already proved the point - the two
        `html-visualizer` skills were the last documents prescribing the retired
        gauge, and nothing would have noticed a third. A glob list silently
        misses whole directories (that is exactly how skills were missed), so
        the scope itself is read back against the tracked tree.
        """
        covered = {path.relative_to(REPO_ROOT).as_posix() for path in SPEC_DOCUMENTS}
        uncovered = [rel for rel in tracked_markdown()
                     if rel not in covered
                     and not any(rel.startswith(prefix)
                                 for prefix in DOCUMENTS_OUT_OF_SCOPE)]
        self.assertEqual(uncovered, [],
                         "tracked documents outside the vocabulary guard - add them "
                         "to SPEC_DOCUMENTS or excuse them in "
                         "DOCUMENTS_OUT_OF_SCOPE:\n  " + "\n  ".join(uncovered))

    def test_the_skills_that_draw_this_report_are_in_scope(self):
        """The report's own skill is the one that has to speak the race."""
        for rel in ("skills/html-visualizer/SKILL.md",
                    ".agents/skills/html-visualizer/SKILL.md"):
            with self.subTest(document=rel):
                text = (REPO_ROOT / rel).read_text(encoding="utf-8")
                self.assertEqual(spec_vocabulary_problems(rel, text), [])
                self.assertIn("Target-vs-Stop Race Gauge", text)

    def test_every_attested_use_still_exists(self):
        for rel, anchor, _why in ATTESTED_USES:
            with self.subTest(document=rel, anchor=anchor):
                text = (REPO_ROOT / rel).read_text(encoding="utf-8")
                self.assertIn(anchor, text,
                              f"stale exemption: {anchor!r} is gone from {rel}")

    def test_the_guard_is_not_vacuous(self):
        """A checker that flags nothing would pass the whole set by accident."""
        for line in ("| 1. DIRECTION GAUGE | Bullish: 78% | Bearish: 8% |",
                     "Calibrated $P(\\text{Bullish})$ over three classes",
                     "| Bullish 78% | Neutral 14% | Bearish 8% |"):
            with self.subTest(line=line):
                self.assertTrue(spec_vocabulary_problems("BRD.md", line), line)
        # Market vocabulary is not the retired event: a measure attributed to a
        # direction is how the catalog, the breaker and stage 2 always spoke.
        for line in ("NSE 500 breadth below 30% bullish is the panic arm",
                     "An annualized basis > 7% reflects bullish financing demand",
                     "| `trend_state` | enum | `BULLISH` / `BEARISH` / `MIXED` |"):
            with self.subTest(line=line):
                self.assertEqual(spec_vocabulary_problems("BRD.md", line), [])


class TestTheReportSpecsSpeakTheRaceToo(unittest.TestCase):
    """A stale spec is how the retired vocabulary comes back.

    Stage 12 renders what three documents say it should — BRD §13.2's dashboard
    mock-up, TICK-010's acceptance line, and the stage-4 contract whose fields
    the card reads (TICK-004 §2). If any still prescribes a directional
    probability, the next implementer builds one, so the vocabulary is graded at
    the spec as well as in the rendering.
    """

    def test_the_dashboard_mock_and_its_tickets_prescribe_a_race_gauge(self):
        mock = brd_dashboard_mock(BRD.read_text(encoding="utf-8"))
        self.assertTrue(mock.strip(), "BRD §13.2's dashboard mock-up is missing")
        self.assertEqual(directional_probability_problems(mock), [],
                         "the frozen dashboard mock-up still draws a "
                         "directional probability gauge")
        self.assertRegex(mock, r"RACE GAUGE",
                         "the mock-up must name the gauge for the race")
        for path in (TICK010, TICK004):
            with self.subTest(spec=path.name):
                self.assertEqual(
                    directional_probability_problems(path.read_text(encoding="utf-8")),
                    [], f"{path.name} still prescribes a direction gauge")


class TestStage12CLI(unittest.TestCase):
    def test_canonical_path_and_json_status_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trade = root / "current_trade.json"
            trade.write_text(json.dumps(synthetic_trade()), encoding="utf-8")
            out_dir = root / "out"
            args = ["--input", str(trade), "--out-dir", str(out_dir)]
            for payload in synthetic_payloads(root):
                args += ["--payload", str(payload)]
            proc = run_stage12(args)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            status = json.loads(proc.stdout.decode("utf-8"))
            artifact = out_dir / "report_INFY_20260105.html"
            self.assertTrue(artifact.exists(),
                            f"canonical artifact missing: {artifact}")
            self.assertEqual(Path(status["artifact"]).name, artifact.name)
            self.assertEqual(status["bytes"], artifact.stat().st_size)

    def test_output_is_deterministic_and_self_contained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trade = root / "current_trade.json"
            trade.write_text(json.dumps(synthetic_trade()), encoding="utf-8")
            args = ["--input", str(trade), "--out-dir", str(root / "a")]
            for payload in synthetic_payloads(root):
                args += ["--payload", str(payload)]
            self.assertEqual(run_stage12(args).returncode, 0)
            args_b = [a.replace(str(root / "a"), str(root / "b")) for a in args]
            self.assertEqual(run_stage12(args_b).returncode, 0)
            first = (root / "a" / "report_INFY_20260105.html").read_bytes()
            second = (root / "b" / "report_INFY_20260105.html").read_bytes()
            self.assertEqual(first, second, "same inputs must yield same bytes")
            text = first.decode("utf-8")
            self.assertTrue(text.startswith("<!doctype html>"))
            for external in ("http://", "https://", "<script", "<link"):
                self.assertNotIn(external, text,
                                 f"{external!r} makes the report not standalone")
            self.assertIn("<svg", text, "charts must be inline SVG")
            # The runner's own validity check must agree with these assertions.
            self.assertEqual(run_evals.html_validity_problems(text), [])

    def test_validity_check_catches_broken_reports(self):
        """The gate's checker must name what a broken report violates."""
        problems = run_evals.html_validity_problems(
            "<html><body>no doctype, no style, no chart</body></html>")
        self.assertTrue(any("doctype" in p for p in problems), problems)
        self.assertTrue(any("<style>" in p for p in problems), problems)
        self.assertTrue(any("<svg>" in p for p in problems), problems)
        external = run_evals.html_validity_problems(
            "<!doctype html><style>a{}</style><svg></svg>"
            '<img src="https://cdn.example.com/x.png">')
        self.assertTrue(any("external resource" in p for p in external), external)
        self.assertTrue(any("network URL" in p for p in external), external)
        # Self-contained references are legal.
        self.assertEqual(run_evals.html_validity_problems(
            "<!doctype html><style>a{}</style><svg></svg>"
            '<img src="data:image/png;base64,AA">'), [])

    def test_absent_payloads_render_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trade = root / "current_trade.json"
            trade.write_text(json.dumps(synthetic_trade()), encoding="utf-8")
            proc = run_stage12(["--input", str(trade), "--out-dir", str(root / "out")])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            text = (root / "out" / "report_INFY_20260105.html").read_text("utf-8")
            self.assertIn("awaiting stage-2 evidence", text)
            self.assertIn("awaiting stage-4 evidence", text)
            self.assertIn("verdict pending stage-9 evidence", text)

    def test_unusable_input_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty = root / "empty.json"
            empty.write_text("{}", encoding="utf-8")
            proc = run_stage12(["--input", str(empty), "--out-dir", str(root / "out")])
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("ERR_INPUT", proc.stderr.decode("utf-8", "replace"))


class TestTheTrackedPrototypeIsAFreshRender(unittest.TestCase):
    """`reports/sample_report.html` is a *produced* artifact, not a mock.

    The one report that is committed (`reports/report_*.html` is gitignored) is
    what a reviewer opens and what a fresh clone reads as an example, and it had
    drifted: it was a hand-written mock of the pre-decision-D dashboard — a
    direction card, a `Confidence Score` table, and no race gauge at all. It is
    now `report_regen.PROTOTYPE_TRADE_ID`'s own stage-12 output, and this seam is
    what keeps it that way: the tracked bytes must equal a fresh render of that
    record, byte for byte, or the committed prototype is a picture of a report
    nobody produced.
    """

    @classmethod
    def setUpClass(cls):
        cls.prototype = REPO_ROOT / "reports" / report_regen.PROTOTYPE_NAME
        if not cls.prototype.exists():
            raise AssertionError(
                f"the tracked prototype is missing: {cls.prototype} "
                "(regenerate with `python tests/evals/report_regen.py --prototype`)")
        cls.html = cls.prototype.read_text(encoding="utf-8")
        cls.job = report_regen.prototype_job()

    def test_the_tracked_bytes_are_exactly_what_stage_12_renders(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = report_regen.write_prototype(Path(tmp) / "prototype.html")
            self.assertEqual(fresh.read_bytes(), self.prototype.read_bytes(),
                             "the committed prototype is not a fresh render of "
                             f"{report_regen.PROTOTYPE_TRADE_ID}: regenerate it with "
                             "`python tests/evals/report_regen.py --prototype`")

    def test_the_prototype_is_one_named_records_own_report(self):
        """It is a record's report — not a bespoke document with its own facts."""
        self.assertEqual(str(self.job["trade"]["trade_id"]),
                         report_regen.PROTOTYPE_TRADE_ID)
        canonical = json.loads(self.job["canonical"].read_text(encoding="utf-8"))
        self.assertEqual(self.prototype.name, report_regen.PROTOTYPE_NAME)
        # The record and the trade it was rendered from are one identity, so the
        # prototype is that record's report under a fixed name (`report_regen`
        # refuses the job otherwise, and this is the same rule, asserted).
        self.assertEqual(run_evals.report_path_for(self.job["trade"]).name,
                         f"report_{canonical['symbol']}_"
                         f"{canonical['recommended_date'].replace('-', '')}.html")
        self.assertIn(canonical["symbol"], self.html)
        self.assertIn(str(canonical["recommended_date"]), self.html)

    def test_the_prototype_draws_the_frozen_dashboard_and_its_gates_pass(self):
        self.assertIn("Target-vs-Stop Race Gauge", self.html,
                      "the committed prototype must show §13.2's gauge")
        self.assertIn('aria-label="Target-vs-Stop race gauge"', self.html)
        self.assertEqual(directional_probability_problems(self.html), [],
                         "the prototype itself may not state a retired probability")
        self.assertEqual(run_evals.html_validity_problems(self.html), [])
        materialized, _refs, _derives = run_evals.dataset_refs.materialize(
            json.loads(DATASET_FILE.read_text(encoding="utf-8")))
        record = next(t for t in materialized["trades"]
                      if str(t["trade_id"]) == report_regen.PROTOTYPE_TRADE_ID)
        self.assertEqual(report_diff.diff(self.html, record), [],
                         "the committed prototype must be differ-clean against "
                         "its own record, exactly like the generated reports")


class TestEveryRecordGetsAGradedReport(unittest.TestCase):
    """The acceptance property: 31 records, 31 clean reports, gate at 100.0%."""

    @classmethod
    def setUpClass(cls):
        if not STAGE12.exists():
            raise AssertionError(f"stage script missing: {STAGE12}")
        cls.data = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        cls.jobs = report_regen.report_jobs(cls.data, manifest)

    def test_every_record_regenerates_and_the_gate_grades_it_clean(self):
        self.assertEqual(len(self.jobs), len(self.data["trades"]),
                         "every golden record must have a regeneration job")
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            artifacts, failures = report_regen.regenerate(self.jobs, out_dir)
            self.assertEqual(failures, [], "; ".join(failures))
            self.assertEqual(len(artifacts), len(self.jobs))
            self.assertEqual(report_regen.diff_reports(self.data, out_dir), [])
            materialized, _r, _d = run_evals.dataset_refs.materialize(self.data)
            gate = run_evals.gate_hallucination(materialized["trades"],
                                                reports_dir=out_dir)
            self.assertFalse(gate.skipped, gate.detail)
            self.assertEqual(gate.value, 100.0, gate.detail)
            status, _ = gate.evaluate()
            self.assertEqual(status, "[PASS]")
            # The same artifacts must also satisfy the HTML-validity gate.
            validity = run_evals.gate_html_validity(materialized["trades"],
                                                    reports_dir=out_dir)
            self.assertFalse(validity.skipped, validity.detail)
            self.assertEqual(validity.value, 100.0, validity.detail)
            v_status, _ = validity.evaluate()
            self.assertEqual(v_status, "[PASS]")


if __name__ == "__main__":
    unittest.main()
