# 2026-10-16 — Stage 12's race vocabulary, and the Phase 0 probe that is still blocked

## What was asked

1. Run the Tier-2 **Phase 0 preflight probe** against the real Dhan API and commit
   the retention/depth-shape disclosure.
2. **Audit stage 12's HTML rendering** against decision D's race classes
   (`TARGET_FIRST` / `STOP_FIRST` / `NEITHER`) so no report still labels
   probabilities as bullish/bearish.

## 1. Phase 0 — the probe exists; the *run* is blocked (no fabrication)

* There is **no `.env`** in this workspace and **no `DHAN_CLIENT_ID` /
  `DHAN_ACCESS_TOKEN`** in the environment — only the `.env.example` landed with
  Phase 1. Credentials are the owner's brokerage account; the agent cannot mint
  them, so the real probe could not be executed this session.
* Landed instead, per plan §4's own text: `tests/evals/dhan_preflight.py` — a
  **disclosure-only** probe (writes nothing, never prints the token) that derives
  its vintages from `scenario_manifest.json` (earliest session of each year, the
  pre-2021 rows as the control), probes the URLs **stage 8 uses** through stage
  8's own `_candles_to_bars` and `spread_from_quote`, throttles itself to the
  documented limits (≤ 5 req/s data, ≤ 1 req/s quote), and grades failures:
  a vintage the broker does not serve is a *result* (HTTP status printed); a 200
  whose body the client cannot read is `ERR_UNEXPECTED_SHAPE` (exit 3), loud and
  still disclosed on stdout.
* The run was attempted with the real transport and **failed closed**:
  `ERR_NO_CREDENTIALS`, exit 2, nothing attempted, nothing written. That refusal
  is the honest end state — a mock can pin a *shape*, never a *reachability*
  finding, so no disclosure was committed to fill the acceptance criterion.
* Seam: `tests/test_dhan_preflight.py` (9 tests, all offline over an injected
  fetcher): refusal names both env vars and writes nothing, the boundary is
  measured (reachable 2022 + unreachable 2019 control), an unreadable 200 stops
  the run at exit 3 while still printing the disclosure, depth is read through
  stage 8's parser, vintages come from the manifest, and the rate floors are
  enforced by the probe.

**What closes it later (operator, one command):**

```
export DHAN_CLIENT_ID=... DHAN_ACCESS_TOKEN=...
python tests/evals/dhan_preflight.py --security-id <id> > preflight.json
```

## 2. Stage 12 — the audit found a real defect, in the *specs* as well as the code

* **No rendered report labelled a probability bullish/bearish** — but only by
  accident of what is built: `scripts/12_generate_html_report.py` titled stage 4's
  card **"Direction Gauge"**, BRD §13.2's frozen dashboard mock-up still drew
  `Bullish: 78% / Neutral: 14% / Bearish: 8%`, TICK-010 still asked for a
  "Direction gauge", and TICK-004's contract table still declared
  `direction_label`: `STRONG_BULLISH` / `BULLISH` / `NEUTRAL` / `BEARISH` with
  `direction_probability` = `P(BULL) + φ·P(NEUTRAL)` — **while the same ticket's
  outcome section and BRD §5.2 said the event is the target-versus-stop race**.
  Decision D reached the engine's spec and not the presentation's, so the next
  implementer of §13.2's gauge would have drawn `P(up)` under a bullish label.
* Fixed: the renderer states the race (`Target-vs-Stop Race Gauge & 22-Day
  Volatility Price Cone`, a caption, and `RACE_FIELD_LABELS` prose) with the
  payload's values untouched (`fmt`-exact, so the Zero-Hallucination gate still
  grades those bytes); the specs were re-frozen to match (BRD §1.1/§4/§13.2/§15's
  example, TICK-010, TICK-004, actionplan's stage-4 checklist and Stage-12
  blueprint — including the speedometer blueprint that keyed its needle on
  `P(Bullish)`).
* Seam (`tests/test_12_generate_html_report.py`): every race class renders
  verbatim; `directional_probability_problems` fails on `bullish`/`bearish` or a
  revived `Direction gauge`/`Direction probability` label **outside stage 2's
  licensed `trend_state` row** (an EMA-stack trend classification the payload
  carries, not a probability — the carve-out is asserted non-vacuous); two tests
  grade the *specs* (BRD §13.2's block, TICK-010, TICK-004), and both failed red
  before the re-freeze.
* Verified on the real artifacts: 58/58 regenerated reports carry the race
  gauge, 27 render a race label (`TARGET_FIRST` 3 / `STRONG_TARGET_FIRST` 15 /
  `STOP_FIRST` 9), **zero** directional probabilities outside stage 2's trend row
  (41 inside it, all `trend_state` values). Zero-Hallucination 100%, HTML validity
  100%, `--regression-exit` 0/0, full suite 668 green.

## Ruled out (and why)

* **Running the probe with a mock or a synthetic "disclosure".** A mock pins a
  shape; §2's host/path/body gap rows and the retention boundary need a *real*
  response. Committing a fabricated disclosure would be the exact defect the
  campaign exists to prevent.
* **Renaming the artifact keys** (`direction_label`, `direction_probability`).
  They are frozen (BRD §9 inventory, TICK-004 §2) and renaming them changes the
  stage contract and every recorded `out_4_ml.json`. The *rendered prose* was in
  scope; the keys were documented as "the field name outlived the event it was
  named for" and left alone.
* **Also scanning actionplan/evals.md with the new guard** — *reversed in §3,* and
  worth keeping the reversal visible. The first pass scoped the guard to the three
  documents that specify *what the report draws*, because legitimate retirement
  records live in the others; §3's widening replaced that with anchored,
  reason-carrying exemptions, so the guard covers them too and history is exempt
  line-by-line instead of by omission.

## 3. The gauge landed, and the guard widened to every spec document

* **`race_svg` draws BRD §13.2's radial speedometer** from the artifact's own
  `direction_probability`: arc radius 80 in `viewBox "0 0 200 120"`, sliced
  `STOP_FIRST` (crimson) / `NEITHER` (amber) / `TARGET_FIRST` (emerald), needle at
  `P(TARGET_FIRST) × 180°` measured from the left — §13.2's own sentence, so the
  retired `P(Bullish)` input cannot return through the geometry. Verified
  visually (`reports/report_HDFCBANK_20220504.html`: 0.6924 ⇒ needle at 124.6°,
  inside the TARGET_FIRST slice, matching its `STRONG_TARGET_FIRST` row).
* Two properties the seam pins, both about honesty rather than looks:
  **geometry lives in attributes and the gauge's *visible text* carries no
  digits** (so the drawing can never claim a figure the record lacks — proved by
  running `report_diff.visible_text` over the SVG), and **a payload with no
  probability draws no gauge at all** (a needle-less dial is a picture of a
  measurement nobody took). Out-of-range values are clipped to the arc.
* **The guard now scans `BRD.md`, `prompt.md`, `AGENTS.md`, `actionplan.md` and
  `docs/**.md`** (`.trail/learnings/` is deliberately out of scope: notes record
  decisions, nobody implements from them). A line is a violation if it names the
  retired gauge *or* states a **direction-first** probability; the
  **measure-first** form (`breadth < 30% bullish`, stage 2's trend enum, the
  `clean_bullish` class) is left alone. Widening it found two live stale specs
  nothing else would have caught: `prompt.md`'s own "Three Core Predictions"
  asked the agent for the three up/down class probabilities, and evals.md twice
  called stage 4's field by the retired directional name.
* One exemption is left, declared line-by-line in `ATTESTED_USES` with a reason
  (evals.md's historical note about the retired model's own predictions), and
  `test_every_attested_use_still_exists` fails if that anchor ever disappears —
  a stale exemption is a hole, so it is graded too.
* **Then the scope check found the last two stale documents, and they were the
  worst place for them:** `skills/html-visualizer/SKILL.md` and its
  `.agents/skills/` mirror — the skill that *generates this report* — still
  prescribed the retired speedometer over the three up/down class probabilities.
  Both now state §13.2's race gauge. A glob list over `docs/` could never have
  found them, which is the lesson: **scope by enumeration, not by glob.**
  `test_every_tracked_document_is_either_scanned_or_excused` now reads
  `git ls-files '*.md'` back against the scan set, so a document joins the guard
  by default and leaving one out requires an entry (with a reason) in
  `DOCUMENTS_OUT_OF_SCOPE` — today only `.trail/` (session records) and `graft/`
  (generated). The scan set went 32 → 79 documents.

## 4. The tracked prototype was the last stale artifact — it is generated now

* `reports/sample_report.html` is the one *committed* report (everything else
  under `reports/` is gitignored), so it is what a reviewer opens and what a
  fresh clone reads as an example. It was a hand-written mock of the
  pre-decision-D dashboard: a direction card, a `Confidence Score` table, and no
  race gauge at all — the frozen §13.2 dashboard was absent from the very
  document everyone looks at. `test_report_diff.py` pinned it by *literals*
  (`₹2,974.00` / `₹2,988.20`) that no record ever carried.
* It is now `report_regen.PROTOTYPE_TRADE_ID` = `GB-2022-0001`'s own stage-12
  report: the benchmark's first record, a `GO` / `clean_bullish` trade whose
  stage-4 evidence means the prototype actually shows the race gauge (a
  hazard-class record would have shown an AVOID card and no gauge).
  `report_regen.py --prototype` renders it, `TestTheTrackedPrototypeIsAFreshRender`
  fails the moment the committed bytes stop being a byte-exact fresh render, and
  the differ/validity gates grade it like any other artifact.
* The pins moved with it: `test_report_diff.py` now reads the numbers out of the
  record and formats them with stage 12's own `fmt`, so a future renderer change
  cannot leave a typed-in literal behind. **Lesson: a hand-typed number in a test
  is a mock that has not drifted yet.**
* **Instrument identity is not one field.** 31 of 58 records say `instrument:
  FUT` while their canonical fixture says `instrument_type: CASH` — by design,
  disclosed as `authoring.legacy_instrument` by the legacy-pinning campaign, not
  a bug. Only `symbol` + `recommended_date` are checked as job identity, so the
  prototype's title honestly reads `RELIANCE CASH`; a reviewer who knows the
  record says `FUT` will ask, which is why the ticket now states the disclosure.
* **Prototype vs record choice is not cosmetic.** 27 of 58 records agree with
  their fixture's instrument (25 `FUT`/`FUT`, 2 `CASH`/`CASH`), and **all 25
  `FUT`/`FUT` records are hazard scenarios whose expected verdict is `AVOID`** —
  a prototype taken from one of those would have taught a reader the layout from
  an avoid card. The representative dashboard (a `GO`, all cards populated) comes
  from a legacy-pinned `CASH` record, which is the choice that was made.

## Watch out for

* **Windows newlines.** `BRD.md`/`actionplan.md`/`TICK-004` are CRLF; `pathlib
  .read_text()/write_text()` round-trips them through `os.linesep` while
  `read_bytes()` does not. Byte-level edits with an ending-uniformity assertion
  are the safe pattern (a `\n`-only rewrite of a CRLF file makes the whole file a
  diff).
* **f-strings cannot contain a backslash before Python 3.12** — a nested
  conditional f-string in the renderer broke the module on import; build the
  fragment in a local variable instead.
* **`reports/*.html` are gitignored generated bytes.** An existing artifact is
  never overwritten by `report_regen.py`: it replays into a sidecar and records a
  *failed* row when the bytes differ, so a renderer change needs
  `rm -f reports/report_*.html reports/run_record.json` first (the repo's own
  `git clean -Xdf reports` step) — otherwise the stale bytes stay and the next
  gate grades them.
* **`grep` on box-drawing characters dies on a cp1252 console.** Do width checks
  and assertions in Python, and only assert/print ASCII.
* **Never hand-write a backslash-bearing anchor.** A `\text{...}` anchor built in
  a heredoc or an edit payload can silently carry one backslash too many, so the
  match fails (or a replacement doubles every LaTeX escape in the file). Read the
  line out of the file and anchor on *that*, and settle ambiguity with
  `[ord(c) for c in fragment]` — repr survives too many escaping layers to trust.
* **A guard over the whole docs set needs a narrow predicate, not a strict word
  list.** "Any occurrence of bullish/bearish" is false-positive noise (breadth,
  basis, trend enums, curation class names); "a direction word carrying a
  probability *and* the word coming first" is the retired event and almost
  nothing else.
* **An audit record needs to name what it removed — and the guard will flag its
  own author.** Three times this session a spec or ticket describing the
  retirement tripped the guard (TICK-010 twice, actionplan once). Reword the
  record to describe the retired form rather than quote it, and keep exactly one
  exemption mechanism (anchored, with a reason, staleness-tested) instead of
  accumulating escape hatches.
