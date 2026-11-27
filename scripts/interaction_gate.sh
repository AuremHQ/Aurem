#!/usr/bin/env bash
# One fail-closed command shared by local QA and every tracked deploy workflow.
#
# Flags: --backend-only / --frontend-only run a single half; default
# (no flag) runs both, backend first. A backend failure exits
# immediately with the pytest exit code and never touches the frontend
# lane — the fail-closed control in
# tests/test_interaction_reliability_contract.py pins this: a stubbed
# `python` exiting 23 must yield returncode 23 with yarn never invoked.
# The workflow splits the halves into two steps (frontend with
# `if: always()`) so vitest still runs — and its result is visible —
# even when pytest fails.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export INTERACTION_CONTRACT_V1=1
mkdir -p "$ROOT/test_reports/interaction"

RUN_BACKEND=1
RUN_FRONTEND=1
for arg in "$@"; do
  case "$arg" in
    --backend-only) RUN_FRONTEND=0 ;;
    --frontend-only) RUN_BACKEND=0 ;;
    *)
      echo "unknown argument: $arg (expected --backend-only or --frontend-only)" >&2
      exit 2
      ;;
  esac
done

if [ "$RUN_BACKEND" = 1 ]; then
  cd "$ROOT/backend"
  python -m pytest tests/test_interaction_reliability_contract.py \
    --override-ini addopts='' --strict-markers --timeout=30 -q \
    --junitxml="$ROOT/test_reports/interaction/backend.xml"
fi

if [ "$RUN_FRONTEND" = 1 ]; then
  cd "$ROOT/frontend"
  yarn vitest run src/utils/__tests__/interactionReliability.test.jsx \
    --reporter=default --reporter=junit \
    --outputFile.junit="$ROOT/test_reports/interaction/frontend.xml"
fi
