"""Chain contract — what stage 0 admits is what every stage can consume.

The gap this pins closed: stage 0 emitted `recommended_date` but never
required it, so a paste it accepted walked stages 0 → 9, wrote every artifact,
and then died at the **last** stage — stage 12 refuses to name a report
without that field, and the harness locates the report by the very string it
names. Every chain stage already declares what it cannot run without
(`REQUIRED_FIELDS`), but nothing ever checked those declarations against the
one payload every stage reads (`current_trade.json`, BRD §9.1), so producer
and consumer could disagree all the way to production.

Two halves, both read from the stages' own constants so the contract cannot
drift away from the code that enforces it:

* **producer → consumer** — stage 0's real payload satisfies every declared
  requirement, *present and non-null*. Key presence alone is exactly what let
  `recommended_date: null` through to stage 12, so null is graded as missing.
* **declaration → behaviour** — deleting a declared field makes that stage
  exit non-zero, so a row in the table is a requirement in the code and not a
  hopeful comment that would quietly stop being true.

The third half — an accepted paste walking the whole chain to a rendered
report, and a refused one stopping at stage 0 with nothing written — is
exercised through the spawned runner in `tests/test_run_pipeline.py`
(`TestOneInvocationChain`), where the orchestrator seam lives; this file
stays in-process on purpose, so the contract costs microseconds, not a chain
run per case.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))

from support.pipeline import (  # noqa: E402
    call_imported_main, import_mode, run_stage0_text,
)

# The dated chain fixture: the one paste already proved end-to-end, so the
# contract is asserted over input whose downstream survival is established.
FIXTURE_PASTE = TESTS_DIR / "fixtures" / "_input_reliance_20261009.txt"

# Every chain stage that reads `current_trade.json` and declares what it needs.
# Stage 0 is the producer (not checked against itself) and stages 4/8/10 do
# not exist yet, so they declare nothing and belong to no contract.
CONTRACT_STAGES = (1, 2, 3, 5, 6, 7, 9, 12)


class TestIngestionSatisfiesEveryStage(unittest.TestCase):
    """Producer → consumer: stage 0's payload carries what every stage demands."""

    @classmethod
    def setUpClass(cls):
        raw = FIXTURE_PASTE.read_text(encoding="utf-8")
        cls.payload, code, err = run_stage0_text(raw)
        if code != 0 or not isinstance(cls.payload, dict):
            raise AssertionError(
                f"the chain fixture no longer ingests: exit {code}, {err}")
        cls.required: dict[int, list[str]] = {}
        for stage in CONTRACT_STAGES:
            module, path = import_mode(stage)
            fields = getattr(module, "REQUIRED_FIELDS", None)
            if not fields:
                raise AssertionError(
                    f"{path.name} declares no REQUIRED_FIELDS — the chain "
                    "contract cannot be checked against a stage that states "
                    "nothing; declare what it cannot run without")
            cls.required[stage] = list(fields)

    def test_every_stage_declares_its_requirements(self):
        """The contract is the stages' own table, read from the code."""
        self.assertEqual(sorted(self.required), sorted(CONTRACT_STAGES))
        for stage, fields in self.required.items():
            self.assertTrue(fields, f"stage {stage} declared an empty set")

    def test_stage_0_payload_satisfies_every_declared_requirement(self):
        """Present *and* non-null — presence alone let a null date through."""
        for stage, fields in sorted(self.required.items()):
            for field in fields:
                with self.subTest(stage=stage, field=field):
                    self.assertIn(
                        field, self.payload,
                        f"stage {stage} requires {field!r}; stage 0 never "
                        "emits it — the chain would die at that stage")
                    self.assertIsNotNone(
                        self.payload[field],
                        f"stage {stage} requires {field!r}; stage 0 emits "
                        "null, which is the gap that reached stage 12")


class TestDeclaredRequirementsAreReal(unittest.TestCase):
    """Declaration → behaviour: a stated requirement is an enforced one.

    Without this half the table could rot into a comment: a stage that
    softened a check would keep the contract green while needing the field
    less than the contract claims. Each declared field is deleted from an
    otherwise valid payload, and the stage must refuse it.
    """

    @classmethod
    def setUpClass(cls):
        raw = FIXTURE_PASTE.read_text(encoding="utf-8")
        cls.payload, code, err = run_stage0_text(raw)
        if code != 0 or not isinstance(cls.payload, dict):
            raise AssertionError(
                f"the chain fixture no longer ingests: exit {code}, {err}")

    def test_deleting_a_declared_field_makes_its_stage_refuse(self):
        checked = 0
        for stage in CONTRACT_STAGES:
            module, path = import_mode(stage)
            for field in getattr(module, "REQUIRED_FIELDS", ()):
                with self.subTest(stage=stage, field=field):
                    broken = {k: v for k, v in self.payload.items() if k != field}
                    with tempfile.TemporaryDirectory() as tmp:
                        input_path = Path(tmp) / "current_trade.json"
                        input_path.write_text(json.dumps(broken),
                                              encoding="utf-8")
                        _, code, err = call_imported_main(
                            module, ["--input", str(input_path)])
                    self.assertNotEqual(
                        code, 0,
                        f"{path.name} ran without {field!r} — the contract "
                        "names it required (stderr: "
                        f"{err.strip()[:160]!r})")
                    checked += 1
        self.assertGreaterEqual(checked, 15,
                                "the requirement sweep covered too little "
                                "to prove anything")


if __name__ == "__main__":
    unittest.main()
