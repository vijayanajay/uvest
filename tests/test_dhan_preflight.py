"""Seam — the Phase 0 preflight probe (plan §4, `tests/evals/dhan_preflight.py`).

The campaign's first acceptance criterion is a *committed disclosure*: per-vintage
reachability, the **measured** retention boundary (the docs once said "5 trading
days", so the claim is verified rather than assumed, plan §3), the data-plan
status, and one real depth payload shape. This seam grades the tool that produces
it, entirely offline over an injected fetcher:

1. **No credentials, no probe, no writes.** A preflight that invented a
   disclosure, or left a cache/fixture behind, would be the exact defect the
   campaign exists to avoid: the run refuses, naming the env vars it needs, and
   nothing lands on disk.
2. **The boundary is measured, not assumed.** A vintage that answers and one that
   does not are both results, and the disclosure says which is which.
3. **A reachable vintage with an unreadable body fails loudly** — that is the
   probe's whole purpose (it is the only place a *real* response can close plan
   §2's host/path/body rows), so an unexpected shape must never be disclosed as
   "reached", and the run stops there rather than collecting more of nothing.
4. **Depth is read through stage 8's own parser** (`spread_from_quote`), so the
   shape printed is the shape the client consumes.
5. **Vintages come from the manifest**, never from dates typed into the tool, and
   the documented rate limits are enforced by the probe itself (§5 invariant 5).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
PREFLIGHT = TESTS_DIR / "evals" / "dhan_preflight.py"
MANIFEST = TESTS_DIR / "proof" / "scenario_manifest.json"

DOCUMENTED_DEPTH = {"data": {"NSE_EQ": {"2885": {
    "last_price": 2974.0,
    "depth": {"buy": [{"price": 2973.5, "quantity": 120, "orders": 3},
                      {"price": 2972.0, "quantity": 90, "orders": 2}],
              "sell": [{"price": 2974.5, "quantity": 80, "orders": 1},
                       {"price": 2976.0, "quantity": 200, "orders": 4}]}}}}}

CREDS = {"DHAN_CLIENT_ID": "client", "DHAN_ACCESS_TOKEN": "token"}


def load_preflight():
    spec = importlib.util.spec_from_file_location("dhan_preflight_seam", PREFLIGHT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, f"HTTP {code}", None, None)


def rows_for(symbol: str, dates: list[str]) -> dict:
    return {"scenarios": [
        {"scenario_id": f"SCEN-{date}", "symbol": symbol,
         "recommendation_date": date, "recorded_artifacts": [],
         "replay_inputs": {}} for date in dates]}


def documented_candles(sessions: int = 3) -> dict:
    """The documented parallel-array intraday body (epoch timestamps, read IST)."""
    return {"open": [100.0 + i for i in range(sessions)],
            "high": [101.0 + i for i in range(sessions)],
            "low": [99.0 + i for i in range(sessions)],
            "close": [100.5 + i for i in range(sessions)],
            "volume": [1000 + i for i in range(sessions)],
            "timestamp": [1741400100 + 300 * i for i in range(sessions)]}


class Recorder:
    """A fetcher answering from a URL→response map, recording every call."""

    def __init__(self, answers: dict):
        self.answers = answers
        self.calls: list[tuple[str, dict, dict | None]] = []

    def __call__(self, url: str, headers: dict, body: dict | None):
        self.calls.append((url, headers, body))
        answer = self.answers.get(url)
        if answer is None:
            raise AssertionError(f"unexpected request: {url}")
        if isinstance(answer, Exception):
            raise answer
        return answer(body) if callable(answer) else answer


class TestPhaseZeroProbe(unittest.TestCase):
    def setUp(self):
        self.pre = load_preflight()
        # The endpoints under test belong to the client (stage 8), not to this
        # seam: the probe must probe the URLs the campaign's client uses.
        self.s8 = self.pre.load_stage8()
        self.slept: list[float] = []

    def sleeper(self, seconds: float) -> None:
        self.slept.append(seconds)

    def run_probe(self, *, answers: dict, manifest: dict, creds: dict | None = None,
                  symbol: str = "RELIANCE", security_id: str = "2885"):
        recorder = Recorder(answers)
        report = self.pre.run_preflight(
            manifest=manifest, symbol=symbol, security_id=security_id,
            creds=CREDS if creds is None else creds,
            fetcher=recorder, sleep=self.sleeper)
        return report, recorder

    # -------------------------------------------------------- fail closed
    def test_absent_credentials_refuse_and_write_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            before = sorted(p.name for p in cwd.iterdir())
            with self.assertRaises(SystemExit) as raised:
                self.pre.main(["--manifest", str(MANIFEST), "--security-id", "2885"],
                              creds={}, fetcher=Recorder({}), sleep=self.sleeper)
            self.assertEqual(raised.exception.code, self.pre.EXIT_NO_CREDENTIALS)
            self.assertEqual(sorted(p.name for p in cwd.iterdir()), before,
                             "the probe must write nothing")

    def test_the_refusal_names_the_credentials_it_needs(self):
        message = self.pre.credentials_problem({})
        self.assertIn("DHAN_CLIENT_ID", message)
        self.assertIn("DHAN_ACCESS_TOKEN", message)
        self.assertIsNone(self.pre.credentials_problem(CREDS))

    def test_the_probe_needs_a_security_id_rather_than_guessing_one(self):
        self.assertIn("security", (self.pre.security_id_problem(None) or "").lower())
        self.assertIsNone(self.pre.security_id_problem("2885"))

    # ------------------------------------------------- measured boundary
    def test_the_retention_boundary_is_measured_not_assumed(self):
        pre = self.pre

        def intraday(body):
            if str((body or {}).get("fromDate", ""))[:4] == "2019":
                raise http_error(self.s8.DHAN_INTRADAY_URL, 400)
            return documented_candles()

        report, recorder = self.run_probe(
            answers={pre.DHAN_PROFILE_URL: {"dhanClientId": "1",
                                            "dataPlan": "Active",
                                            "dataValidity": "2026-12-31 23:59:59"},
                     self.s8.DHAN_INTRADAY_URL: intraday,
                     self.s8.DHAN_MARKETFEED_URL: DOCUMENTED_DEPTH},
            manifest=rows_for("RELIANCE", ["2019-01-25", "2022-04-11",
                                           "2024-06-04", "2025-10-15"]))
        self.assertEqual(report["data_plan"]["dataPlan"], "Active")
        self.assertEqual(report["outcome"], "complete")
        vintages = {v["year"]: v for v in report["vintages"]}
        self.assertEqual(sorted(vintages), ["2019", "2022", "2024", "2025"])
        self.assertTrue(vintages["2022"]["reachable"])
        self.assertFalse(vintages["2019"]["reachable"],
                         "the pre-2021 control answered — the boundary moved")
        self.assertEqual(report["retention"]["measured_earliest_vintage"], "2022")
        self.assertEqual(report["retention"]["unreachable_vintages"], ["2019"])
        # The disclosure carries what an operator needs to audit the run.
        self.assertIn("fromDate", report["vintages"][0]["request"]["body"])
        self.assertTrue(report["vintages"][0]["endpoint"].startswith(
            self.s8.DHAN_INTRADAY_URL))
        self.assertEqual(report["writes"], "none")
        self.assertTrue(recorder.calls)
        json.dumps(report)  # the disclosure is a committable JSON document

    # --------------------------------------------------- loud shape fail
    def test_a_reachable_vintage_with_an_unreadable_body_fails_loudly(self):
        pre = self.pre
        report, _ = self.run_probe(
            answers={self.s8.DHAN_INTRADAY_URL: {"unexpected": "shape"}},
            manifest=rows_for("RELIANCE", ["2022-04-11", "2024-06-04"]))
        self.assertEqual(report["outcome"], "unexpected_shape")
        self.assertEqual([v["reachable"] for v in report["vintages"]], [False])
        self.assertIn("shape", report["vintages"][0]["detail"].lower())
        self.assertEqual(pre.exit_code_for(report), pre.EXIT_UNEXPECTED_SHAPE)
        self.assertEqual(len(report["vintages"]), 1,
                         "the probe stops at the first unreadable shape")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            with self.assertRaises(SystemExit) as raised:
                pre.main(["--manifest", str(MANIFEST), "--security-id", "2885"],
                         creds=CREDS, sleep=self.sleeper,
                         fetcher=Recorder({
                             pre.DHAN_PROFILE_URL: {"dataPlan": "Active"},
                             self.s8.DHAN_INTRADAY_URL: {"x": 1}}))
        self.assertEqual(raised.exception.code, pre.EXIT_UNEXPECTED_SHAPE)
        # It fails loudly *and* still discloses what it got: the operator's only
        # record of a shape the client cannot read is the JSON on stdout.
        self.assertEqual(json.loads(printed.getvalue())["outcome"],
                         "unexpected_shape")

    # --------------------------------------------------------- depth shape
    def test_the_depth_shape_is_read_through_stage_8s_own_parser(self):
        pre = self.pre
        report, _ = self.run_probe(
            answers={self.s8.DHAN_INTRADAY_URL: documented_candles(),
                     self.s8.DHAN_MARKETFEED_URL: DOCUMENTED_DEPTH},
            manifest=rows_for("RELIANCE", ["2024-06-04"]))
        depth = report["depth"]
        self.assertEqual(depth["shape"],
                         "data.<segment>.<securityId>.depth.buy/sell")
        self.assertAlmostEqual(depth["spread_pct"],
                               round((2974.5 - 2973.5) / 2974.0 * 100.0, 4))
        self.assertEqual(depth["levels"], {"buy": 2, "sell": 2})
        self.assertEqual(depth["raw"], json.loads(json.dumps(DOCUMENTED_DEPTH)),
                         "the payload is printed, not paraphrased")
        self.assertEqual(depth["request"]["body"], {"NSE_EQ": ["2885"]})

    def test_an_unreadable_depth_body_is_disclosed_not_dropped(self):
        pre = self.pre
        report, _ = self.run_probe(
            answers={self.s8.DHAN_INTRADAY_URL: documented_candles(),
                     self.s8.DHAN_MARKETFEED_URL:
                         {"data": {"NSE_EQ": {"2885": {}}}}},
            manifest=rows_for("RELIANCE", ["2024-06-04"]))
        self.assertIsNone(report["depth"]["spread_pct"])
        self.assertTrue(report["depth"]["waived_reason"])
        self.assertIsNone(report["depth"]["levels"])

    # --------------------------------------------------- manifest-driven
    def test_one_probe_per_vintage_and_the_control_come_from_the_manifest(self):
        pre = self.pre
        manifest = {"scenarios": [
            {"scenario_id": "a", "symbol": "X", "recommendation_date": "2022-04-11"},
            {"scenario_id": "b", "symbol": "X", "recommendation_date": "2022-11-02"},
            {"scenario_id": "c", "symbol": "X", "recommendation_date": "2024-06-04"},
            {"scenario_id": "d", "symbol": "X", "recommendation_date": "2019-10-01"}]}
        probes = pre.plan_probes(manifest)
        self.assertEqual([p["year"] for p in probes], ["2019", "2022", "2024"])
        self.assertEqual(probes[1]["scenario_id"], "a",
                         "the earliest session of each vintage is the probe")
        self.assertEqual(probes[0]["role"], "pre-2021 control")
        self.assertEqual(probes[1]["role"], "vintage")
        real = pre.plan_probes(pre.load_manifest(MANIFEST))
        self.assertEqual(len({p["year"] for p in real}), len(real))
        self.assertTrue(all(p["symbol"] for p in real))

    # -------------------------------------------------------- rate limits
    def test_the_documented_rate_limits_are_enforced_by_the_probe(self):
        pre = self.pre
        self.run_probe(answers={self.s8.DHAN_INTRADAY_URL: documented_candles(),
                                self.s8.DHAN_MARKETFEED_URL: DOCUMENTED_DEPTH},
                       manifest=rows_for("RELIANCE", ["2022-04-11", "2024-06-04"]))
        self.assertTrue(self.slept, "the probe must throttle itself")
        self.assertGreaterEqual(min(self.slept), pre.DATA_MIN_INTERVAL)
        self.assertEqual(self.slept.count(pre.QUOTE_MIN_INTERVAL), 1,
                         "the quote API's 1 req/s limit is its own floor")


if __name__ == "__main__":
    unittest.main()
