"""Cross-document pipeline contract consistency test.

Documentation-as-tested-artifact invariant: fails the suite whenever the frozen
pipeline contract drifts between any of its mirrors —

  1. ``BRD.md`` §9.1 canonical contract table (the single source of truth)
  2. ``BRD.md`` §9 execution pipeline bash block
  3. ``prompt.md`` §2 execution pipeline bash block
  4. ``docs/tickets/INDEX.md`` ticket rows and test seams
  5. The sliced ticket files themselves

Zero-dependency, fast, and network-free (Kailash Nadh style): a broken doc link
or a renamed script must fail the build, not a code review.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
BRD_FILE = ROOT_DIR / "BRD.md"
PROMPT_FILE = ROOT_DIR / "prompt.md"
INDEX_FILE = ROOT_DIR / "docs" / "tickets" / "INDEX.md"
TICKETS_DIR = ROOT_DIR / "docs" / "tickets"
AGENTS_DIR = ROOT_DIR / "docs" / "agents"
INTEGRITY_TEST = ROOT_DIR / "tests" / "test_prompt_integrity.py"

# BRD.md §9.1 canonical contract row: | stage | `script` | artifact(s) | `seam` | ticket |
CONTRACT_ROW_RE = re.compile(
    r"^\|\s*(\d{1,2})\s*\|\s*`(scripts/[0-9a-z_]+\.py)`\s*\|\s*(.+?)\s*"
    r"\|\s*`(tests/[0-9a-z_]+\.py)`\s*\|\s*(.+?)\s*\|\s*$",
    re.MULTILINE,
)
# Harness row: no stage number, seam duplicated for the eval runner.
HARNESS_ROW_RE = re.compile(
    r"^\|\s*[\u2014\u2013-]\s*\|\s*`(tests/[0-9a-z_]+\.py)`[^|]*"
    r"\|\s*(.+?)\s*\|\s*`(tests/[0-9a-z_]+\.py)`\s*\|\s*(.+?)\s*\|\s*$",
    re.MULTILINE,
)
PIPELINE_CALL_RE = re.compile(r"python\s+(scripts/[0-9a-z_]+\.py)")
SCRIPT_MENTION_RE = re.compile(r"scripts/([0-9a-z_]+\.py)")
FENCED_BLOCK_RE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)

INDEX_ROW_RE = re.compile(
    r"^\|\s*`(?P<ticket>[A-Z]+-\d+)`\s*\|\s*.*?\|\s*(?P<status>[^|]*?)\s*"
    r"\|\s*`(?P<seam>tests/[0-9a-z_]+\.py)`\s*\|\s*(?P<priority>[^|]*?)\s*\|\s*$",
    re.MULTILINE,
)
TICKET_ID_RE = re.compile(r"`([A-Z]+-\d+)`")
TEST_SEAM_RE = re.compile(r"\*\*Test Seam:\*\*\s*`(tests/[0-9a-z_]+\.py)`")
TARGET_SCRIPT_RE = re.compile(r"\*\*Target Script:\*\*\s*`(scripts/[0-9a-z_]+\.py)`")

REPORT_PATH_RE = re.compile(r"reports/report_[A-Za-z0-9_{}]+\.html")
CANONICAL_REPORT_TEMPLATE = "reports/report_{SYMBOL}_{DATE}.html"
CONCRETE_REPORT_RE = re.compile(r"^reports/report_[A-Z0-9]+_\d{8}\.html$")

SKILLS_DIR = ROOT_DIR / "skills"
AGENT_SKILLS_DIR = ROOT_DIR / ".agents" / "skills"

SLICED_MARKER = "sliced"
PENDING_MARKER = "pending"


def read_normalized(path: Path) -> str:
    """Read a contract document with LF line endings for stable parsing."""
    if not path.exists():
        raise AssertionError(f"required contract document missing: {path}")
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def pipeline_calls(text: str) -> list[str]:
    """Extract the ordered script list of the single pipeline bash block."""
    blocks = [block for block in FENCED_BLOCK_RE.findall(text) if "python scripts/" in block]
    if len(blocks) != 1:
        raise AssertionError(
            f"expected exactly one 'python scripts/' pipeline block, found {len(blocks)}"
        )
    return PIPELINE_CALL_RE.findall(blocks[0])


class TestContractConsistency(unittest.TestCase):
    """The frozen pipeline contract and all of its mirrors must agree exactly."""

    @classmethod
    def setUpClass(cls):
        cls.brd = read_normalized(BRD_FILE)
        cls.prompt = read_normalized(PROMPT_FILE)
        cls.index = read_normalized(INDEX_FILE)

        rows = CONTRACT_ROW_RE.findall(cls.brd)
        if not rows:
            raise AssertionError(
                "BRD.md §9.1 canonical contract table not found — rows must match "
                "| stage | `scripts/x.py` | artifact | `tests/test_x.py` | ticket |"
            )

        cls.contract: list[dict] = []
        for stage_raw, script, artifact, seam, ticket_cell in rows:
            ticket_match = TICKET_ID_RE.search(ticket_cell)
            cls.contract.append(
                {
                    "stage": int(stage_raw),
                    "script": script,
                    "artifact": artifact,
                    "seam": seam,
                    "ticket": ticket_match.group(1) if ticket_match else None,
                    "ticket_cell": ticket_cell,
                    "unclaimed": ticket_match is None,
                }
            )

        cls.scripts = [row["script"] for row in cls.contract]
        cls.seams = [row["seam"] for row in cls.contract]
        cls.seam_by_ticket = {
            row["ticket"]: row["seam"] for row in cls.contract if row["ticket"]
        }
        cls.script_by_ticket = {
            row["ticket"]: row["script"] for row in cls.contract if row["ticket"]
        }

        # The evals harness is a ticket with a seam but no pipeline stage.
        cls.harness_tickets: dict[str, str] = {}
        for _script_cell, _artifact, seam, ticket_cell in HARNESS_ROW_RE.findall(cls.brd):
            ticket_match = TICKET_ID_RE.search(ticket_cell)
            if ticket_match:
                cls.harness_tickets[ticket_match.group(1)] = seam
        cls.seam_by_ticket = {**cls.seam_by_ticket, **cls.harness_tickets}
        cls.index_rows = INDEX_ROW_RE.findall(cls.index)
        if not cls.index_rows:
            raise AssertionError("docs/tickets/INDEX.md ticket rows could not be parsed")

    @classmethod
    def contract_docs(cls) -> list[Path]:
        """Every document that must mirror the canonical pipeline contract."""
        docs = [BRD_FILE, PROMPT_FILE]
        docs += sorted(AGENTS_DIR.glob("*.md"))
        docs += sorted(TICKETS_DIR.glob("*.md"))
        docs += sorted(SKILLS_DIR.rglob("*.md"))
        docs += sorted(AGENT_SKILLS_DIR.rglob("*.md"))
        return docs

    # ---------------------------------------------------------------- contract

    def test_contract_table_is_well_formed(self):
        """Stages are contiguous from 0, unique, and named/seamed consistently."""
        stages = [row["stage"] for row in self.contract]
        self.assertEqual(
            stages, list(range(len(stages))), "contract stages must be contiguous from 0"
        )
        self.assertEqual(len(set(stages)), len(stages), "duplicate stage number in contract")
        self.assertEqual(len(set(self.scripts)), len(self.scripts), "duplicate script in contract")
        self.assertEqual(len(set(self.seams)), len(self.seams), "duplicate test seam in contract")

    def test_seam_is_derived_from_script_stem(self):
        """A seam is never hand-assigned: tests/test_{script stem}.py, always."""
        for row in self.contract:
            expected = f"tests/test_{Path(row['script']).stem}.py"
            self.assertEqual(
                row["seam"],
                expected,
                f"stage {row['stage']}: seam {row['seam']} is not derived from {row['script']}",
            )

    def test_artifact_name_matches_stage(self):
        """JSON artifacts follow out_{stage}_{slug}.json; 0 holds input, 12 holds the report."""
        for row in self.contract:
            stage = row["stage"]
            if stage == 0:
                self.assertEqual(row["artifact"], "`current_trade.json`")
            elif stage == 12:
                self.assertEqual(
                    row["artifact"], "`reports/report_{SYMBOL}_{DATE}.html`"
                )
            else:
                self.assertIn(
                    f"out_{stage}_",
                    row["artifact"],
                    f"stage {stage}: artifact {row['artifact']} lacks prefix out_{stage}_",
                )

    # --------------------------------------------------------------- pipelines

    def test_brd_pipeline_matches_contract_order(self):
        """BRD §9 execution block is the contract, in ascending stage order."""
        calls = pipeline_calls(self.brd)
        self.assertEqual(calls, self.scripts, "BRD pipeline order disagrees with the contract")

    def test_prompt_pipeline_matches_contract_order(self):
        """prompt.md §2 mirrors the contract exactly: same scripts, same order."""
        calls = pipeline_calls(self.prompt)
        self.assertEqual(
            calls, self.scripts, "prompt.md §2 pipeline order disagrees with the contract"
        )

    def test_pipelines_reference_no_unknown_script(self):
        """No document may invoke a script absent from the contract."""
        for name, text in (("BRD.md", self.brd), ("prompt.md", self.prompt)):
            for call in pipeline_calls(text):
                self.assertIn(call, self.scripts, f"{name} invokes unknown script: {call}")

    def test_every_script_mention_in_docs_is_canonical(self):
        """Catches invented or stale script names anywhere in the contract docs."""
        allowed = set(self.scripts) | {"scripts/run_evals.py"}
        for path in self.contract_docs():
            text = read_normalized(path)
            for mention in set(SCRIPT_MENTION_RE.findall(text)):
                self.assertIn(
                    f"scripts/{mention}",
                    allowed,
                    f"{path.relative_to(ROOT_DIR)} references non-canonical script: {mention}",
                )

    def test_html_report_path_is_canonical(self):
        """The HTML artifact is the {SYMBOL}_{DATE} template, never a rolling filename."""
        for path in self.contract_docs():
            for ref in sorted(set(REPORT_PATH_RE.findall(read_normalized(path)))):
                self.assertTrue(
                    ref == CANONICAL_REPORT_TEMPLATE or CONCRETE_REPORT_RE.match(ref),
                    f"{path.relative_to(ROOT_DIR)} references non-canonical report path: {ref}",
                )

    def test_skill_trees_are_identical_mirrors(self):
        """Two skill copies must stay byte-identical, or they will silently diverge."""
        vendor = {
            p.relative_to(SKILLS_DIR): p.read_bytes() for p in SKILLS_DIR.rglob("*") if p.is_file()
        }
        runtime = {
            p.relative_to(AGENT_SKILLS_DIR): p.read_bytes()
            for p in AGENT_SKILLS_DIR.rglob("*")
            if p.is_file()
        }
        self.assertEqual(sorted(vendor), sorted(runtime), "skill trees contain different files")
        for rel in sorted(vendor):
            self.assertEqual(vendor[rel], runtime[rel], f"skill file content differs: {rel}")

    # ------------------------------------------------------------------- index

    def test_index_seams_match_contract_seams(self):
        """Every ticket's seam must equal the contract seam for its script."""
        for ticket, _status, seam, _priority in self.index_rows:
            self.assertIn(ticket, self.seam_by_ticket, f"{ticket} owns no contract stage")
            self.assertEqual(
                seam,
                self.seam_by_ticket[ticket],
                f"{ticket} seam {seam} disagrees with contract {self.seam_by_ticket[ticket]}",
            )

    def test_index_and_contract_cover_the_same_tickets(self):
        """No orphan tickets, no unlisted owners."""
        indexed = {row[0] for row in self.index_rows}
        contracted = set(self.seam_by_ticket)
        self.assertEqual(
            indexed,
            contracted,
            f"index-only: {sorted(indexed - contracted)} | contract-only: {sorted(contracted - indexed)}",
        )

    def test_every_stage_is_sliced_or_explicitly_unclaimed(self):
        """An unowned stage must be marked unclaimed in the contract, never silent."""
        for row in self.contract:
            if row["unclaimed"]:
                self.assertIn(
                    "unclaimed",
                    row["ticket_cell"],
                    f"stage {row['stage']} has no ticket and is not marked unclaimed",
                )
            else:
                self.assertNotIn(
                    "unclaimed",
                    row["ticket_cell"],
                    f"stage {row['stage']} is both claimed by {row['ticket']} and marked unclaimed",
                )

    # ----------------------------------------------------------------- tickets

    def test_sliced_ticket_files_exist_and_pending_do_not(self):
        """Slice status in the index reflects reality on disk."""
        for ticket, status, _seam, _priority in self.index_rows:
            matches = sorted(TICKETS_DIR.glob(f"{ticket}-*.md"))
            if SLICED_MARKER in status.lower():
                self.assertEqual(
                    len(matches),
                    1,
                    f"{ticket} is marked sliced but {len(matches)} file(s) exist",
                )
            elif PENDING_MARKER in status.lower():
                self.assertEqual(
                    len(matches),
                    0,
                    f"{ticket} is marked pending but {matches} exist(s)",
                )
            else:
                self.fail(f"{ticket} has unrecognised slice status: {status!r}")

    def test_ticket_files_declare_matching_seam_and_target(self):
        """A sliced ticket's declared seam and target must match index and contract."""
        for ticket, _status, index_seam, _priority in self.index_rows:
            files = sorted(TICKETS_DIR.glob(f"{ticket}-*.md"))
            if not files:
                continue
            text = read_normalized(files[0])
            seam_match = TEST_SEAM_RE.search(text)
            self.assertIsNotNone(seam_match, f"{files[0].name} declares no **Test Seam:**")
            self.assertEqual(
                seam_match.group(1), index_seam, f"{files[0].name} seam disagrees with the index"
            )

            if ticket.startswith("EVAL"):
                continue

            self.assertIn(
                ticket,
                self.script_by_ticket,
                f"{files[0].name} exists but no contract stage is owned by {ticket}",
            )
            target_match = TARGET_SCRIPT_RE.search(text)
            self.assertIsNotNone(
                target_match, f"{files[0].name} declares no **Target Script:**"
            )
            target = target_match.group(1)
            self.assertEqual(
                target,
                self.script_by_ticket[ticket],
                f"{files[0].name} targets {target}, contract says {self.script_by_ticket[ticket]}",
            )
            self.assertEqual(
                seam_match.group(1),
                f"tests/test_{Path(target).stem}.py",
                f"{files[0].name} seam is not derived from its target script",
            )

    # ------------------------------------------------------------ prompt test

    def test_prompt_integrity_required_scripts_are_canonical(self):
        """The prompt integrity suite must not require scripts the contract lacks."""
        text = read_normalized(INTEGRITY_TEST)
        match = re.search(r"required_scripts\s*=\s*\[(.*?)\]", text, re.DOTALL)
        self.assertIsNotNone(match, "required_scripts list not found in test_prompt_integrity.py")
        required = re.findall(r'"([^"]+)"', match.group(1))
        self.assertTrue(required, "required_scripts list is empty")
        for name in required:
            self.assertIn(
                f"scripts/{name}",
                self.scripts,
                f"test_prompt_integrity.py requires non-canonical script: {name}",
            )


if __name__ == "__main__":
    unittest.main()
