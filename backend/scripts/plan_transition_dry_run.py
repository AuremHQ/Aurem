"""
scripts/plan_transition_dry_run.py — Master Prompt v2 PART 6.1.

CLI entry point for the dry-run tool. Connects to the real
MONGO_URL/DB_NAME (same env vars every other script/test in this repo
uses), wraps the handle in a WriteGuardedDB, runs the classifier over
every dev_users row, and writes a CSV + prints the summary. Writes
NOTHING to any real collection — see services.plan_transition_write_guard
and services.plan_transition.run_dry_run's own docstrings for exactly
how that's enforced structurally, not just by convention.

Usage:
    python3 scripts/plan_transition_dry_run.py [--out PATH]

Default output path is a scratch file, never anything under version
control or a real backup location (that's a separate, human decision
— see the PART 6 report's rollback-plan section).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
try:
    from _cli import run_with_guarded_db
except ImportError:  # imported as backend.scripts.<name>
    from backend.scripts._cli import run_with_guarded_db


async def _run() -> dict | None:
    async def _work(guarded):
        from services.plan_transition import run_dry_run

        return await run_dry_run(guarded)

    return await run_with_guarded_db(_work)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="/tmp/plan_transition_dry_run.csv",
        help="CSV output path (default: a scratch file, never version-controlled)",
    )
    args = parser.parse_args()
    report = asyncio.run(_run())
    if report is None:
        return 1

    from services.plan_transition import rows_to_csv
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(rows_to_csv(report["rows"]))

    print(f"[plan_transition_dry_run] wrote {len(report['rows'])} rows -> {args.out}")
    print(json.dumps(report["summary"], indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
