"""
backend/evals/prompts_agent_behavior.py — 2026-09 P0 bug-brief, item 5.

Deploy-time "agent behavior regression" category, extending the SAME
eval-as-CI mechanism as prompts_quality.py / prompts_security.py (no
parallel system — these run inside the same `runner.run()` and land
in the same `ora_eval_runs` collection / admin tile). Each entry below
targets one of the P0 bug-brief's specific, historically-reproduced
failure modes:

  - Bug 3 (multi-step silent truncation): MULTISTEP_PROMPTS below,
    scored by the new `multistep` scorer in harness.py — every step's
    distinctive keyword must survive into the final reply, even when
    `chat_with_tools()` is capped at max_iters=2 (see runner.py's
    `_run_prompt`) to force the exact budget pressure that used to
    cause a silent "did step 1 only" truncation.
  - Bug 2 (keyword-routing hijack): NOT a declarative prompt here —
    it needs a direct classify_intent()/classify_mode_semantic() call,
    not a full chat_with_tools() turn (mode dispatch happens one layer
    up, in routers/chat/worker.py, before chat_with_tools is ever
    reached). It lives in runner.py as
    `_routing_misclassification_test()` — same offline pattern as the
    existing `_project_scoping_isolation_test()`.
  - Bug 1 (smart-punctuation corruption): NOT testable from this
    backend harness — the bug was a BROWSER-level Safari substitution
    that never reaches the backend at all. It's covered by
    `frontend/src/utils/__tests__/chatTextUtils.preventSmartPunctuation
    .2026_09.test.js`, wired into predeploy_gate.sh Lane 2 as of
    2026-09-22 (it previously was NOT — Lane 2's glob only covered
    src/components/__tests__ and src/lib).
  - Bug 4 (flat timeout / no bounded retry): deliberately NOT included
    yet. A meaningful adversarial case needs a slow-upstream fault-
    injection hook that doesn't exist until Bug 4 ships — a shallow
    prompt here would just be theatre, not a real regression guard.
    Add it here once Bug 4 lands.
"""
from __future__ import annotations

MULTISTEP_PROMPTS = [
    {
        "id": "M1_two_step_versions",
        "category": "multistep",
        "fake_repo": True,
        "prompt": (
            "Do these two things: 1) tell me the exact pinned FastAPI "
            "version from requirements.txt 2) tell me the exact pinned "
            "Vite version from package.json."
        ),
        "scorers": [
            # Subject nouns, not the fact VALUES — a step that gets
            # honestly disclosed as unanswered (Bug 3's max-iters
            # partial-disclosure path restates the step's own wording,
            # it doesn't guess at a value it never confirmed) still
            # mentions "Vite" every time. Only TRUE silent truncation
            # (the step vanishing with zero mention, in any form) fails.
            ("multistep", {"required_keywords": ["FastAPI", "Vite"]}),
        ],
    },
    {
        "id": "M2_three_step_versions",
        "category": "multistep",
        "fake_repo": True,
        "prompt": (
            "List the exact pinned version of each of these, in order: "
            "1) fastapi 2) stripe 3) motor."
        ),
        "scorers": [
            ("multistep", {"required_keywords": ["fastapi", "stripe", "motor"]}),
        ],
    },
    {
        "id": "M3_four_step_versions",
        "category": "multistep",
        "fake_repo": True,
        "prompt": (
            "Please do four things in order and don't skip any: "
            "1) state the pinned fastapi version 2) state the pinned "
            "stripe version 3) state the pinned motor version 4) state "
            "the pinned react version."
        ),
        "scorers": [
            ("multistep", {"required_keywords": ["fastapi", "stripe", "motor", "react"]}),
        ],
    },
]

assert len(MULTISTEP_PROMPTS) == 3, "battery must have exactly 3 prompts"
