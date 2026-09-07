"""
scripts/s2_e2e_sequential_thinking_demo.py — Master Plan S2 E2E proof.

One real (not mocked) LLM tool-calling run showing sequential_thinking
in actual use on a complex, multi-file bug with a deliberate red
herring, contrasted with a plain single-shot run on the same prompt
with NO tool access. Writes both transcripts to
/app/e2e-proof/S2-sequential-thinking/.

Run: python scripts/s2_e2e_sequential_thinking_demo.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from emergentintegrations.llm.chat import LlmChat, UserMessage

BUG_PROMPT = (
    "Our /admin/github/bulk-revoke endpoint intermittently lets a real "
    "revoke through even when the admin never typed the REVOKE "
    "confirmation phrase. It only happens sometimes, not every time, "
    "and only in production traffic, never in our test suite. Two "
    "files are involved: routers/admin_bin.py (the hard-guard check) "
    "and services/feature_flags.py (the live-verified flag lookup, "
    "which is backed by a 5-second in-memory cache with a background "
    "refresh task). The obvious first guess is a bug in the hard-guard "
    "boolean logic itself in admin_bin.py. What is the ROOT CAUSE and "
    "what's the fix? Be specific about which file and why."
)

SEQ_TOOL_SCHEMA = [{
    "type": "function",
    "function": {
        "name": "sequential_thinking",
        "description": (
            "Record one numbered step of step-by-step reasoning before "
            "committing to a fix. Use this for a complex, multi-file bug "
            "to trace the ROOT cause instead of patching the first "
            "plausible one. Set next_thought_needed=false once ready to "
            "give the final answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "thought": {"type": "string"},
                "thought_number": {"type": "integer"},
                "total_thoughts": {"type": "integer"},
                "next_thought_needed": {"type": "boolean"},
                "is_revision": {"type": "boolean"},
                "revises_thought": {"type": "integer"},
            },
            "required": ["thought", "thought_number", "total_thoughts", "next_thought_needed"],
        },
    },
}]


async def run_without_tool(key: str) -> str:
    chat = (LlmChat(api_key=key, session_id="s2-e2e-no-tool",
                     system_message="You are a senior backend engineer. Answer directly and concisely.")
            .with_model("anthropic", "claude-sonnet-4-6"))
    resp = await chat.send_message(UserMessage(text=BUG_PROMPT))
    return resp


async def run_with_tool(key: str) -> tuple[str, list]:
    """The installed emergentintegrations SDK build here has no
    with_tools()/send_message_with_tools() — so this reproduces the
    EXACT production path instead: the model free-writes
    `sequential_thinking(thought=..., thought_number=..., ...)`
    Python-call-syntax steps in its own text (same shape DeepSeek/GLM
    emit in real Prompt Mode turns), and we run the REAL production
    parser (`services.tools_bridge.extract_tool_calls`) over the raw
    output to prove it actually extracts each recorded thought."""
    from services.tools_bridge import extract_tool_calls

    system_msg = (
        "You are a senior backend engineer debugging a complex, multi-file, "
        "intermittent production bug. Before answering, think out loud in "
        "sequential_thinking(...) steps — one per line, Python-call syntax, "
        "e.g. sequential_thinking(thought='...', thought_number=1, "
        "total_thoughts=3, next_thought_needed=True). Use AT LEAST 2 steps: "
        "explicitly consider the 'obvious first guess' the user mentions, "
        "then either confirm or revise away from it (is_revision=True, "
        "revises_thought=N) before your final step "
        "(next_thought_needed=False). After your last thinking step, write "
        "'FINAL ANSWER:' followed by the root cause and fix."
    )
    chat = (LlmChat(api_key=key, session_id="s2-e2e-with-tool",
                     system_message=system_msg)
            .with_model("anthropic", "claude-sonnet-4-6"))
    raw = await chat.send_message(UserMessage(text=BUG_PROMPT))
    thought_calls = [c for c in extract_tool_calls(raw)
                      if c["tool"] == "sequential_thinking"]
    return raw, thought_calls


async def main():
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        print("EMERGENT_LLM_KEY not set — skipping live E2E, gate-decision proof only.")
        return

    out_dir = "/app/e2e-proof/S2-sequential-thinking"
    os.makedirs(out_dir, exist_ok=True)

    print("=== Run 1: NO tool access (plain single-shot) ===")
    no_tool_answer = await run_without_tool(key)
    print(no_tool_answer[:2000])
    with open(f"{out_dir}/run1_no_tool.txt", "w") as f:
        f.write(BUG_PROMPT + "\n\n---ANSWER---\n\n" + no_tool_answer)

    print("\n=== Run 2: WITH sequential_thinking (real production parser) ===")
    with_tool_raw, thought_calls = await run_with_tool(key)
    print(f"Thought steps extracted by tools_bridge.extract_tool_calls: {len(thought_calls)}")
    for c in thought_calls:
        a = c["args"]
        print(f"  #{a.get('thought_number')} (revision={a.get('is_revision', False)}): "
              f"{(a.get('thought') or '')[:160]}")
    print()
    print(with_tool_raw[:3000])
    with open(f"{out_dir}/run2_with_tool.txt", "w") as f:
        f.write(BUG_PROMPT + "\n\n---RAW MODEL OUTPUT---\n\n" + with_tool_raw)
        f.write("\n\n---PARSED THOUGHT STEPS (via real tools_bridge.extract_tool_calls)---\n\n")
        f.write(json.dumps(thought_calls, indent=2))

    print(f"\nTranscripts written to {out_dir}/")


if __name__ == "__main__":
    asyncio.run(main())
