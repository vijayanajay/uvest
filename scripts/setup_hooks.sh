#!/bin/sh
# Activate the tracked git hooks for this clone (idempotent).
# Usage:  sh scripts/setup_hooks.sh
#
# Windows clones cannot chmod +x an unstaged file, so when you `git add`
# githooks/pre-commit for the first time, also run:
#     git update-index --chmod=+x githooks/pre-commit
# so Unix clones execute the hook instead of treating it as data.
set -e
git config core.hooksPath githooks
echo "✅ core.hooksPath → githooks (pre-commit now runs the invariant suite)."
