"""
services/generation_rules_triggers.py — one-line trigger descriptions
per scanner rule id, used by services/generation_rules.py's condensed
manifest builder.

Extracted from services/generation_rules.py (2026-08-27, mechanical
split — no behaviour change) to keep that module under the platform's
file-size guard.
"""
from __future__ import annotations

# One-line trigger descriptions per rule id. Hand-written because they
# regex source is neither readable nor safe to embed in a prompt (a
# raw regex would encourage them model to try to defeat their pattern)
# rather than internalise their intent).
#
# Rule ids MUST match those emitted by their scanners. If an id is
# missing here their manifest falls back on their scanner's own message.
TRIGGER_ONELINE = {
}