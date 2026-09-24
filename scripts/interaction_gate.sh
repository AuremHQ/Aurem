#!/usr/bin/env bash
# One fail-closed command shared by local QA and every tracked deploy workflow.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export INTERACTION_CONTRACT_V1=1
mkdir -p "$ROOT/test_reports/interaction"
cd "$ROOT/backend"
python -m pytest tests/test_interaction_reliability_contract.py \
  --override-ini addopts='' --strict-markers --timeout=30 -q \
  --junitxml="$ROOT/test_reports/interaction/backend.xml"
cd "$ROOT/frontend"
yarn vitest run src/utils/__tests__/interactionReliability.test.jsx \
  --reporter=default --reporter=junit \
  --outputFile.junit="$ROOT/test_reports/interaction/frontend.xml"