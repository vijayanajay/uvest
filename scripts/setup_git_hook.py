"""Kailash Nadh-style zero-friction pre-commit hook installer.

Installs a lightweight pre-commit hook in .git/hooks/pre-commit to automatically
execute the test suite on every git commit.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parent.parent
HOOK_DIR = ROOT_DIR / ".git" / "hooks"
PRE_COMMIT_FILE = HOOK_DIR / "pre-commit"

HOOK_CONTENT = """#!/bin/sh
# 🛡️ Uvest Pre-Commit Invariant & Prompt Safety Gate
# Kailash Nadh-style fast local test verification (< 300ms)

echo ""
echo "🛡️  Running Uvest invariant tests on prompt.md & decision engine..."

python tests/run_all_tests.py

RESULT=$?

if [ $RESULT -ne 0 ]; then
    echo ""
    echo "❌ COMMIT BLOCKED: Invariant tests failed."
    echo "   Ensure prompt.md contracts and trade gating logic are strictly maintained."
    echo ""
    exit 1
fi

echo "✅ All invariants verified. Proceeding with commit."
echo ""
exit 0
"""


def install_hook() -> bool:
    if not (ROOT_DIR / ".git").exists():
        print(f"❌ Error: {ROOT_DIR} is not a git repository.")
        return False

    HOOK_DIR.mkdir(parents=True, exist_ok=True)
    PRE_COMMIT_FILE.write_text(HOOK_CONTENT, encoding="utf-8")

    # Set executable permissions
    try:
        current_stat = os.stat(PRE_COMMIT_FILE)
        os.chmod(PRE_COMMIT_FILE, current_stat.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass

    print(f"✅ Pre-commit hook successfully installed at: {PRE_COMMIT_FILE}")
    print("   Every 'git commit' will now automatically run the invariant test suite in < 300ms.")
    return True


if __name__ == "__main__":
    success = install_hook()
    sys.exit(0 if success else 1)
