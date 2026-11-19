"""
scripts/fix_entitlement_preflight.py — ITEM 1(c), independent review
fix round.

CLI entry point for the stale-fix_entitlement pre-flight report.
Connects to the real MONGO_URL/DB_NAME (same env vars every other
script/test in this repo uses), wraps the handle in a WriteGuardedDB,
scans dev_users for the stale shape (see
services.fix_entitlement.find_stale_entitlements's own docstring), and
writes a CSV + prints the summary. Writes NOTHING to any real
collection — enforced structurally (find_stale_entitlements refuses a
raw db handle), not just by convention.

This is a DISCOVERY tool only. It does not revoke anything. See
docs/CUTOVER_TOKEN_BILLING.md for the cutover step this report feeds
into.

Usage:
    python3 scripts/fix_entitlement_preflight.py [--out PATH]

Default output path is a scratch file, never anything under version
control or a real backup location.
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
        from services.fix_entitlement import find_stale_entitlements

        return await find_stale_entitlements(guarded)

    return await run_with_guarded_db(_work)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="/tmp/fix_entitlement_preflight.csv",
        help="CSV output path (default: a scratch file, never version-controlled)",
    )
    args = parser.parse_args()
    report = asyncio.run(_run())
    if report is None:
        return 1

    from services.plan_transition import rows_to_csv
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(rows_to_csv(report["rows"]))

    print(
        f"[fix_entitlement_preflight] scanned {report['scanned']} "
        f"fix_entitlement=True row(s), found {report['count']} stale "
        f"-> {args.out}",
    )
    print(json.dumps({"scanned": report["scanned"], "count": report["count"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
