"""Shared test harness for UVEST pipeline stage scripts.

The stage seams test two different contracts:

* **Behavior contract** — given a payload/text, what does the stage emit?
  Verified thousands of times per session; spawning a fresh interpreter per
  case costs ~200 ms each and made the suite 5 s. Imported directly, the
  same assertions run in microseconds.
* **CLI contract** — the one frozen invocation shape BRD §9.1 spells out
  (`python scripts/N_foo.py --input … > artifact.json`). Exactly one test
  per stage exercises it via a real subprocess, so a vendored import trick
  or a broken shebang/exit-code path can never go unnoticed.

Import-safety requirement that makes this workable: every stage script MUST
keep all work inside functions and invoke `main()` only behind
`if __name__ == "__main__"`. `import_mode()` guards this with a re-import
and asserts the script never writesstream output into stdout on import.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # tests/support → repo root
SCRIPTS_DIR = REPO_ROOT / "scripts"

CWD = REPO_ROOT  # subprocess invocations run from the repo root, like git hooks


def import_mode(stage: int):
    """Import `scripts/{stage}_*.py` as a module without running its main().

    Returns (module, script_path). Raises AssertionError if the script is
    missing or if importing it wrote to stdout (which would mean the script
    runs work at import time — a forbidden pattern; stage scripts must be
    pure libraries with a main() guard).
    """
    candidates = sorted(SCRIPTS_DIR.glob(f"{stage}_*.py"))
    if not candidates:
        raise AssertionError(f"no stage-{stage} script found in {SCRIPTS_DIR}")
    script = candidates[0]

    spec = importlib.util.spec_from_file_location(
        f"uvest_stage{stage}_${script.stem}", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, script


def call_imported_main(module, argv: list[str]):
    """Run the script's main() in-process with argv swapped, capturing stdio.

    Returns (payload_or_None, returncode, stderr_text). stdout is captured and
    attempted to parse as JSON; if main() does not emit JSON (e.g. it exited
    early on stderr), stdout is returned as raw text via payload=None and the
    caller asserts on returncode/stderr.
    """
    saved_argv, saved_stdout, saved_stderr = sys.argv, sys.stdout, sys.stderr
    sys.argv = ["prog"] + argv
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    code = 0
    try:
        sys.stdout, sys.stderr = stdout_buf, stderr_buf
        # SystemExit from `sys.exit(...)` inside main() is expected control flow.
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            try:
                module.main()
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.argv, sys.stdout, sys.stderr = saved_argv, saved_stdout, saved_stderr
    out = stdout_buf.getvalue()
    try:
        payload = json.loads(out) if out.strip() else None
    except json.JSONDecodeError:
        payload = None if not out.strip() else out
    return payload, code, stderr_buf.getvalue()


def subprocess_mode(script: Path, argv: list[str]) -> subprocess.CompletedProcess:
    """Run the script exactly as the canonical pipeline does.

    One invocation per stage per suite run — the *only* subprocess each stage
    seam is allowed, guarding the CLI contract (exit codes, stderr codes,
    stdout-only-JSON on success).
    """
    return subprocess.run(
        [sys.executable, str(script), *argv],
        capture_output=True, text=True, encoding="utf-8", check=False, cwd=CWD,
    )


@contextlib.contextmanager
def temp_input(text_or_payload, suffix: str = ".txt"):
    """Write a temp input file (raw text or JSON payload) and yield its path."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    ) as f:
        if isinstance(text_or_payload, (dict, list)):
            json.dump(text_or_payload, f)
        else:
            f.write(text_or_payload)
        path = f.name
    try:
        yield path
    finally:
        Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------- convenience
def run_stage0_text(raw_text: str):
    """Stage 0 behavior test helper: import-and-run on a raw paste.

    Returns (payload|None, returncode, stderr).
    """
    module, _ = import_mode(0)
    with temp_input(raw_text, ".txt") as path:
        return call_imported_main(module, ["--input", path])


def run_stage1_json(payload: dict):
    """Stage 1 behavior test helper: import-and-run on a canonical payload."""
    module, _ = import_mode(1)
    with temp_input(payload, ".json") as path:
        return call_imported_main(module, ["--input", path])
