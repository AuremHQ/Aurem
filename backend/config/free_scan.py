"""
config/free_scan.py — Master Prompt v2 PART 4.1, the one free full
scan every newly-connected repo gets.

FREE_SCAN_COST_CAP_USD is the per-scan spend ceiling. The 16 scanner
categories (services/codebase_health_core.py::SCANNERS) are static/
regex-based with no LLM calls today, so real per-scan cost is near
zero — this cap exists as a forward-looking guard (a future category
that calls a paid API must still respect it) and is deliberately
enforced in services/free_scan_results.py regardless, rather than
assumed to never matter because today's categories happen to be free.
"""
from __future__ import annotations

import os

FREE_SCAN_COST_CAP_USD: float = float(os.getenv("FREE_SCAN_COST_CAP_USD", "1.00"))

# Forward-looking per-category cost estimate used only to decide
# whether running ANOTHER category would exceed the cap above. Scaled
# by repo size (files scanned) since the dominant real cost in this
# pipeline is the upfront GitHub fetch, not the (free) category
# functions themselves — see services/free_scan_results.py.
ESTIMATED_COST_PER_FILE_PER_CATEGORY_USD: float = float(
    os.getenv("FREE_SCAN_EST_COST_PER_FILE_PER_CATEGORY_USD", "0.0001"),
)

# A completed scan costing more than this multiple of the rolling
# average of recent completed scans raises `cost_alarm` in the result
# doc (surfaced to admins, never blocks the scan itself).
COST_ALARM_MULTIPLE: float = float(os.getenv("FREE_SCAN_COST_ALARM_MULTIPLE", "5.0"))
