"""
services/fix_items.py — facade for the single "things to fix" layer.

Pure model: `fix_items_model`.  Persistence over `cto_open_findings`
(additive, no renames): `fix_items_store`.  Everything callers need is
re-exported here so `from services import fix_items as fi` keeps working.
See the module docstrings of the two halves for the design rules.
"""
from services.fix_items_model import *            # noqa: F401,F403
from services.fix_items_model import (            # noqa: F401  (private helpers used by tests/callers)
    _is_live, _first, _now,
)
from services.fix_items_store import *            # noqa: F401,F403
