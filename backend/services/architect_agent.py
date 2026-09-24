"""
services/architect_agent.py — Architect Agent core (2026-09-13, Phase 2
of the "Make ORA Genuinely Autonomous" ticket, approved in 2 phases).

Runs ONCE, before code generation, when a user describes a NEW
build/feature (not a previously-scoped fix) — infers a real,
project-specific technical blueprint from the raw idea text via an
LLM call (genuine reasoning, NOT a hardcoded keyword→template map:
two structurally different ideas must produce structurally different
blueprints), then persists it to `project_blueprints` so later turns
reuse the settled decisions instead of re-inferring/drifting.

Logging convention mirrors `services/task_chain_logging.py` /
`services/ora_chat_v2/audit.py`: best-effort, never raises, one
record per event, appended to `architect_events`.

Events emitted (per the ticket):
  architect_agent_invoked            — inference started
  architect_agent_blueprint_written  — blueprint persisted, with the
                                        `project_blueprints` doc id
  type_a_decision_made               — one per FINAL Type A decision
                                        (after the deterministic
                                        override pass below)
  type_b_decision_deferred           — one per FINAL Type B decision
  type_b_decision_resolved           — a deferred Type B item later
                                        resolved by the user

2026-09-13 — Phase 3 (approved with one required change): the LLM
call that infers the blueprint ALSO proposes an initial Type A/B
classification per concrete decision it made (efficient — no second
LLM pass), but that classification is NEVER the final authority for
financial/legal/irreversible categories. `apply_type_b_overrides()`
below is a deterministic, code-level second pass — independent of
the model — that force-reclassifies ANY decision matching a known
Type-B category (payment, domain, branding, pricing, legal/financial,
irreversible) to Type B, even if the LLM called it Type A. This
mirrors Phase 1's principle (a deterministic check on top of model
behavior, never trusting the model alone) and directly answers the
ticket's "not an implicit judgment call buried in a prompt" line: a
model that just invented a requirement has no independent incentive
to correctly flag its own payment/domain/pricing decision as needing
human input, so the code — not the prompt — is the final authority
for those specific categories.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Optional

from services.llm import call_llm

logger = logging.getLogger(__name__)

BLUEPRINT_SCHEMA_VERSION = 1

_ARCHITECT_SYS_PROMPT = """You are ORA's Architect Agent. Given a short, often one-line, product idea, infer a genuine technical blueprint for THIS SPECIFIC idea by reasoning about what it actually implies — not by following a generic checklist. Two different ideas MUST produce substantively different blueprints: different entities, different infra flags, different notification needs, different non-scope items. If you find yourself writing the same infra/data-model shape regardless of the idea, you are doing this wrong.

Rules:
- Only infer auth if the idea genuinely implies user accounts/ownership/access control. Many ideas (a single-purpose tool, a stateless converter) need NO auth — say so plainly.
- Only infer cache/Redis, a vector store/RAG, or background jobs if the idea's phrasing genuinely implies that need (e.g. high concurrent read volume on shared state implies caching; "search my documents"/"chat with my notes" implies a vector store; a scheduled/delayed/bulk operation implies background jobs). Do NOT default to enabling infra "to be safe" — absence of a real signal means false.
- Only infer notifications if the idea implies an event a user would want to be told about asynchronously (booking confirmed, price dropped, etc). A tool with no such event needs none.
- data_model.entities should reflect the REAL nouns of this idea, not generic placeholders like "User"/"Item" unless that's genuinely all the idea needs.
- non_scope should list concrete things a reasonable engineer would explicitly defer for THIS idea, each with a one-line reason grounded in the idea (not generic boilerplate).
- decisions: list EVERY concrete decision you just made above (e.g. "auth type = email/password", "cache = enabled") as its own entry. Classify each as Type A (the system can decide without asking — internal/reversible: DB field names, cache usage, REST vs GraphQL, default email provider, folder structure) or Type B (a real human choice that must NEVER be silently decided — payment provider, custom domain, branding/colors, pricing tiers, any financial/legal/irreversible-without-user-input decision). For every Type B decision, also propose a `safe_default` — something the build can proceed with immediately that is EXPLICITLY not-yet-live (e.g. "payment: none configured, placeholder checkout button" — never a real/default provider).

Output ONLY strict JSON, no prose, no markdown fences, matching exactly this shape:
{
  "auth": {"needed": bool, "type": string|null, "reasoning": string},
  "data_model": {"entities": [{"name": string, "fields": [string], "persistence": string}], "reasoning": string},
  "concurrency": {"applicable": bool, "description": string|null},
  "notifications": {"needed": bool, "channels": [string], "trigger_events": [string]},
  "infra": {"cache_or_redis": bool, "vector_store_or_rag": bool, "background_jobs": bool, "reasoning": string},
  "non_scope": [{"item": string, "reason": string}],
  "decisions": [{"field": string, "value": string, "type": "A"|"B", "reasoning": string, "safe_default": string|null}]
}"""

_REQUIRED_TOP_KEYS = (
    "auth", "data_model", "concurrency", "notifications", "infra", "non_scope",
)

# 2026-09-13 — Phase 3 code-level Type-B override. These are the exact
# categories the ticket named as "must never be silently decided" —
# kept as an explicit, auditable list rather than a vague "ask an LLM
# if this is risky" second call, so the override itself can never be
# talked out of firing by clever phrasing.
_TYPE_B_CATEGORIES: dict[str, list[str]] = {
    "payment": [
        "payment", "stripe", "paypal", "razorpay", "checkout",
        "billing provider", "payment provider", "payment gateway",
    ],
    "domain": [
        "custom domain", "domain name", "dns", "subdomain", "domain",
    ],
    "branding": [
        "brand color", "branding", "color scheme", "color palette",
        "logo design", "visual identity", "brand identity", "brand",
    ],
    "pricing": [
        "pricing tier", "price tier", "subscription price",
        "plan price", "pricing plan", "pricing model", "pricing", "price",
    ],
    "legal_financial": [
        "terms of service", "privacy policy", "compliance",
        "tax rate", "refund policy", "legal disclaimer",
    ],
    "irreversible": [
        "irreversible", "permanently delete", "data deletion policy",
        "account closure", "cannot be undone",
    ],
}


def _type_b_category_match(text: str) -> Optional[str]:
    low = (text or "").lower().replace("_", " ").replace("-", " ")
    for category, keywords in _TYPE_B_CATEGORIES.items():
        for kw in keywords:
            if kw in low:
                return category
    return None


def apply_type_b_overrides(decisions: list[dict]) -> list[dict]:
    """Deterministic, code-level second pass over the LLM's OWN Type
    A/B classification — independent of it, and the final authority
    for the categories above. Any decision whose field/value text
    matches a known Type-B category is force-reclassified to Type B
    regardless of what the model called it. Never overrides
    A -> unrelated (only ever tightens A -> B, never loosens B -> A).

    Deliberately matches ONLY `field` + `value`, NOT `reasoning`:
    live-tested and confirmed the model's free-text reasoning for an
    UNRELATED decision (e.g. "notifications needed" reasoned as
    "users need to be informed about subscription and PAYMENT
    events") can mention a Type-B word in passing, which would
    false-positive-flip a genuinely fine Type A decision to Type B if
    reasoning were included. Matching only what is actually BEING
    decided (field/value) avoids that."""
    out = []
    for d in decisions or []:
        d = dict(d)
        combined = f"{d.get('field','')} {d.get('value','')}"
        category = _type_b_category_match(combined)
        original_type = d.get("type")
        if category and original_type != "B":
            d["type"] = "B"
            d["override_applied"] = True
            d["override_reason"] = (
                f"code-level override: matched Type-B category "
                f"'{category}' (LLM had classified this as Type "
                f"{original_type or '?'})"
            )
            if not d.get("safe_default"):
                d["safe_default"] = "not yet configured — pending your input"
        else:
            d["override_applied"] = False
        out.append(d)
    return out


async def log_architect_event(
    db, *, event_type: str, project_id: Optional[str], user_id: Optional[str],
    idea: Optional[str] = None, blueprint_id: Optional[str] = None,
    decision: Optional[dict] = None, error: Optional[str] = None,
) -> None:
    """Best-effort — appends ONE record to `architect_events`. Never
    raises (logging must never break the caller)."""
    if db is None:
        return
    try:
        await db.architect_events.insert_one({
            "event_type": event_type,
            "project_id": project_id,
            "user_id": user_id,
            "idea": (idea or "")[:300] if idea else None,
            "blueprint_id": blueprint_id,
            "decision": decision,
            "error": (error or "")[:500] if error else None,
            "ts": time.time(),
        })
    except Exception as e:  # noqa: BLE001
        logger.debug("log_architect_event failed (non-blocking): %r", e)


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def parse_blueprint_json(reply: str) -> Optional[dict]:
    """Strict-ish JSON parse of the model's reply. Returns None (not
    an exception) on any structural failure so the caller can decide
    whether to retry — never half-trusts a malformed blueprint."""
    if not reply:
        return None
    candidate = _strip_fences(reply)
    try:
        data = json.loads(candidate)
    except Exception:
        # Fallback: grab the first {...} span in case the model added
        # stray prose despite the instruction not to.
        m = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except Exception:
            return None
    if not isinstance(data, dict):
        return None
    if not all(k in data for k in _REQUIRED_TOP_KEYS):
        return None
    data.setdefault("decisions", [])
    if not isinstance(data.get("decisions"), list):
        data["decisions"] = []
    return data


async def infer_blueprint(idea: str, *, max_tokens: int = 2000) -> tuple[Optional[dict], Optional[str]]:
    """REAL LLM call (no hardcoded template) — returns (blueprint, error).
    One retry on unparseable output; gives up after that (caller must
    treat a None blueprint as a hard failure, never fabricate one)."""
    if not (idea or "").strip():
        return None, "empty idea"
    user_msg = f"IDEA: {idea.strip()}"
    for attempt in (1, 2):
        try:
            reply = await call_llm(
                messages=[{"role": "user", "content": user_msg}],
                system=_ARCHITECT_SYS_PROMPT, max_tokens=max_tokens,
                temperature=0.3,
            )
        except Exception as e:
            return None, f"architect LLM call failed: {e}"
        bp = parse_blueprint_json(reply or "")
        if bp is not None:
            return bp, None
        if attempt == 1:
            user_msg = (
                f"IDEA: {idea.strip()}\n\nYour previous reply was not "
                f"valid JSON matching the required shape. Output ONLY "
                f"the JSON object, nothing else."
            )
            continue
    return None, "architect could not produce a parseable blueprint after retry"


async def write_blueprint(
    db, *, project_id: str, user_id: str, idea: str, blueprint: dict,
) -> str:
    """Upserts the CURRENT blueprint for a project (one active doc per
    project_id, versioned) into `project_blueprints`. Returns the
    blueprint's own id (a stable uuid, independent of Mongo's ObjectId,
    so callers/tests never need to deal with BSON serialization).

    `blueprint["decisions"]` is expected to already have gone through
    `apply_type_b_overrides()` (done by `invoke_architect` before this
    is called) — `type_b_pending` is derived straight from it so the
    admin panel and this collection never disagree."""
    blueprint_id = uuid.uuid4().hex[:16]
    now = time.time()
    existing = await db.project_blueprints.find_one(
        {"project_id": project_id}, {"_id": 0, "version": 1},
    )
    version = (existing.get("version") if existing else 0) + 1
    decisions = blueprint.get("decisions") or []
    type_b_pending = [
        {
            "field": d.get("field"), "value": d.get("value"),
            "safe_default": d.get("safe_default"),
            "resolved": False, "resolution": None,
        }
        for d in decisions if d.get("type") == "B"
    ]
    doc = {
        "project_id": project_id,
        "user_id": user_id,
        "blueprint_id": blueprint_id,
        "schema_version": BLUEPRINT_SCHEMA_VERSION,
        "version": version,
        "idea": idea,
        "blueprint": blueprint,
        "type_b_pending": type_b_pending,
        "created_at": now,
        "updated_at": now,
    }
    await db.project_blueprints.update_one(
        {"project_id": project_id},
        {"$set": doc},
        upsert=True,
    )
    await log_architect_event(
        db, event_type="architect_agent_blueprint_written",
        project_id=project_id, user_id=user_id, idea=idea,
        blueprint_id=blueprint_id,
    )
    return blueprint_id


async def get_blueprint(db, *, project_id: str) -> Optional[dict]:
    """Read-only fetch of the CURRENT persisted blueprint for a
    project — the admin 'What I set up' panel and any future turn
    that wants to reuse settled decisions both read through here
    rather than re-inferring."""
    if db is None:
        return None
    doc = await db.project_blueprints.find_one(
        {"project_id": project_id}, {"_id": 0},
    )
    return doc


async def invoke_architect(
    db, *, project_id: str, user_id: str, idea: str,
) -> tuple[Optional[dict], Optional[str]]:
    """Top-level entry point: logs invocation, infers, applies the
    deterministic Type-B override, logs one event per FINAL decision,
    persists. Returns (blueprint_doc_or_None, error_or_None)."""
    await log_architect_event(
        db, event_type="architect_agent_invoked",
        project_id=project_id, user_id=user_id, idea=idea,
    )
    blueprint, err = await infer_blueprint(idea)
    if err or blueprint is None:
        await log_architect_event(
            db, event_type="architect_agent_invoked",
            project_id=project_id, user_id=user_id, idea=idea,
            error=err or "unknown inference failure",
        )
        return None, err

    # Phase 3 — deterministic override pass, independent of the LLM's
    # own classification. This can only tighten A -> B, never loosen.
    blueprint["decisions"] = apply_type_b_overrides(blueprint.get("decisions") or [])
    for d in blueprint["decisions"]:
        event_type = ("type_b_decision_deferred" if d.get("type") == "B"
                      else "type_a_decision_made")
        await log_architect_event(
            db, event_type=event_type, project_id=project_id,
            user_id=user_id, idea=idea, decision=d,
        )

    blueprint_id = await write_blueprint(
        db, project_id=project_id, user_id=user_id, idea=idea,
        blueprint=blueprint,
    )
    return {
        "blueprint_id": blueprint_id,
        "project_id": project_id,
        "idea": idea,
        "blueprint": blueprint,
    }, None


async def resolve_type_b_decision(
    db, *, project_id: str, field: str, resolution: str, user_id: Optional[str] = None,
) -> bool:
    """A previously-deferred Type B item (e.g. "payment provider")
    gets a real answer from the user. Updates the pending entry
    in-place on the CURRENT blueprint doc and logs
    `type_b_decision_resolved`. Returns False if no matching pending
    item was found (no-op, not an error — caller decides how to
    surface that)."""
    doc = await db.project_blueprints.find_one(
        {"project_id": project_id}, {"_id": 0, "type_b_pending": 1},
    )
    if not doc:
        return False
    pending = doc.get("type_b_pending") or []
    found = False
    for item in pending:
        if item.get("field") == field and not item.get("resolved"):
            item["resolved"] = True
            item["resolution"] = resolution
            found = True
            break
    if not found:
        return False
    await db.project_blueprints.update_one(
        {"project_id": project_id},
        {"$set": {"type_b_pending": pending, "updated_at": time.time()}},
    )
    await log_architect_event(
        db, event_type="type_b_decision_resolved", project_id=project_id,
        user_id=user_id, decision={"field": field, "resolution": resolution},
    )
    return True
