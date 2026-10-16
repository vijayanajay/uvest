# 2026-10-14 — Thin-series joins, a sourced booster, and engine gates that finally grade

Two asks landed in one change: stop the market-context fetcher's inner join from
collapsing the whole bundle when one sector series is thin, and build a
replaceable pipeline that trains stage 4's booster from the sourced NSE history
corpus with purged group time-series CV — so eval dimensions 4 and 5 can finally
be graded.

## 1. The fetcher's join was destroying evidence (fixed, live-proved)

`align_axes` intersected every index series with all the others, so a trade whose
sector index came back as a single Yahoo session took `^NSEI`'s trend and
`^INDIAVIX`'s panic evidence down with it. Live probe before the fix: `^CNXENERGY`
1 session, `^NSEI`/`^INDIAVIX` cut to 1. After: `^NSEI` and `^INDIAVIX` keep all
**319** sessions and `^CNXENERGY` is omitted with a "too thin to join" line in
`provenance.sources`.

- The rule is a floor, not a heuristic: a member with fewer than
  `MIN_JOIN_SESSIONS` (21 = one trading month, the shortest *joint* window any
  reader aligns) is omitted and disclosed; survivors are still intersected, so no
  consumer sees a date another series lacked.
- Watch out: `align_axes` now returns `(aligned, omitted)` — a tuple, not a dict.
- The seam is in `tests/test_fetch_market_context.py` (pure join, assembly-level
  bundle, and the real stage-7 CLI waiving on *only* the lost series).

## 2. The sourced booster (ADR-011)

The corpus already existed on the stock side (173 real yfinance vintages); the
*market* side did not, and stage 4's two cross-asset features need it. So:

- `tests/evals/build_index_corpus.py --fetch` pulls the panel once through
  `fetch_market_context.fetch_yahoo_closes` (one fetch implementation, note) and
  commits `tests/fixtures/history/indices_daily.json`: **5 series served**
  (`^NSEI`, `^INDIAVIX`, `^NSEBANK`, `^CNXIT`, `^CNXPHARMA`), **7 disclosed
  absent** — Yahoo serves one session for `^CNXAUTO`/`^CNXFMCG`/`^CNXMETAL`/
  `^CNXINFRA`/`^CNXENERGY`/`^CNXREALTY` and nothing at all for `^CNXBANK`.
  Consequence, disclosed in the artifact: the trained universe is 8 F&O symbols
  across BANK/IT/PHARMA only, and the rest are named per symbol.
- `tests/evals/train_direction_model.py` builds 6,261 point-in-time rows and
  trains `models/lgbm_direction_v1.*` with `origin: sourced`. It imports stage
  4's own `feature_vector` and cone primitives, so training and inference cannot
  drift. Determinism is asserted (two runs → identical bytes).
- **The purge rule is the subtle part.** Purging against the sessions a symbol
  happens to have *inside* a fold looks right and leaks: a symbol halted (or
  missing) on the fold's dates has no session there, so the row directly before
  the fold survives — and its 22-session label window covers the whole fold.
  Purge against the fold's **date span on the symbol's own calendar** instead
  (`before`/`through` rank counts). The named test is
  `test_a_symbol_that_did_not_trade_in_the_fold_is_still_purged`.
- Rows are skipped when the index has no session on the row's own date: a stale
  market window stamped on a row blurs the timestamp the label refers to.

## 3. Engine gates grade now — and fail (deliberate, measured)

`tests/evals/record_stage4_evidence.py` records `out_4_ml.json` on the 6 proof
rows that own a market-context bundle (56 rows walked and disclosed as unable to
run stage 4), declares the artifact, `$ref`s it and reads the claims out of the
recorded bytes. Stage 4 joined `test_proof_manifest.RERUNNABLE` (V10 byte-compare).

Graded result, published as-is: **Brier 0.636** (≤0.18) and **cone coverage 0.0%**
(≥80%). The causes are measured, not mysterious:

- **No 22-session directional skill.** OOF over all 6,261 corpus rows: Brier
  0.2653 vs a no-skill base rate 0.2447; accuracy 39.1% vs a 37.7% majority
  class. The six graded tapes are the 2020–2024 crisis/hazard rows while the
  pools cover 2021–2026, so the model is confidently bullish into five down-tapes.
- **The cone answers a different question than gate 5.** The shipped cone is the
  mixture's *terminal-return* interval; the gate asks whether the *path* stayed
  inside it. Corpus measurement: 72.3% terminal containment vs 49.5% path. The
  fix is engine-side (build the envelope from the `mae_pct`/`mfe_pct` mixtures
  already in the artifact), not a threshold edit.

Do not "fix" this by widening thresholds or re-selecting rows: the gates are
doing exactly what they were built for. The open decisions are the cone's
definition and BRD §5.2's target/threshold pair — both the owner's.

## 4. Traps for the next session

- `models/lgbm_direction_v1.txt` is now ~1 MB (200 rounds × 15 leaves) and is
  written as **bytes**; a text-mode write turns `\n` into `\r\n` and LightGBM
  refuses the file ("expect a tree here"). `.gitattributes` pins it to LF.
- `tests/evals/build_direction_model.py` is **gone** (retired with the stand-in).
  Rebuild artifacts with `python tests/evals/train_direction_model.py`.
- The trainer's calibration must keep the *nested* `samples` shape
  (`measure → class → values`); stage 4 reads `samples["forward_return_pct"]`.
  A flat per-class pool (what the CV loop uses) makes `compute()` raise
  `KeyError` — that cost one debugging round.
- Any new script must reconfigure stdout/stderr to UTF-8 (repo convention);
  without it, a `·` in output breaks subprocess capture under the Windows
  codepage (two tools hit this).
- The suite is 527 tests and green; `tests/run_evals.py` exits 1 **by design**
  now (two graded gates under threshold), and the run_evals seams assert verdict
  consistency rather than "no `[FAIL]` lines".
