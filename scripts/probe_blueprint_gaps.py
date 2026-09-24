"""Read-only audit observations, NOT acceptance tests and NOT release approval.
Run: PYTHONPATH=backend python scripts/probe_blueprint_gaps.py
No external requests, credentials, database writes, or deployment actions.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

from services.input_normalizer import normalize_input
from services import semantic_mode_router as router


async def main():
    code = '```js\nconst label = "a—b";\n```'
    normalized = normalize_input(code)
    with patch.object(router, "is_enabled", return_value=True), \
         patch.object(router, "_in_canary", return_value=True), \
         patch.object(router, "_get_session_state", return_value={"current_mode": "E", "pending_mode": None, "streak": 0}), \
         patch.object(router, "_ask_jev_for_mode", new=AsyncMock(return_value=None)):
        fallback = await router.classify_mode_semantic(
            "What is the audit status?", f12_payload=None, project_id=None,
            session_id="read-only-audit-probe",
        )
    print(json.dumps({
        "environment": "sandbox; mocked classifier timeout; no production traffic",
        "normalizer": {"input": code, "observed": normalized, "literal_preserved": normalized["text"] == code},
        "classifier_timeout_with_prior_E": fallback,
        "status": "OPEN findings, not fixes",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())