"""Kailash Nadh-style fast, zero-dependency invariant test runner.

Runs all invariant tests in < 300ms, printing a concise execution summary.
Returns exit code 0 on success, 1 on failure (ideal for git hooks & CI).
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

TESTS_DIR = Path(__file__).resolve().parent


def main() -> int:
    print("=" * 70)
    print("🛡️  UVEST — FROZEN SPECIFICATION & PROMPT INVARIANT TEST SUITE")
    print("=" * 70)

    start_time = time.perf_counter()

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(TESTS_DIR), pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    print("-" * 70)
    print(f"⏱️  Execution Time: {elapsed_ms:.1f}ms")
    print(f"📊 Total Tests: {result.testsRun} | Passed: {result.testsRun - len(result.failures) - len(result.errors)} | Failures: {len(result.failures)} | Errors: {len(result.errors)}")

    if result.wasSuccessful():
        print("✅ ALL INVARIANTS & DECISION GATES PASSED.")
        print("=" * 70)
        return 0
    else:
        print("❌ INVARIANT TESTS FAILED. CAPITAL RISK DETECTED.")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
