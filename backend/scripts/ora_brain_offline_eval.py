#!/usr/bin/env python3
"""
scripts/ora_brain_offline_eval.py — ORA Brain Part 3, Phase F.

Offline eval harness. NO live LLM call, NO production traffic, no
writes. Runs 16 representative task strings through
services.knowledge_injector's selection + build_answer_context logic
at off vs on, and checks:
  - off is always byte-empty ("", [])
  - the right knowledge file is selected for "on" per task's keywords
  - the fixed "say unknown, never invent" rule is present only when on

Knowledge files on disk ship `status: draft` until the founder
approves them (backend/knowledge/*.md) — nothing is ever auto-
approved. This script patches `_load_file` IN-PROCESS ONLY (never
touches disk) to simulate "approved" for the duration of this run, so
it evaluates the SELECTION LOGIC itself, not today's actual
draft-gated production behavior (which currently selects nothing, by
design, until a human approves a file).

Run: python3 scripts/ora_brain_offline_eval.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.knowledge_injector as ki

SAMPLE_TASKS: list[tuple[str, str | None]] = [
    ("What is ORA and how does Loop Mode work?", "product.md"),
    ("How much does the Pro plan cost per month?", "pricing.md"),
    ("The user hit a null pointer exception, what's the standard fix pattern?", "common-fixes.md"),
    ("What security checks does vanguard run for SQL injection?", "security-checks.md"),
    ("What tier includes unlimited Maxx tasks?", "pricing.md"),
    ("Explain how Swift mode differs from Pro mode", "product.md"),
    ("What's our XSS mitigation pattern for repo-derived strings?", "common-fixes.md"),
    ("Does ORA ship a direct push to my branch?", "product.md"),
    ("How do I upgrade my subscription tier?", "pricing.md"),
    ("What's a totally unrelated question about the weather today?", None),
    # F6 — 6 additional phrasings added to close the original 3 gaps
    # and broaden coverage past the first 10's keyword shapes.
    ("What are ORA's product features and capabilities overall?", "product.md"),
    ("Will ORA ever push directly to my live branch without a PR?", "product.md"),
    ("What's included in Maxx mode pricing and the monthly cost?", "pricing.md"),
    ("The app crashed with an undefined variable, what's the fix?", "common-fixes.md"),
    ("How do you sanitize untrusted repo-derived strings against injection?", "common-fixes.md"),
    ("Where do Vanguard's security checks live in the codebase?", "security-checks.md"),
]


def _force_all_approved_in_memory() -> None:
    """Wraps _load_file so every returned front-matter dict reports
    status=approved, without ever writing to the real .md files on
    disk. Idempotent — safe to call more than once in the same
    process (e.g. once per test in a pytest session that imports
    this script repeatedly): wraps the ORIGINAL lru_cache'd
    `_load_file` exactly once, never re-wraps an already-wrapped
    version (which would otherwise accumulate nested closures and
    break `.cache_clear()` on the second call)."""
    real_load = getattr(ki, "_real_load_file_for_eval", None) or ki._load_file
    ki._real_load_file_for_eval = real_load

    def _approved_load(filename: str):
        meta, body = real_load(filename)
        if meta:
            meta = {**meta, "status": "approved"}
        return meta, body

    if hasattr(ki._load_file, "cache_clear"):
        ki._load_file.cache_clear()
    ki._load_file = _approved_load  # type: ignore[assignment]


async def _run_one(task: str, expected_file: str | None) -> tuple[bool, bool, bool]:
    os.environ.pop("ORA_KNOWLEDGE_MODE", None)
    off_block, off_files = await ki.build_answer_context("evaluser", None, task)
    off_ok = off_block == "" and off_files == []

    os.environ["ORA_KNOWLEDGE_MODE"] = "on"
    on_block, on_files = await ki.build_answer_context("evaluser", None, task)
    os.environ.pop("ORA_KNOWLEDGE_MODE", None)

    selected_ok = (expected_file in on_files) if expected_file else (on_files == [])
    unknown_rule_ok = "unknown" in on_block.lower()
    return off_ok, selected_ok, unknown_rule_ok


async def main() -> int:
    _force_all_approved_in_memory()
    print(f"{'task':<60s}{'expected':<16s}{'off_ok':<8s}{'selected_ok':<13s}{'unknown_rule_ok':<16s}")
    failures = 0
    for task, expected_file in SAMPLE_TASKS:
        off_ok, selected_ok, unknown_rule_ok = await _run_one(task, expected_file)
        row_ok = off_ok and selected_ok and unknown_rule_ok
        if not row_ok:
            failures += 1
        print(
            f"{task[:58]:<60s}{expected_file!s:<16s}{off_ok!s:<8s}"
            f"{selected_ok!s:<13s}{unknown_rule_ok!s:<16s}"
        )
    print(f"\n{len(SAMPLE_TASKS) - failures}/{len(SAMPLE_TASKS)} sample tasks correct.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
