"""
scripts/seed_harness_page_content.py — one-off / occasional seed of
the /harness page's content into the private AuremHQ/aurem-loop repo.

Run manually (`python scripts/seed_harness_page_content.py`) whenever
the Phase A copy needs to be created or updated. Never imported by
the running app — the request-serving path only ever READS via
services.harness_content_repo.fetch_harness_page_content().

Every number/claim below is taken verbatim from the approved
"verified facts" (Aurem Loop Developer Paper, 2026-11-10) and the
founder's follow-up corrections (demo video, clones/views refresh,
waitlist email). Nothing here is invented.
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, ".")

from services.harness_content_repo import push_harness_page_content  # noqa: E402

DEMO_VIDEO_URL = (
    "https://customer-assets-v7afamib.emergentagent.net/"
    "job_1b6bb0b1-4adc-4fcf-acd0-64257fdcd80e/artifacts/"
    "1c1756af7f866bd2_VID-20261110-WA0006.mp4"
)

CONTENT = {
    "title": "Aurem Loop — local coding harness",
    "description": (
        "A local coding harness that checks its own work. Free to use, "
        "MIT licensed — not available to download yet."
    ),
    "canonical": "https://auremcto.com/harness",
    "badges": ["FREE", "LOCAL", "MIT LICENSED"],
    "hero": {
        "headline": "Your code. Your PC. Your Accept.",
        "subhead": (
            "A local coding harness with weight-space refinement for your "
            "small model — and a phone view to use it from anywhere."
        ),
        "body": (
            "Aurem Loop is a free coding harness built around a tiny 1.5B "
            "local model (qwen2.5-coder:1.5b via Ollama). After one-time "
            "setup it runs on your own PC with no per-token bill, and "
            "nothing touches your code until you press Accept."
        ),
    },
    "phaseA": {
        "banner": (
            "Not available to download yet. The release is being "
            "prepared. When it goes live, the download link and its "
            "SHA-256 will appear right here."
        ),
        "waitlistEmail": "polarisbuiltinc@gmail.com",
        "waitlistSubject": "Aurem Loop waitlist",
        "waitlistBody": (
            "Please add me to the Aurem Loop waitlist. One email when "
            "the download goes live, nothing else."
        ),
    },
    "featureTags": [
        "$0 for local use",
        "Windows — supported & tested path",
        "~1 GB model · runs on CPU",
        "No account, no subscription",
        "Phone view via optional tunnel",
    ],
    "demoVideo": {
        "url": DEMO_VIDEO_URL,
        "caption": (
            "Watch one run end to end: describe a task, see the diff, "
            "press Accept — then the weight-space refinement view. "
            "Illustration of the flow, not a real recording — the "
            "harness is not downloadable yet."
        ),
    },
    "setApart": {
        "headline": "Refine your model. Use it from your phone.",
        "wspKit": {
            "badge": "EXPERIMENTAL · OFF BY DEFAULT",
            "body": (
                "wsp-kit edits a small slice of your model's own weights, "
                "gated by an evaluation: an edit is kept only if it gains "
                "at least +2 points on the target behaviour and loses no "
                "more than 2 points elsewhere. Under the previous, "
                "stricter +5 gate, 2 of 7 attempts passed. The harness's "
                "own weight-edit mode has not produced a verified edit."
            ),
        },
        "phoneTunnel": {
            "badge": "OPTIONAL",
            "body": (
                "An optional Cloudflare Tunnel lets you open the harness "
                "from your phone. While it's on, screen content passes "
                "through Cloudflare; the link and access token are yours "
                "and are never shared."
            ),
        },
    },
    "githubStats": {
        "repoLabel": "wsp-kit — real traction on GitHub",
        "note": (
            "These numbers are for the wsp-kit research repo, not "
            "harness downloads — the harness itself hasn't shipped."
        ),
        "clones": 939,
        "views": 562,
        "lastVerified": "10 Nov 2026",
    },
    "whatItIsNot": [
        "Not a replacement for large frontier-model tools. It is weaker on hard tasks.",
        (
            "Not a hosted service — there's no cloud account, and nothing "
            "runs on Aurem's servers unless you explicitly choose a "
            "subscriber cloud option, and that option is not yet available."
        ),
        (
            "It does not edit your model's weights inside the harness yet. "
            "That is a separate research tool (wsp-kit) and an "
            "off-by-default mode that has not produced a verified edit."
        ),
    ],
    "compare": {
        "headline": "Local coding agents: what to check before you choose",
        "body": (
            "Aurem Loop trades raw accuracy for cost, privacy, and "
            "control: it runs for $0 on your own machine, asks before "
            "every change, and keeps a rollback — check these columns "
            "against any tool you're considering."
        ),
        "auremRow": {
            "Price": "$0 (local)",
            "Works offline": "After setup",
            "Model": "qwen2.5-coder:1.5b",
            "Asks before changing code": "Yes — Accept / Reject, rollback",
            "Platform": "Windows",
            "Evidence": "Tie with bare model (26/48 vs 27/48, false accepts 6)",
        },
        "competitorNote": (
            "Aider · Codex CLI · Other tools — competitor details will be "
            "filled in from their official documentation on the day this "
            "page is published, with a source link for each fact. Nothing "
            "is guessed here in the meantime."
        ),
    },
    "whoShouldNotPick": [
        "You need a large frontier-model tool for hard, multi-file tasks — this harness's small local model is weaker on those.",
        "You're not on Windows — there's no install for other platforms yet.",
        "You need a security-audited tool — Aurem Loop has not been through an independent security audit.",
        "You want the highest possible pass rate over local-first operation — the harness ties, not beats, the bare model on the bench above.",
    ],
    "usersSay": {
        "headline": "What users say — once there are users",
        "body": (
            "Aurem Loop has not shipped yet, so there are no user quotes "
            "to show. Rather than invent testimonials, this section stays "
            "empty until the first real users have run it. When they do, "
            "a short case study and their own words will appear here."
        ),
    },
    "setup": {
        "headline": "Setup on Windows — about 10 minutes",
        "steps": [
            "Download the release zip from this page (the link appears once Phase B goes live) and verify its SHA-256 checksum, shown here too.",
            "Unzip it, then run install.bat.",
            "The installer checks for Python and installs Ollama plus the qwen2.5-coder:1.5b model (about 1 GB) if they're missing.",
            "Open http://127.0.0.1:8899 in your browser — that's the harness's front screen.",
            "Describe your first task in plain English.",
            "Review the diff, then press Accept or Reject.",
        ],
    },
    "troubleshooting": [
        {
            "q": "The page shows \"Checking model...\" forever",
            "a": (
                "Ollama isn't running yet, or the qwen2.5-coder:1.5b "
                "model hasn't finished downloading. Open a terminal and "
                "run `ollama list` to confirm the model is present, then "
                "restart the harness."
            ),
        },
        {
            "q": "The page shows \"Disconnected\"",
            "a": (
                "The front screen lost its connection to the local "
                "harness process. Make sure the harness window/process "
                "is still running and that nothing else is using port "
                "8899, then refresh http://127.0.0.1:8899."
            ),
        },
        {
            "q": "A run looks stuck",
            "a": (
                "Large tasks can take longer on CPU. Give it a minute — "
                "if nothing changes after that, press Reject to cancel "
                "the step safely; nothing is applied to your files until "
                "you press Accept."
            ),
        },
    ],
    "faq": [
        {
            "q": "Is Aurem Loop free?",
            "a": "Yes. The full local harness is free with a signup — no payment required.",
        },
        {
            "q": "Does it send my code anywhere?",
            "a": (
                "The local harness runs against a small model on your own "
                "machine. It does not send your code to Aurem's servers "
                "unless you explicitly choose the subscriber cloud "
                "option, and that option is not yet available."
            ),
        },
        {
            "q": "What does it actually do to my files?",
            "a": (
                "It plans a task, has a local model write one small step "
                "at a time, and verifies each step with real checks "
                "before keeping it. It never deletes files and never "
                "pushes to a remote repo on its own."
            ),
        },
        {
            "q": "Does Aurem Loop get smarter over time?",
            "a": (
                "It has a gated self-improvement step (wsp-kit) that "
                "only ever touches its own local model. As of today it "
                "has not produced any real weight changes."
            ),
        },
        {
            "q": "What's included for free vs. as a subscriber?",
            "a": (
                "The local harness itself is the same for everyone. A "
                "subscriber additionally unlocks a cloud model option — "
                "that option is locked for free accounts and is not yet "
                "available even for subscribers."
            ),
        },
        {
            "q": "What platforms does it support?",
            "a": "Windows. There is no install for other platforms yet.",
        },
        {
            "q": "How do I uninstall it?",
            "a": (
                "Run the uninstaller the installer places alongside the "
                "harness, or remove the install folder and its scheduled "
                "task manually."
            ),
        },
    ],
    "footer": {
        "tagline": "Aurem Loop – a free local coding harness by Aurem.",
        "legal": (
            "Aurem is a registered tradename of Polaris Built Inc., "
            "based in Ontario, Canada."
        ),
        "waitlistEmail": "polarisbuiltinc@gmail.com",
        "license": "MIT licensed · Licence terms are included in the download.",
    },
}


async def main() -> None:
    result = await push_harness_page_content(
        CONTENT,
        commit_message="Seed/update /harness Phase A page content",
    )
    print("Pushed:", result)


if __name__ == "__main__":
    asyncio.run(main())
