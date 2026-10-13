"""Cross-document pipeline contract consistency test.

Documentation-as-tested-artifact invariant: fails the suite whenever the frozen
pipeline contract drifts between any of its mirrors —

  1. ``BRD.md`` §9.1 canonical contract table (the single source of truth)
  2. ``BRD.md`` §9 execution pipeline bash block
  3. ``prompt.md`` §2 execution pipeline bash block
  4. ``docs/tickets/INDEX.md`` ticket rows and test seams
  5. The sliced ticket files themselves
  6. ``tests/evals/golden_dataset.json`` per-stage payload keys (below)
  7. The verdict vocabulary itself — retired tokens may not reappear, and every
     ``§N`` citation must resolve to a real section (BRD §10.4, ADR-006)

Zero-dependency, fast, and network-free (Kailash Nadh style): a broken doc link
or a renamed script must fail the build, not a code review.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "tests" / "evals"))

import dataset_refs  # noqa: E402  (sibling eval module, spliced onto the path above)
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
# Non-stage scripts the contract docs may name: the eval harness and the 0 → 12
# orchestrator. Both are real entry points that are deliberately *not* BRD §9.1
# stage rows — run_pipeline executes those rows and run_evals grades them — so
# naming one is a reference, not an invented script.
NON_STAGE_SCRIPTS = frozenset({"scripts/run_evals.py", "scripts/run_pipeline.py"})
FENCED_BLOCK_RE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)

INDEX_ROW_RE = re.compile(
    r"^\|\s*`(?P<ticket>[A-Z]+-\d+)`\s*\|\s*.*?\|\s*(?P<status>[^|]*?)\s*"
    r"\|\s*`(?P<seam>tests/[0-9a-z_]+\.py)`\s*\|\s*(?P<priority>[^|]*?)\s*\|\s*$",
    re.MULTILINE,
)
# BRD §9 "Script Inventory" payload column: | `script.py` | in | ops | `fields` |
PAYLOAD_ROW_RE = re.compile(
    r"^\|\s*`([0-9a-z_]+\.py)`\s*\|\s*.*?\|\s*.*?\|\s*(.+?)\s*\|\s*$",
    re.MULTILINE,
)
DATASET_FILE = ROOT_DIR / "tests" / "evals" / "golden_dataset.json"
TICKET_ID_RE = re.compile(r"`([A-Z]+-\d+)`")
TEST_SEAM_RE = re.compile(r"\*\*Test Seam:\*\*\s*`(tests/[0-9a-z_]+\.py)`")
TARGET_SCRIPT_RE = re.compile(r"\*\*Target Script:\*\*\s*`(scripts/[0-9a-z_]+\.py)`")

REPORT_PATH_RE = re.compile(r"reports/report_[A-Za-z0-9_{}]+\.html")
CANONICAL_REPORT_TEMPLATE = "reports/report_{SYMBOL}_{DATE}.html"
CONCRETE_REPORT_RE = re.compile(r"^reports/report_[A-Z0-9]+_\d{8}\.html$")

SKILLS_DIR = ROOT_DIR / "skills"
AGENT_SKILLS_DIR = ROOT_DIR / ".agents" / "skills"
UNDERSTANDING_FILE = ROOT_DIR / "docs" / "understanding.md"

# The frozen verdict vocabulary is the 7-state lifecycle set (BRD §10.4, ADR-006).
CANONICAL_VERDICTS = ("GO", "GO_WEAK", "WAIT", "AVOID", "HOLD", "PRESSURE", "EXIT")
PRE_ENTRY_VERDICTS = CANONICAL_VERDICTS[:4]
# Tokens the 7-state set replaced (ADR-006). They may survive only as quoted
# history on a line that says so out loud — never as live instructions.
RETIRED_VERDICT_RE = re.compile(r"\b(NO[ _-]?GO|CONDITIONAL[ _-]?GO)\b", re.IGNORECASE)
HISTORICAL_MARKER_RE = re.compile(
    r"\b(retired|original|legacy|superseded|deprecated|rejected)\b", re.IGNORECASE
)
TOKEN_SPAN_RE = re.compile(r"`[^`]*`")

# A §N citation may carry a document qualifier (``BRD.md §10.3 #5``,
# ``docs/agents/evals.md §3``); unqualified citations resolve against the BRD,
# against understanding.md, or within the citing document itself.
CITATION_RE = re.compile(r"§\s*(\d+(?:\.\d+)?)(?:\s*#(\d+))?")
QUALIFIER_RE = re.compile(r"([\w./-]+\.md)`?\s*$")
BRD_HEADING_RE = re.compile(r"^#{1,6}\s*(\d+(?:\.\d+)?)[.):]?\s", re.MULTILINE)
BRD_ITEM_RE = re.compile(r"^(\d+)\.\s+\*\*", re.MULTILINE)

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


def _ascii(text: str) -> str:
    """Keep failure output printable on a CP1252 console, emojis and all."""
    return text.encode("ascii", "replace").decode("ascii")


def section_headings(text: str) -> set[str]:
    """Numbered section identifiers declared by a document's headings."""
    return set(BRD_HEADING_RE.findall(text))


def section_block(text: str, section: str) -> str:
    """One numbered section: from its heading to the next heading or rule."""
    match = re.search(
        rf"^#{{1,6}}\s*{re.escape(section)}[.):]?\s.*?(?=^#{{1,6}}\s|\n---)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(0) if match else ""


def numbered_items(block: str) -> list[str]:
    """The 1., 2., … items of a section, which are what `#K` citations count."""
    return BRD_ITEM_RE.findall(block)


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

        # Frozen payload-field inventory: every field the contract says a script
        # may emit, keyed by the AUTHENTIC contract artifact name (from the §9.1
        # canonical table, not re-derived — slugs like out_2_market.json don't
        # follow the script stem). Payload fields come from the §9 "Script
        # Inventory" payload column, listed in backticks: `field_a`, `field_b`…
        cls.payload_fields: dict[str, set[str]] = {}
        for _stage, script, artifact, _seam, _ticket in rows:
            artifact_name = artifact.strip().strip("`")
            # §9.1 rows keyed `scripts/N_foo.py`; §9 inventory rows keyed bare
            # `N_foo.py` — normalize to the basename before lookup.
            payload_cell = dict(PAYLOAD_ROW_RE.findall(cls.brd)).get(Path(script).name)
            cls.payload_fields[artifact_name] = set(
                re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", payload_cell or "")
            )

    @classmethod
    def vocabulary_docs(cls) -> list[Path]:
        """Every prose document that can hand a verdict token to a human or agent."""
        docs = sorted(ROOT_DIR.glob("*.md"))
        docs += sorted((ROOT_DIR / "docs").rglob("*.md"))
        docs += sorted(SKILLS_DIR.rglob("*.md"))
        docs += sorted(AGENT_SKILLS_DIR.rglob("*.md"))
        return docs

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
        allowed = set(self.scripts) | NON_STAGE_SCRIPTS
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

    # ------------------------------------------------------------- eval dataset

    def test_golden_dataset_payload_keys_exist_in_contract(self):
        """An *inline* `script_outputs` artifact and field must be declared in the
        frozen contract inventory (BRD §9 Script Inventory).

        Dataset records encode 'what a stage would emit' for historical trades.
        A hand-typed key that the contract never froze (e.g. a phantom
        `risk_reward_ratio` when stage 1 actually emits `raw_rr`/`live_rr`)
        would grade a universe no script can ever produce — caught here, at
        freeze time, rather than the first time a real replay lands.

        Records that `$ref` a proof-root artifact are out of scope: those bytes
        are a stage's full recorded stdout (which legitimately carries fields the
        §9 inventory, a list of *graded* fields, never enumerates), and they are
        enforced where they belong — by `test_proof_manifest.py` (the ref must
        equal the row's declared, hash-frozen artifact, and a recorded artifact
        may never also be inlined) and by the dataset seam that re-checks the
        breaker encoding on the resolved bytes.
        """
        if not DATASET_FILE.exists():
            self.skipTest("golden dataset not yet frozen")
        raw = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
        try:
            referenced = {(tid, artifact) for tid, artifact, _ref in dataset_refs.refs(raw)}
            dataset, _ref_notes, _derive_notes = dataset_refs.materialize(raw)
        except dataset_refs.RefError as exc:
            self.fail(f"golden dataset has an unresolvable artifact reference: {exc}")
        for trade in dataset.get("trades", []):
            tid = trade.get("trade_id", "<?>")
            for artifact, payload in trade.get("script_outputs", {}).items():
                if (tid, artifact) in referenced:
                    continue
                self.assertIn(
                    artifact,
                    self.payload_fields,
                    f"{tid}: script_outputs names artifact {artifact}, "
                    "which the BRD contract table does not declare",
                )
                for field in payload:
                    self.assertIn(
                        field,
                        self.payload_fields[artifact],
                        f"{tid}: {artifact}.{field} is not a frozen contract payload field "
                        f"(declared: {sorted(self.payload_fields[artifact])})",
                    )

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


    # ------------------------------------------------------- verdict vocabulary

    def test_documents_use_only_the_canonical_verdict_vocabulary(self):
        """Retired verdict tokens must not reappear anywhere in prose.

        A retired token is tolerated on a line only when that line explicitly
        frames it as history (an ADR context bullet, a migration note) *and*
        quotes it as a literal token. So `NO_GO` may be explained, never used.
        """
        offenders = []
        for path in self.vocabulary_docs():
            for lineno, line in enumerate(read_normalized(path).splitlines(), start=1):
                matches = list(RETIRED_VERDICT_RE.finditer(line))
                if not matches:
                    continue
                spans = [m.span() for m in TOKEN_SPAN_RE.finditer(line)]
                quoted = all(
                    any(start <= m.start() and m.end() <= end for start, end in spans)
                    for m in matches
                )
                if not (HISTORICAL_MARKER_RE.search(line) and quoted):
                    offenders.append(
                        f"{path.relative_to(ROOT_DIR).as_posix()}:{lineno}: {_ascii(line.strip())}"
                    )
        self.assertFalse(
            offenders,
            "retired verdict vocabulary reappeared — use the 7-state set "
            "(BRD §10.4, ADR-006):\n" + "\n".join(offenders),
        )

    def test_canonical_verdict_vocabulary_is_declared(self):
        """The 7-state set must stay declared where the contract points at it."""
        block = section_block(self.brd, "10.4")
        self.assertTrue(block, "BRD §10.4 (Canonical 7-State Verdict Vocabulary) is missing")
        for verdict in CANONICAL_VERDICTS:
            self.assertRegex(
                block, rf"\b{verdict}\b", f"BRD §10.4 no longer declares {verdict}"
            )

        for path, where, verdicts in (
            (PROMPT_FILE, "prompt.md's report template", PRE_ENTRY_VERDICTS),
            (AGENTS_DIR / "domain.md", "ADR-006", CANONICAL_VERDICTS),
        ):
            text = read_normalized(path)
            for verdict in verdicts:
                self.assertRegex(
                    text, rf"\b{verdict}\b", f"{where} no longer declares the {verdict} verdict"
                )

    def test_section_references_resolve(self):
        """A `§N` citation must name a section that exists, and `§X.Y #K` its K-th item.

        Section numbers are load-bearing: when the breaker list moved from §10.2
        to §10.3 (the retired pledge-pricing compromise was replaced by ADR-005),
        every ticket citing the old anchor silently pointed at the wrong rule.
        The `#K` half is checked against BRD sections only — that is where rule
        lists live and where renumbering is invisible.
        """
        brd = self.brd
        documents = self.vocabulary_docs()
        known: dict[str, Path] = {}
        for doc in documents:
            known[doc.name] = doc
            known[doc.relative_to(ROOT_DIR).as_posix()] = doc
        global_sections = section_headings(brd) | section_headings(
            read_normalized(UNDERSTANDING_FILE)
        )

        problems: list[str] = []
        for path in documents:
            text = read_normalized(path)
            local_sections = section_headings(text)
            where = path.relative_to(ROOT_DIR).as_posix()
            for line in text.splitlines():
                for citation in CITATION_RE.finditer(line):
                    ref, index = citation.group(1), citation.group(2)
                    qualifier = QUALIFIER_RE.search(line[: citation.start()])
                    target = known.get(qualifier.group(1)) if qualifier else None
                    if qualifier and target is None:
                        problems.append(
                            f"{where}: `{qualifier.group(1)}` is not a repo document"
                        )
                        continue
                    if target is not None:
                        if ref not in section_headings(read_normalized(target)):
                            problems.append(
                                f"{where}: §{ref} is not a section of {qualifier.group(1)}"
                            )
                            continue
                        document, cited = target, qualifier.group(1)
                    else:
                        if ref not in global_sections | local_sections:
                            problems.append(f"{where}: §{ref} resolves to no section")
                            continue
                        document, cited = brd, "BRD.md"
                    if index and document == BRD_FILE:
                        items = numbered_items(section_block(brd, ref))
                        if len(items) < int(index):
                            problems.append(
                                f"{where}: {cited} §{ref} #{index} cited but that section "
                                f"declares {len(items)} numbered item(s)"
                            )
        self.assertFalse(
            problems,
            "dangling section citations:\n" + "\n".join(_ascii(line) for line in problems),
        )


if __name__ == "__main__":
    unittest.main()
