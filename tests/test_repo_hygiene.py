"""Repository hygiene invariants for `.gitignore`.

Guards the line between two kinds of JSON in this repo:

* **Generated per-run artifacts** (`out_*.json`, `current_trade.json`,
  `thresholds.json`, `uvest_history.db`, generated reports) — must never be
  committed, or the suite starts asserting against stale local state.
* **Spec artifacts** (the 50-trade eval dataset, recorded OHLCV fixtures, the
  pinned `skills-lock.json`) — must always be committable, or a fresh clone
  cannot reproduce a single evaluation.

A blanket `*.json` rule silently breaks the second kind, so it is banned here.
Ignore semantics are resolved by git itself (parent-directory exclusions and
negation ordering both matter, and neither is reproducible with a hand-rolled
matcher): every path is checked in a single `git check-ignore` invocation to
keep this file compatible with the fast, offline test runner.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
GITIGNORE_FILE = ROOT_DIR / ".gitignore"

# Generated per recommendation — must never be tracked.
IGNORED_PATHS = [
    "current_trade.json",
    "out_1_math.json",
    "out_2_market.json",
    "out_11_postmortem.json",
    "thresholds.json",
    "run_record.json",
    "uvest_history.db",
    "dhan_cache.db",
    "data_cache/nse_bhavcopy.csv",
    "reports/report_RELIANCE_20261009.html",
    ".venv/lib/python3.11/site-packages/anything.json",
]

# Frozen spec artifacts — losing any of these breaks reproducibility.
COMMITTABLE_PATHS = [
    "tests/evals/golden_dataset.json",
    "tests/fixtures/out_2_market.json",
    "tests/evals/replay/RELIANCE_2022.json",
    "skills-lock.json",
    "reports/sample_report.html",
]


def resolve_ignored(git: str, paths: list[str]) -> set[str]:
    """Return the subset of paths the repo's ignore rules exclude, in one call.

    NUL delimiters on both stdin and stdout are deliberate: line-delimited text
    mode lets Windows newline translation append a stray CR to every path, which
    silently makes file-level ignore rules appear not to match.
    """
    proc = subprocess.run(
        [git, "check-ignore", "-z", "--stdin"],
        cwd=ROOT_DIR,
        input="\0".join(paths).encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if proc.returncode not in (0, 1):  # 0 = some ignored, 1 = none ignored
        raise AssertionError(
            "git check-ignore failed (rc=%d): %s"
            % (proc.returncode, proc.stderr.decode("utf-8", "replace").strip())
        )
    return {p.decode("utf-8") for p in proc.stdout.split(b"\0") if p}


class TestRepoHygiene(unittest.TestCase):
    """Ignored artifacts stay uncommittable; spec artifacts stay committable."""

    @classmethod
    def setUpClass(cls):
        git = shutil.which("git")
        if not git:
            raise unittest.SkipTest("git executable not available")
        if not GITIGNORE_FILE.exists():
            raise AssertionError(f".gitignore not found at {GITIGNORE_FILE}")
        cls.ignored = resolve_ignored(git, IGNORED_PATHS + COMMITTABLE_PATHS)

    def test_generated_artifacts_are_ignored(self):
        """Local run outputs, caches, and databases must never reach version control."""
        for rel_path in IGNORED_PATHS:
            self.assertIn(
                rel_path, self.ignored, f"generated artifact is trackable: {rel_path}"
            )

    def test_spec_artifacts_are_committable(self):
        """The eval dataset, fixtures, and lockfile must survive a fresh clone."""
        for rel_path in COMMITTABLE_PATHS:
            self.assertNotIn(
                rel_path, self.ignored, f"spec artifact is hidden by a rule: {rel_path}"
            )

    def test_canonical_database_names_are_covered(self):
        """AGENTS.md rule 3 names both SQLite stores explicitly."""
        for name in ("uvest_history.db", "dhan_cache.db"):
            self.assertIn(name, self.ignored, f"SQLite store would be committed: {name}")

    def test_gitignore_ends_with_a_newline(self):
        """A missing final terminator corrupts the last rule when anything is appended.

        The final rule here is a negation (`!skills-lock.json`), so appending
        without a terminator would silently re-ignore the skill lockfile.
        """
        raw = GITIGNORE_FILE.read_bytes()
        self.assertTrue(raw.endswith(b"\n"), ".gitignore is not newline-terminated")

    def test_gitignore_has_no_blanket_or_stale_rules(self):
        """Locks out the specific regressions this file exists to prevent."""
        patterns = [
            line.strip()
            for line in GITIGNORE_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        self.assertNotIn(
            "*.json",
            patterns,
            "a blanket *.json rule hides the eval dataset and test fixtures",
        )
        self.assertFalse(
            [p for p in patterns if p.startswith("screen_")],
            "dead screen_* rules remain from the deleted screener pipeline",
        )
        self.assertFalse(
            [p for p in patterns if "univest_history" in p],
            "misspelled cache rule: the canonical database is uvest_history.db",
        )


if __name__ == "__main__":
    unittest.main()
