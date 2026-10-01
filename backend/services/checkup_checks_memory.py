"""services/checkup_checks_memory.py — Check Library v2 layer 0 registration (L0.1)."""
from __future__ import annotations

from services.project_memory import check_project_memory

CHECKS = [
    ("launch", "project_memory", check_project_memory),
]
