"""Kailash Nadh-style fast, zero-dependency parallel invariant test runner.

Runs all invariant test suites across multiple CPU cores using stdlib
parallelization, reducing test suite latency from > 5 minutes down to < 1 minute.
Returns exit code 0 on success, 1 on failure (ideal for git hooks, pre-commit & CI).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent


def _run_single_suite(stem: str) -> tuple[str, int, int, int, int, float, str]:
    """Runs a single test module via subprocess; returns (stem, code, total, fails, errs, dur, raw)."""
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", f"tests.{stem}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    dur = time.perf_counter() - t0
    raw = (proc.stderr or "") + "\n" + (proc.stdout or "")

    m_ran = re.search(r"Ran (\d+) tests? in", raw)
    count = int(m_ran.group(1)) if m_ran else 0

    fails, errs = 0, 0
    m_fail = re.search(r"FAILED \((?:failures=(\d+))?(?:, )?(?:errors=(\d+))?\)", raw)
    if m_fail:
        fails = int(m_fail.group(1) or 0)
        errs = int(m_fail.group(2) or 0)
    elif proc.returncode != 0:
        errs = 1

    return stem, proc.returncode, count, fails, errs, dur, raw


def run_parallel(test_files: list[str], max_workers: int) -> int:
    start_time = time.perf_counter()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_run_single_suite, test_files))

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    total_tests = sum(r[2] for r in results)
    total_fails = sum(r[3] for r in results)
    total_errs = sum(r[4] for r in results)
    total_passed = total_tests - total_fails - total_errs
    all_ok = all(r[1] == 0 for r in results)

    for stem, code, count, fails, errs, dur, raw in sorted(results, key=lambda x: x[0]):
        status = "✅ PASS" if code == 0 else "❌ FAIL"
        print(f"  {status} {stem:<35} | {dur:>6.2f}s | {count:>3} tests")
        if code != 0:
            print("\n" + "=" * 50 + f" FAILURE IN {stem} " + "=" * 50)
            print(raw.strip())
            print("=" * (100 + len(stem)) + "\n")

    print("-" * 70)
    print(f"⏱️  Execution Time: {elapsed_ms:.1f}ms ({elapsed_ms / 1000.0:.2f}s)")
    print(
        f"📊 Total Tests: {total_tests} | Passed: {total_passed} | Failures: {total_fails} | Errors: {total_errs}"
    )

    if all_ok:
        print("✅ ALL INVARIANTS & DECISION GATES PASSED.")
        print("=" * 70)
        return 0
    else:
        print("❌ INVARIANT TESTS FAILED. CAPITAL RISK DETECTED.")
        print("=" * 70)
        return 1


def run_serial() -> int:
    start_time = time.perf_counter()

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(TESTS_DIR), pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    print("-" * 70)
    print(f"⏱️  Execution Time: {elapsed_ms:.1f}ms")
    print(
        f"📊 Total Tests: {result.testsRun} | Passed: {result.testsRun - len(result.failures) - len(result.errors)} | Failures: {len(result.failures)} | Errors: {len(result.errors)}"
    )

    if result.wasSuccessful():
        print("✅ ALL INVARIANTS & DECISION GATES PASSED.")
        print("=" * 70)
        return 0
    else:
        print("❌ INVARIANT TESTS FAILED. CAPITAL RISK DETECTED.")
        print("=" * 70)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fast zero-dependency parallel invariant test runner."
    )
    parser.add_argument(
        "--serial",
        action="store_true",
        help="Run tests sequentially in a single process.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(8, max(2, os.cpu_count() or 4)),
        help="Number of parallel worker processes (default: up to 8).",
    )
    args = parser.parse_args(argv)

    print("=" * 70)
    print("🛡️  UVEST — FROZEN SPECIFICATION & PROMPT INVARIANT TEST SUITE")
    print("=" * 70)

    if args.serial:
        return run_serial()

    test_files = sorted([f.stem for f in TESTS_DIR.glob("test_*.py")])
    return run_parallel(test_files, max_workers=args.workers)


if __name__ == "__main__":
    sys.exit(main())
