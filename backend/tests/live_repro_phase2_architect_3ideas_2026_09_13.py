"""
FRESH, LIVE inference test — 3 genuinely different, slightly unusual
ideas through the REAL `infer_blueprint()` (real LLM call, no mocks),
run 2026-09-13. Per the user's explicit requirement: one example
("coffee shop booking app") is exactly what a hardcoded template would
handle well, so this does NOT stop there — it also runs:
  2. "a single-page tool that converts CSV to JSON" — no obvious
     auth/data-model need at all.
  3. "an app where thousands of people might check the same live
     leaderboard at once" — infra signal (caching) buried in phrasing,
     the words "Redis"/"cache" never appear in the idea text.

Prints all 3 blueprints side by side. If they look structurally
identical with just words swapped, that's a template, not inference.
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.architect_agent import infer_blueprint

IDEAS = [
    "a coffee shop booking app where customers reserve a table for a specific time slot",
    "a single-page tool that converts CSV to JSON",
    "an app where thousands of people might check the same live leaderboard at once",
]


async def main():
    results = {}
    for idea in IDEAS:
        print(f"\n{'='*70}\nIDEA: {idea}\n{'='*70}")
        bp, err = await infer_blueprint(idea)
        if err:
            print("ERROR:", err)
            results[idea] = {"error": err}
            continue
        print(json.dumps(bp, indent=2))
        results[idea] = bp

    print(f"\n\n{'#'*70}\nSTRUCTURAL DIVERGENCE CHECK\n{'#'*70}")
    auth_needed = {idea: r.get("auth", {}).get("needed") for idea, r in results.items()}
    infra = {idea: r.get("infra", {}) for idea, r in results.items()}
    n_entities = {idea: len(r.get("data_model", {}).get("entities", [])) for idea, r in results.items()}
    print("auth.needed per idea:      ", auth_needed)
    print("infra flags per idea:")
    for idea, i in infra.items():
        print(f"  {idea[:50]:50s} -> cache={i.get('cache_or_redis')} vector={i.get('vector_store_or_rag')} jobs={i.get('background_jobs')}")
    print("entity count per idea:     ", n_entities)

if __name__ == "__main__":
    asyncio.run(main())
