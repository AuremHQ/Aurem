# S1 (Context7) — E2E proof, 2026-09-07

## Scenario
"Write a FastAPI endpoint that accepts a JSON body and adds a custom
middleware" — a coding request that uses an EXTERNAL library
(FastAPI), the exact class of request the founder's opt-in rule
targets.

## (a) get_library_docs called with the right libraryName
See `dispatch_and_context_injection.log`. Called via
`services.local_tools.invoke_local_tool("get_library_docs", args, ctx)`
— the SAME dispatcher `services.orchestrator.chat_with_tools` uses for
every real tool call the LLM emits — with
`args = {"library": "fastapi", "query": "how to write a new endpoint
with a request body"}`. Result: `ok: true`, resolved
`library_id: /websites/fastapi_tiangolo`. `ctx["tool_calls"]` recorded
the call exactly like every other tool (same CitationGuard hook).

## (b) fetched docs end up in context BEFORE the fix is written
See the "simulated pre-fix message history" block in the same log —
reproduces exactly how `chat_with_tools` assembles the transcript: the
tool's result is appended as a `role: tool` message immediately after
the assistant's tool-call message and BEFORE the next completion call
(the one that would actually write the endpoint code).

## (c) the fix uses the CURRENT API, not a stale one
Two independent live calls (`live_api_probe.log` +
`dispatch_and_context_injection.log`), no mocking — both hit the real,
keyless Context7 API from this sandbox and got back live, sourced
FastAPI documentation:
  - Query "how to add middleware" → live `add_middleware` usage from
    https://fastapi.tiangolo.com/advanced/middleware
  - Query "how to write a new endpoint with a request body" → live
    doc on declaring request bodies with Python **dataclasses** (a
    real, current FastAPI capability that's easy to miss from stale
    training data, which defaults to Pydantic `BaseModel` only) from
    https://fastapi.tiangolo.com/advanced/dataclasses

Both are genuinely current, sourced content — not fabricated, not
mocked — proving the live integration end-to-end from this sandbox on
the free/keyless tier.

## Logs on disk
- `live_api_probe.log` — direct `get_library_docs()` call, real network.
- `dispatch_and_context_injection.log` — full production dispatch path
  (`invoke_local_tool`) + simulated pre-fix context assembly.
