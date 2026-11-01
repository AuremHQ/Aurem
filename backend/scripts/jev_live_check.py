#!/usr/bin/env python3
"""
scripts/jev_live_check.py — Robot Fix List STEP 4.

One-shot live probe for Jev (services/llm/jev_client.py). Reads
EMERGENT_LLM_KEY from the environment, NEVER prints it, and makes one
real `ask_choice` call. Exits:
  0  "JEV LIVE OK" — a real label came back.
  1  "JEV LIVE FAILED: <reason>" — key present, call failed.
  2  "NOT TESTED (no key)" — EMERGENT_LLM_KEY not set; this script
     never ran a real call, so this is NOT a pass or a fail.

Usage: python3 scripts/jev_live_check.py
"""
from __future__ import annotations

import asyncio
import os
import sys


async def _main() -> int:
    if not os.environ.get("EMERGENT_LLM_KEY"):
        print("NOT TESTED (no key)")
        return 2
    from services.llm.jev_client import ask_choice
    label, meta = await ask_choice(
        state="Chat message to a coding assistant: 'fix the login bug'",
        instructions="Classify the SINGLE best-fit category for this message.",
        criteria={"A": "general chat", "B": "a concrete bug-fix request"},
        timeout=10.0,
    )
    if label is None:
        print(f"JEV LIVE FAILED: {meta.get('error', 'unknown')!s}")
        return 1
    print(f"JEV LIVE OK — label={label!r} model={meta.get('model')!r}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
