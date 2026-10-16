# 2026-10-15 — Label windows, a second index source, and sector-faithful cone pools

Three owner prompts in one pass: (1) restrict forward labels to within-vintage
sessions, (2) rebuild the cone's excursion pools per-sector or with crash-aware
tails so the envelope can contain the −43% and V-rebound tapes, (3) find a
tier-1 source for the six sector indices Yahoo does not serve. All three touch
the same artifact, so they were ordered A → C → B and retrained once at the end.

## What was decided

**A — a label window is one vintage deep (ADR-011 decision 11).** The vintages
are *not* consecutive: SBIN's and RELIANCE's run `202003` and then `202204`, so
the merged series steps 2020-03-31 → 2021-05-03. A 22-session window read across
that step is fourteen months of real market time labeled as one month — and
those rows carried the *deepest* adverse atoms in the pools, i.e. the ones the
crash tapes' envelopes were leaning on. The rule: one committed vintage must
contain the row's own session **and** every session after it
(`label_window_dates`). Cost: 44 rows (the `202003` tails of RELIANCE and SBIN),
disclosed in `provenance.corpus.label_window` and printed by the trainer.
`build_rows(..., vintages=...)` defaults to "one implicit vintage" so in-memory
seams keep working; `load_stock_corpus` now also returns per-vintage date lists.

**C — the panel has a second tier-1 source (decision 12).** Yahoo serves long
history for `^NSEI`, `^INDIAVIX`, `^NSEBANK`, `^CNXIT`, `^CNXPHARMA` and a
*single session* for the other six (no `^CNXBANK` symbol at all). NSE's own
daily index snapshot (`ind_close_all_DDMMYYYY.csv`, one file per trading day,
Open/High/Low/Close for every Nifty sector index) carries all six back through
the corpus — free, official, and the tier AGENTS.md §5 already prefers. Panel:
**12 served / 0 absent** (was 5/7), window 2017-11-30 – 2026-10-15, archive
carrying 2,189 of 2,315 weekdays. The fallback is asked **only** about what
Yahoo missed; each series records its source (`tickers.<ticker>.source`).

The load-bearing distinction: **404 is a fact about the source; a timeout is a
fact about us.** Only 404 on both hosts becomes `no_file`; anything else raises
`ArchiveUnreachable` naming the day, is never cached, and fails the build — so a
blip can neither shorten a series nor be reported as the source's limit. The
per-day cache lives at `data_cache/nse_index/` (already gitignored) and makes a
2,315-request run resumable; the whole fetch is a one-off offline step (~2 min
at 8 workers) and is not part of the suite.

Effect: universe 42 symbols → 17 buildable (9 sector blocks), rows 6,294 →
**11,267**. RELIANCE (energy), LT and ADANIPORTS (infra) — three of the six
benchmark rows' own sectors — joined training for the first time.

**B — the cone's pools are the trade's own sector's (decision 13).** Levels are
quantiles of *observed* excursions, so the block decides the envelope. The
calibration ships `samples_by_sector` beside the pooled block; stage 4's
`samples_for` picks the sector's when every class of every measure holds ≥
`MIN_SECTOR_ATOMS_PER_CLASS` (30) atoms, else the pooled block (stderr note; the
8-key payload does not grow). The CV measures the shipped cone through that same
selector. `provenance.corpus.sector_blocks` discloses atoms and tails
(`mae_pct.p01/p10`, `mfe_pct.p90/p99`) per block.

Re-measured: 11,267 rows, 99.73% of out-of-fold rows on their own block,
containment **76.51%** bar path (pooled 77.32%) / 82.41% close / 89.30%
terminal; Brier 0.263231 (base 0.244927). Retrained → stage-4 evidence
re-recorded (6 rows) → gates: Brier **0.533**, coverage **0.0%** (6/6 miss,
no ungraded), suite 578/578, runner exit 1.

## What was ruled out

* **Not a threshold move.** Coverage 80% and Brier 0.18 are untouched. Gate 5
  still fails and the numbers are published as they fall.
* **Crash-aware tails that actually contain the tapes.** Measured on the very
  blocks the cones read: `SCEN-008`'s −43.48% low is the **1.83rd** percentile
  of the 1,037 infra excursions, `SCEN-004`'s −17.57% the **4.53rd**,
  `SCEN-003`'s −17.05% the **1.97th** and `SCEN-006`/`SCEN-002` the 12.19th /
  13.43rd of the bank block, while `SCEN-001`'s +63.17% rebound is the
  **99.91st** of the energy block's favourable atoms. Containing all six at a
  10%-per-tail envelope needs ≥10% of the mass beyond −43.48%: the infra block's
  own `p01` is −49.80%, but the *pooled* block's `p01` is −20.47% — still short
  of `SCEN-008`. So the six engine-graded rows are a deliberately adversarial
  ~2–13% tail sample of the corpus's own distribution, not a pool defect. The
  lever left is the benchmark's composition or the threshold: both owner calls.
* **Filling the panel from the archive by default.** Only never-served tickers
  are asked for, so the fallback cannot silently become the primary.
* **Not touching the gate's scope or text.** The scope rule (decision 9/10) is
  unchanged; the new per-row numbers were measured against the committed
  artifact + the dataset's derived tape extremes and published in evals.md §2.

## Watch out for

* `test_the_artifact_still_matches_the_corpus_it_names` and
  `TestTheCorpusReachesTheGradedTapes` fail whenever the corpus grows without a
  retrain — that is the intended tripwire; run
  `tests/evals/train_direction_model.py` after any panel or vintage change.
* Re-recording stage-4 evidence rewrites `manifest.sha256`/`baseline_scorecard`;
  the recorded payloads only change when cones or probabilities change, so a
  provenance-only calibration edit needs no re-record (models/ is not in the
  proof manifest).
* NSE's archive has real holes (~5% of weekdays 404). A retro-fitting vintage
  lands on those dates without a panel session there → those rows are skipped by
  the existing `day not in market or day not in sector` rule, which is disclosed
  through the corpus's per-series session counts, not a bug.
* `^CNXBANK` in the shared sector map is the **PSU-bank** slot; the archive's
  official name for it is `Nifty PSU Bank` (Yahoo's `^CNXPSUBANK`). The map is
  pinned domain data and was not renamed.
* The `--cv-only` path of the trainer returns before writing anything: keep the
  drop-summary prints inside that branch, or the run silently exits 0 without a
  model (this happened once during this change and was caught by the missing
  stdout lines).
