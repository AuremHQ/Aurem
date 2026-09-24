# Scaling Plan — First Bottleneck at 10x Load (v2#7)

**Status:** Lightweight, honest documentation of what would actually break first, based on reading the current running configuration — not an invented capacity model, not a load-test result. Where a number below wasn't directly measured, it is marked **[ASSUMPTION]**.

_Last updated: 2026-09-22_

## 1. Current confirmed configuration (evidence)

| Component | Confirmed setting | Source |
|---|---|---|
| Backend process model | 1 uvicorn worker, `--workers 1`, `--reload` | `/etc/supervisor/conf.d/*.conf`, `program:backend` |
| Mongo connection pool | `maxPoolSize=50`, single `AsyncIOMotorClient` per process | `main.py:352-354` |
| Outbound LLM call concurrency | **No app-level semaphore/admission-control found anywhere in `services/llm/*`.** Every concurrent chat turn opens its own `httpx.AsyncClient` call with a per-call timeout (5–30s depending on provider); there is no shared queue or cap. | `services/llm/openrouter_client.py`, `services/llm/groq_client.py`, `services/llm/jev_client.py` (grepped for `Semaphore` — zero hits) |
| LLM provider fallback | Multi-provider fallback chain exists (OpenRouter → Groq → Jev, etc.) for *reliability*, not for *rate-limit load-shedding* — a fallback triggers on a failed/slow primary, not on "primary is at capacity, spread load". | `services/llm/openrouter_providers.py` |

## 2. What breaks first at 10x concurrent users — reasoning, not a benchmark

Given the above, the ordering of what saturates first, from most to least likely:

1. **Upstream LLM provider rate limits (429s) — most likely first bottleneck.**
   Every chat turn is a direct, unthrottled outbound call. At 10x concurrent turns, AUREM's own process has no internal admission control to smooth a burst — it will simply fire 10x as many simultaneous provider calls. The existing retry/fallback logic (Bug 4 work, in progress separately) reduces the *user-visible* damage of a 429, but it does not prevent the burst from hitting the provider in the first place. This is a **provider-side** ceiling, not an AUREM capacity design choice, and it is the cheapest one to hit because it requires no change in AUREM's own request pattern — just more concurrent users.

2. **Single uvicorn worker's event loop — second most likely.**
   One process, one event loop. Async I/O (Mongo, HTTP) doesn't block the loop, but any CPU-bound work in the request path (regex-heavy orchestrator parsing, JSON tool-call extraction, the hallucination/citation scorers) executes serially on that one loop. At 10x concurrent turns, these small CPU slices start queuing behind each other, adding latency to *every* concurrent request rather than failing outright. **[ASSUMPTION]** — not measured; this only becomes the *dominant* bottleneck once the provider-rate-limit ceiling above is fixed (e.g., by adding an internal queue), since right now the provider ceiling is hit first.

3. **Mongo connection pool (`maxPoolSize=50`) — unlikely to be first.**
   Typical Mongo ops here (session/task-chain logging, project lookups) are short-lived reads/writes, not long-held connections. 50 concurrent sockets comfortably covers 10x today's traffic unless a single slow aggregation (e.g., an unindexed `task_chains` scan) holds a connection open under load. **[ASSUMPTION]** — no current query has been profiled under 10x load; flagged as a check-first item if #1 and #2 are addressed and the bottleneck moves further down the stack.

## 3. What this plan is NOT
- Not a promise that the app currently handles 10x load gracefully — it does not have a queue/backpressure layer, and this document does not build one.
- Not based on an actual load test run in this sandbox (no load-testing tool was run for this investigation).
- Not a claim that horizontal scaling (`--workers N`, multiple pods) has been tested — it has not; the current deployment is single-worker by measured configuration.

## 4. If/when this needs to be acted on
The cheapest first fix, in order, would be: (a) an application-level `asyncio.Semaphore` around outbound LLM calls per provider to turn an ungraceful 429 burst into a graceful internal queue, before (b) considering `--workers N` horizontal scale-out, before (c) touching the Mongo pool size. None of this is implemented as part of this document — this is a plan, not a change.
