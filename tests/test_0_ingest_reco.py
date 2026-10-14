"""TICK-001 seam — scripts/0_ingest_reco.py

Stage 0 owns the canonical schema and three hard structural gates, per
BRD §3.1–3.3 and TICK-001. Every acceptance criterion here is a frozen
criterion in docs/tickets/TICK-001-canonical-parser.md §3.

Runtime strategy (tests/support/pipeline.py): the behavior contract runs
in-process via import (microseconds), and ONE subprocess test guards the
frozen CLI contract so exit codes / stderr routing / stdout-JSON purity can
never silently break.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from support.pipeline import (  # noqa: E402
    import_mode, run_stage0_text, subprocess_mode, temp_input,
)
# NOTE: import_mode imported but unused today; kept as the documented entry
# point the next stage seam copies. Linter-safe via explicit re-export below.

REPO_ROOT = TESTS_DIR.parent
SCRIPT = REPO_ROOT / "scripts" / "0_ingest_reco.py"

# Both carry `Recommended Date:` because every real Univest card does, and
# because stage 12 names its artifact from it (BRD §13, TICK-010): a paste
# without it can no longer be admitted by stage 0. See TestRecommendedDate.
VALID_FUTURES = "Symbol: RELIANCE\nType: FUT\nEntry Range: 2975.00-2985.00\nLive Price: 2988.20\nStoploss: 2910.00\nTarget: 3120.00\nLot Size: 250\nRecommended Date: 2026-10-09\n"
VALID_CASH = "Symbol: SBIN\nType: CASH\nEntry Range: 820.00-825.00\nLive Price: 822.10\nStoploss: 795.00\nTarget: 890.00\nRecommended Date: 2026-10-09\n"


class TestOptionsGate(unittest.TestCase):
    """ADR-001 hard rule: options die at ingestion, with the frozen error code."""

    def test_ce_is_rejected_with_frozen_error_and_exit_2(self):
        payload, code, err = run_stage0_text(VALID_FUTURES.replace("Type: FUT", "Type: CE"))
        self.assertEqual(code, 2, f"expected exit 2, got {code}; stderr={err}")
        self.assertIn("ERR_OPTIONS_PROHIBITED", err)
        self.assertIsNone(payload, "rejected run must not emit a canonical payload")

    def test_pe_and_opt_variants_rejected_too(self):
        for variant in ("PE", "OPT"):
            with self.subTest(variant=variant):
                _, code, err = run_stage0_text(VALID_FUTURES.replace("Type: FUT", f"Type: {variant}"))
                self.assertEqual(code, 2)
                self.assertIn("ERR_OPTIONS_PROHIBITED", err)


class TestCanonicalSchema(unittest.TestCase):
    """The output must be the BRD §3.2 canonical JSON, byte-checkable key-by-key."""

    def test_valid_futures_pays_out_canonical_schema(self):
        payload, code, err = run_stage0_text(VALID_FUTURES)
        self.assertEqual(code, 0, err)
        for key in ("symbol", "exchange", "instrument_type", "action", "entry_price",
                    "entry_range_min", "entry_range_max", "live_price", "target",
                    "stoploss", "lot_size"):
            self.assertIn(key, payload, f"canonical field missing: {key}")
        self.assertEqual(payload["symbol"], "RELIANCE")
        self.assertEqual(payload["instrument_type"], "FUT")
        self.assertEqual(payload["entry_range_min"], 2975.00)
        self.assertEqual(payload["entry_range_max"], 2985.00)
        self.assertEqual(payload["lot_size"], 250)
        self.assertEqual(payload["live_price"], 2988.20)

    def test_cash_equity_action_defaults_to_buy(self):
        payload, code, err = run_stage0_text(VALID_CASH)
        self.assertEqual(code, 0, err)
        self.assertEqual(payload["instrument_type"], "CASH")
        self.assertEqual(payload["action"], "BUY")


class TestBanAndSurveillance(unittest.TestCase):
    """TICK-001 gates 2 and 3: MWPL ban flag, ASM/GSM stage flags.

    Offline determinism: the script must carry its own mock ban/ASM lists so
    the seam never touches the network (zero-hallucination fixture contract).
    """

    def test_unbanned_futures_gets_ban_status_false(self):
        payload, code, err = run_stage0_text(VALID_FUTURES)
        self.assertEqual(code, 0, err)
        self.assertFalse(payload["gatekeeper"]["ban_status"])
        self.assertFalse(payload["gatekeeper"]["asm_gsm_flagged"])

    def test_banned_symbol_flags_ban_status_true_without_rejecting_schema(self):
        payload, code, err = run_stage0_text(VALID_FUTURES.replace("RELIANCE", "TESTBANNED"))
        self.assertEqual(code, 0, err)
        self.assertTrue(payload["gatekeeper"]["ban_status"])

    def test_asm_gsm_stage2_flagged(self):
        payload, code, err = run_stage0_text(VALID_FUTURES.replace("RELIANCE", "TESTASM2"))
        self.assertEqual(code, 0, err)
        self.assertTrue(payload["gatekeeper"]["asm_gsm_flagged"])


class TestLotMarginExposure(unittest.TestCase):
    """TICK-001 gate 4: validate lot size and total initial margin exposure."""

    def test_margin_exposure_computed_from_lot_size_and_live_price(self):
        payload, code, err = run_stage0_text(VALID_FUTURES)
        self.assertEqual(code, 0, err)
        self.assertIn("margin_exposure_inr", payload["gatekeeper"])
        expected = 250 * 2988.20
        self.assertEqual(payload["gatekeeper"]["margin_exposure_inr"], expected)

    def test_cash_equity_has_no_lot_exposure(self):
        payload, code, err = run_stage0_text(VALID_CASH)
        self.assertEqual(code, 0, err)
        self.assertIsNone(payload["gatekeeper"].get("margin_exposure_inr"))


class TestBadInputHandling(unittest.TestCase):
    """Structural parse failures must be distinguishable from gate rejections."""

    def test_empty_input_fails_cleanly(self):
        _, code, err = run_stage0_text("")
        self.assertNotEqual(code, 0)
        self.assertIn("ERR_PARSE", err)

    def test_missing_required_field_fails_cleanly(self):
        _, code, err = run_stage0_text("Symbol: RELIANCE\nType: FUT\n")
        self.assertNotEqual(code, 0)
        self.assertIn("ERR_PARSE", err)

    def test_malformed_entry_range_fails_cleanly(self):
        _, code, err = run_stage0_text(VALID_FUTURES.replace("2975.00-2985.00", "garbage"))
        self.assertNotEqual(code, 0)
        self.assertIn("ERR_PARSE", err)


class TestCliContract(unittest.TestCase):
    """Exactly ONE subprocess invocation per stage — the frozen BRD §9.1 shape.

    The rejection path is already covered in-process (exit code + stderr code
    come from the same main() both modes call); this single interpreter-level
    run pins the wrapper mechanics: real argv handling, exit-code propagation,
    stderr routing, stdout-only-JSON purity. One spawn, not one per case.
    """

    def test_canonical_invocation_via_real_interpreter(self):
        """`python scripts/0_ingest_reco.py --input input.txt > current_trade.json`
        must behave end-to-end like the imported mode."""
        with temp_input(VALID_FUTURES, ".txt") as path:
            proc = subprocess_mode(SCRIPT, ["--input", path])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        import json as _json
        payload = _json.loads(proc.stdout)  # stdout must be pure JSON
        self.assertEqual(payload["symbol"], "RELIANCE")


# --------------------------------------------------------------------------
# ADR-001 property coverage: the rejection invariant holds around the five
# seeded probes, not only on them (evals.md §2.1).
# --------------------------------------------------------------------------
# ADR-001's fatal vocabulary, asserted equal to the parser's own below.
FATAL_TOKENS = ("CE", "PE", "OPT", "OPTION", "CALL", "PUT")
# ADR-001's other half (docs/agents/domain.md): "only Equities and Futures are
# permitted", so the instrument domain is closed. A value the gate cannot
# recognize leaves with its own code — the failure mode this stops is an
# unrecognized instrument being *assumed* to be cash and scored as one.
RECOGNIZED_INSTRUMENTS = ("CASH", "FUT")
# Option tokens written with look-alike letters: a human reads "CE", the parser
# reads a two-letter string that is not the ASCII token. The closed domain is
# what stops these — refused as unrecognized, never admitted as an unknown
# instrument (and never reported as an option rejection, which they are not).
HOMOGLYPH_OPTION_TOKENS = {
    "cyrillic-CE": "\u0421\u0415",
    "cyrillic-PE": "\u0420\u0415",
    "greek-CE": "\u03f9\u0395",
    "mixed-latin-cyrillic-CE": "C\u0415",
}
# Values that are simply not in the closed domain.
UNRECOGNIZED_INSTRUMENTS = ("NFO", "EQ", "EQUITY", "FUTURE", "FUTURES",
                            "CASH EQUITY", "FO", "CASHFUT", "OPTIONS")
ZERO_WIDTH = "\u200b"          # a Cf character: invisible, not whitespace
FULLWIDTH_COLON = "\uff1a"    # NFKC folds it to ":"
# Colon look-alikes NFKC folds to ":" — the field parses, so the frozen
# options code is reachable and required. (U+FE55 small colon, U+FE13
# presentation-form vertical colon.)
FOLDED_COLONS = ("\ufe55", "\ufe13")
# Colon look-alikes NFKC does *not* fold: the field cannot parse at all, so
# the paste is refused without ever reaching the options gate. ADR-001 only
# requires that it never becomes a trade.
UNFOLDED_COLONS = ("\u2236", "\u02d0", "\u05c3", "\ua789")
BENIGN_BODY = ("Entry Range: 100.00-102.00\nLive Price: 101.00\n"
               "Stoploss: 95.00\nTarget: 115.00\n"
               "Recommended Date: 2026-10-09\n")


def _fullwidth(text: str) -> str:
    """Render ASCII letters/punctuation with their fullwidth look-alikes."""
    return "".join(chr(ord(ch) + 0xFEE0) if "!" <= ch <= "~" else ch for ch in text)


def _zero_width_inside(token: str) -> str:
    """`CE` → `C\u200bE`: one invisible character between every letter."""
    return ZERO_WIDTH.join(token)


def _near_miss_pastes() -> list[tuple[str, str, str]]:
    """(label, paste, expectation) triples — the fuzz space around the probes.

    Two directions, both required: a **fatal** family that declares an option
    token somewhere a paste can declare an instrument (the `Type:` field or the
    `Symbol:` field) under case, padding, token punctuation, Unicode
    look-alikes and invisible characters, and a **benign** family whose text
    merely resembles one (a ticker that *contains* the letters, a prose warning
    that mentions options) and which must still be accepted — a parser that
    refused everything would pass a one-sided property and fail this half.
    """
    cases: list[tuple[str, str, str]] = []

    def add(label: str, paste: str, expectation: str) -> None:
        cases.append((label, paste, expectation))

    token_forms = {
        "plain": lambda t: t,
        "lower": lambda t: t.lower(),
        "title": lambda t: t.title(),
        "padded": lambda t: f" {t} ",
        "parenthesised": lambda t: f"({t})",
        "fullwidth": _fullwidth,
        "zero-width": _zero_width_inside,
    }
    field_forms = {
        "canonical": ("Type", ": "),
        "lower-key": ("type", ": "),
        "upper-key": ("TYPE", ": "),
        "spaced-colon": ("Type", " : "),
        "fullwidth-colon": ("Type", FULLWIDTH_COLON),
        "small-colon": ("Type", FOLDED_COLONS[0]),
        "vertical-colon": ("Type", FOLDED_COLONS[1]),
        "invisible-key": (f"Ty{ZERO_WIDTH}pe", ": "),
    }

    # 1. The fatal token in the instrument field, in every near-miss shape.
    for token in FATAL_TOKENS:
        for form, render in token_forms.items():
            for field, (key, sep) in field_forms.items():
                add(f"Type/{token}/{form}/{field}",
                    f"Symbol: RELIANCE\n{key}{sep}{render(token)}\n" + BENIGN_BODY,
                    "reject")

    # 2. A cash label over an option-shaped payload: the token in the symbol.
    for token in FATAL_TOKENS:
        for form, render in token_forms.items():
            add(f"Symbol/{token}/{form}",
                f"Symbol: NIFTY 25000 {render(token)}\nType: CASH\n" + BENIGN_BODY,
                "reject")
        add(f"Type-annotated/{token}",
            f"Symbol: NIFTY\nType: CASH ({token})\n" + BENIGN_BODY, "reject")
        add(f"Type-annotated/fullwidth/{token}",
            f"Symbol: NIFTY\nType: {FULLWIDTH_COLON}{_fullwidth(f'CASH ({token})')}\n"
            + BENIGN_BODY, "reject")

    # 2b. Separator look-alikes normalization does not fold: the instrument
    # field never parses, so the paste is refused for a different reason — the
    # invariant that must hold is the weaker, honest one (no canonical payload).
    for separator in UNFOLDED_COLONS:
        for token in ("CE", "PE", "OPT"):
            add(f"unfolded-colon/U+{ord(separator):04X}/{token}",
                f"Symbol: RELIANCE\nType{separator}{token}\n" + BENIGN_BODY,
                "refuse")

    # 2c. Option tokens written with look-alike letters: not the ASCII token,
    # so the *closed domain* is what refuses them.
    for label, value in HOMOGLYPH_OPTION_TOKENS.items():
        add(f"homoglyph/{label}",
            f"Symbol: RELIANCE\nType: {value}\n" + BENIGN_BODY, "unrecognized")

    # 2d. The closed domain itself (ADR-001): everything that is not CASH/FUT.
    for value in UNRECOGNIZED_INSTRUMENTS:
        add(f"unrecognized/{value}",
            f"Symbol: SBIN\nType: {value}\n" + BENIGN_BODY, "unrecognized")

    # 3. Benign near-misses: same letters, no declared option instrument.
    benign_symbols = ("CENTURYPLY", "PEPSICO", "OPTIMUS", "CALLISTO",
                      f"CENT{ZERO_WIDTH}URYPLY", "NIFTY 25000")
    for symbol in benign_symbols:
        for kind in ("CASH", "FUT"):
            add(f"benign/{symbol}/{kind}",
                f"Symbol: {symbol}\nType: {kind}\n" + BENIGN_BODY, "accept")
    add("benign/prose-warning",
        "Symbol: SBIN\nType: CASH\n"
        "App Warning: options, calls and puts are for experts\n" + BENIGN_BODY,
        "accept")
    add("benign/fullwidth-colon",
        f"Symbol: SBIN\nType{FULLWIDTH_COLON}CASH\n" + BENIGN_BODY, "accept")
    add("benign/lower-case-key",
        "symbol: SBIN\ntype: cash\n" + BENIGN_BODY, "accept")
    add("benign/futures-expiry",
        "Symbol: RELIANCE\nType: FUT\nLot Size: 250\nExpiry: 2026-10-29\n"
        + BENIGN_BODY, "accept")
    # 3b. Recognized spellings: the closed domain must not be brittle. Case,
    # padding, NFKC look-alikes and invisible characters are all folded by the
    # parser, so these are the same two instruments, not three.
    for value, label in (("cash", "lower-case-cash"),
                         ("Fut", "title-case-fut"),
                         ("  FUT  ", "padded-fut"),
                         ("\uff23\uff21\uff33\uff28", "fullwidth-cash"),
                         (f"CA{ZERO_WIDTH}SH", "zero-width-cash")):
        add(f"benign/type-{label}",
            f"Symbol: SBIN\nType: {value}\n" + BENIGN_BODY, "accept")
    return cases


class TestOptionRejectionPropertyCoverage(unittest.TestCase):
    """The invariant is a property of the paste space, not of five samples.

    Stage 0 kills an option paste before a canonical trade exists (ADR-001), so
    the five seeded probes are five test cases, never the whole class. This
    suite enumerates near-miss transforms deterministically (no dependency, no
    sampling) and asserts both directions of the invariant, including the
    disguised shapes a `CASH` label can wear — the shapes a field-equality
    check (`instrument_type in OPTIONS_TYPES`) would wave through.
    """

    @classmethod
    def setUpClass(cls):
        cls.cases = _near_miss_pastes()

    def test_the_fuzz_alphabet_is_the_parsers_own_vocabulary(self):
        """Both vocabularies are the parser's own; widening one, not the fuzz,
        must fail here — that is the whole point of asserting the parity."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "uvest_stage0_property_vocabulary", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(set(FATAL_TOKENS), set(module.OPTIONS_TYPES))
        self.assertEqual(set(RECOGNIZED_INSTRUMENTS),
                         set(module.RECOGNIZED_INSTRUMENTS))

    def test_unrecognized_instruments_are_refused_with_their_own_code(self):
        """A closed domain (ADR-001): anything that is not CASH/FUT leaves.

        The distinct code matters — an unrecognized instrument is not an
        options rejection and must not borrow that verdict, or the scorecard's
        option rate would count pastes the gate never classified as options.
        """
        unrecognized = [(label, paste) for label, paste, want in self.cases
                        if want == "unrecognized"]
        self.assertGreaterEqual(len(unrecognized), 12,
                                "the unrecognized family collapsed")
        for label, paste in unrecognized:
            with self.subTest(variant=label):
                payload, code, err = run_stage0_text(paste)
                self.assertEqual(code, 2, f"{label}: exit {code}, stderr={err.strip()!r}")
                self.assertIn("ERR_INSTRUMENT_UNRECOGNIZED", err, label)
                self.assertNotIn("ERR_OPTIONS_PROHIBITED", err,
                                 f"{label}: borrowed the option verdict")
                self.assertIsNone(payload, f"{label}: emitted a canonical payload")

    def test_an_option_homoglyph_cannot_pose_as_an_unknown_instrument(self):
        """The gap this closes: `Type: СЕ` (Cyrillic) read as CE by a human.

        It is not the ASCII token, so the option gate cannot name it — before
        the closed domain it was admitted and downstream stages scored it as
        cash. Now the choice is between a policy refusal and a mislabelled
        trade, and the refusal wins; the contrasting ASCII paste proves the
        option verdict still belongs to the token it names.
        """
        for label, value in HOMOGLYPH_OPTION_TOKENS.items():
            with self.subTest(homoglyph=label):
                payload, code, err = run_stage0_text(
                    f"Symbol: RELIANCE\nType: {value}\n" + BENIGN_BODY)
                self.assertEqual(code, 2, f"{label}: admitted — {err.strip()!r}")
                self.assertIn("ERR_INSTRUMENT_UNRECOGNIZED", err)
                self.assertIsNone(payload)
        for token in ("CE", "PE", "OPT"):
            with self.subTest(ascii_token=token):
                payload, code, err = run_stage0_text(
                    f"Symbol: RELIANCE\nType: {token}\n" + BENIGN_BODY)
                self.assertEqual(code, 2)
                self.assertIn("ERR_OPTIONS_PROHIBITED", err)
                self.assertNotIn("ERR_INSTRUMENT_UNRECOGNIZED", err,
                                 "an option paste is an option, not an unknown type")
                self.assertIsNone(payload)

    def test_every_fatal_variant_is_refused_with_the_frozen_contract(self):
        """Exit 2 *and* the frozen code *and* no canonical payload."""
        fatal = [(label, paste) for label, paste, want in self.cases if want == "reject"]
        self.assertGreaterEqual(len(fatal), 60, "the fuzz space collapsed")
        for label, paste in fatal:
            with self.subTest(variant=label):
                payload, code, err = run_stage0_text(paste)
                self.assertEqual(code, 2,
                                 f"{label}: exit {code}, stderr={err.strip()!r}")
                self.assertIn("ERR_OPTIONS_PROHIBITED", err, label)
                self.assertIsNone(payload, f"{label}: rejected but still emitted a trade")

    def test_unfolded_separators_are_refused_without_a_payload(self):
        """Separator look-alikes that never parse must still refuse the option.

        `Type∶CE` (U+2236) is not `Type: CE` to any parser, so the frozen
        `ERR_OPTIONS_PROHIBITED` code is unreachable — but an option
        recommendation must not become a trade either way, so these are graded
        on the invariant that does apply: a non-zero exit with the refusal on
        stderr and no canonical payload. Grading them on the frozen code would
        assert something the parser cannot deliver; grading them not at all is
        how a look-alike separator becomes a bypass.
        """
        unfolded = [(label, paste) for label, paste, want in self.cases
                    if want == "refuse"]
        self.assertGreaterEqual(len(unfolded), 8, "the separator family collapsed")
        for label, paste in unfolded:
            with self.subTest(variant=label):
                payload, code, err = run_stage0_text(paste)
                self.assertNotEqual(code, 0, f"{label}: admitted a refused paste")
                self.assertIsNone(payload, f"{label}: emitted a canonical payload")
                self.assertTrue("ERR_OPTIONS_PROHIBITED" in err or "ERR_PARSE" in err,
                                f"{label}: unnamed refusal — {err.strip()!r}")

    def test_benign_lookalikes_are_still_accepted(self):
        """The other side of the property: fail closed, not fail everything."""
        benign = [(label, paste) for label, paste, want in self.cases if want == "accept"]
        self.assertGreaterEqual(len(benign), 8, "the benign control set collapsed")
        for label, paste in benign:
            with self.subTest(variant=label):
                payload, code, err = run_stage0_text(paste)
                self.assertEqual(code, 0, f"{label}: refused a legitimate paste — {err}")
                self.assertIn(payload["instrument_type"], ("CASH", "FUT"))

    def test_the_near_misses_are_near_misses(self):
        """The fatal family must not be plain `Type: CE` ten times over.

        Each shape family has to be present, or the coverage would only repeat
        what the seeded probes already establish: a declared option, plainly.
        """
        labels = [label for label, _, want in self.cases if want == "reject"]
        for family in ("/zero-width", "/fullwidth", "/small-colon",
                       "/vertical-colon", "/lower", "/padded",
                       "/parenthesised", "Symbol/", "Type-annotated/"):
            self.assertTrue(any(family in label for label in labels),
                            f"no {family} variant was generated")
        unrecognized = [label for label, _, want in self.cases
                        if want == "unrecognized"]
        for family in ("homoglyph/", "unrecognized/"):
            self.assertTrue(any(family in label for label in unrecognized),
                            f"no {family} variant was generated")


class TestRecommendedDate(unittest.TestCase):
    """The date stage 12 names its report from must exist before the chain starts.

    `recommended_date` was the one field the canonical schema (BRD §3.2)
    carried but stage 0 did not require, and stage 12 refuses to render
    without it: a paste stage 0 admitted walked 0 → 9, wrote every artifact,
    then died at the last stage with `ERR_INPUT: trade input is missing
    recommended_date` and exit 2. The report is named
    `report_{SYMBOL}_{DATE}.html` from exactly this string (TICK-010) and the
    evals locate it by it, so no later stage can invent it — the check has to
    live where the refusal is free, before a single artifact exists.
    """

    @staticmethod
    def without_date(paste: str) -> str:
        return "".join(line for line in paste.splitlines(keepends=True)
                       if not line.lower().startswith("recommended date"))

    def test_the_accepted_payload_carries_the_declared_date(self):
        payload, code, err = run_stage0_text(VALID_FUTURES)
        self.assertEqual(code, 0, err)
        self.assertEqual(payload["recommended_date"], "2026-10-09")

    def test_a_dateless_paste_is_refused_before_any_artifact_exists(self):
        """The gap: admitted at stage 0, fatal at stage 12. Refuse instead."""
        payload, code, err = run_stage0_text(self.without_date(VALID_CASH))
        self.assertEqual(code, 1, f"expected an ingestion refusal, got {code}: {err}")
        self.assertIn("ERR_PARSE", err)
        self.assertIn("recommended_date", err)
        self.assertIsNone(payload, "a dateless paste still emitted a trade")

    def test_a_malformed_date_is_refused_too(self):
        """Present but unusable would kill the chain just as late: `report_X_09/10/26.html`
        is not even a path, and the harness keys reports off an ISO date."""
        for value in ("09/10/2026", "Oct 09, 2026", "09-10-2026", "20260105",
                      "2026-13-45", "2026-02-30"):
            with self.subTest(value=value):
                paste = self.without_date(VALID_CASH) + f"Recommended Date: {value}\n"
                payload, code, err = run_stage0_text(paste)
                self.assertEqual(code, 1,
                                 f"{value!r} admitted — {err.strip()!r}")
                self.assertIn("ERR_PARSE", err)
                self.assertIn("recommended_date", err)
                self.assertIsNone(payload)

    def test_the_policy_gates_still_speak_first(self):
        """BRD §3.3 #1 — an option must never be reported as a parse problem.

        The date check therefore runs *after* the ADR-001 gates: a card that
        is both undated and an option is still refused as an option, and an
        unknown instrument still borrows neither verdict. Fail closed, but
        never with the wrong reason.
        """
        payload, code, err = run_stage0_text(
            self.without_date(VALID_FUTURES).replace("Type: FUT", "Type: CE"))
        self.assertEqual(code, 2, f"an option was not refused as one: {err!r}")
        self.assertIn("ERR_OPTIONS_PROHIBITED", err)
        self.assertNotIn("ERR_PARSE", err)
        self.assertIsNone(payload)

        payload, code, err = run_stage0_text(
            self.without_date(VALID_CASH).replace("Type: CASH", "Type: NFO"))
        self.assertEqual(code, 2, f"an unknown instrument was not refused: {err!r}")
        self.assertIn("ERR_INSTRUMENT_UNRECOGNIZED", err)
        self.assertNotIn("ERR_PARSE", err)
        self.assertIsNone(payload)