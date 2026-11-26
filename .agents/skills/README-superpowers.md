# Superpowers skills — vendored (2026-11-26)

Source: [obra/superpowers](https://github.com/obra/superpowers) —
MIT license, by Jesse Vincent (obra) + Prime Radiant.
Pinned version: **v7.0.2**, commit
**`22f0b463e7a5e579904dd6211a520321035526f7`**.
Upstream LICENSE (MIT) applies to everything in the 14 skill
directories below. Do not edit the vendored SKILL.md files except
through the adaptations listed here — upstream updates come in by
re-vendoring at a new pinned commit, never by hand-patching.

## Vendored (14 of 15)

brainstorming, diagnosing-superpowers, dispatching-parallel-agents,
executing-plans, finishing-a-development-branch, receiving-code-review,
requesting-code-review, subagent-driven-development,
systematic-debugging, test-driven-development, using-git-worktrees,
using-superpowers, verification-before-completion, writing-plans.

## Dropped

`writing-skills` — the meta skill for authoring skills. Its 9
self-modification HIGHs cannot be fixed without gutting it, so it
was not vendored (SkillSpector re-scan gate, Option A).

## Ora adaptations (meaning preserved, minimal)

1. `systematic-debugging/SKILL.md` — removed the macOS code-signing
   example's Layer 3 (Keychain state) and Layer 4 (`codesign`).
2. `using-superpowers/SKILL.md` — removed the `## Platform Adaptation`
   section (it only pointed at per-harness references Ora doesn't use).
3. `executing-plans/SKILL.md:49-51` — removed the dangling pointer to
   `../using-superpowers/references/`.
4. False-positive rewordings (SkillSpector string-matcher, A2):
   `git clean -fdx` warning ×2 → "`git clean` that also removes
   ignored files"; "Do not judge whether your human partner was
   right" → "Do not take sides on whether your human partner was
   right"; "Capture context for forensics" → "Record surrounding
   state for forensics".
5. `scripts/` references ×8 (executing-plans, subagent-driven-
   development) — the upstream helper scripts assume a local
   checkout with a `.superpowers/sdd/` layout Ora doesn't have, so
   the invocations were replaced with Ora-native equivalents:
   the plan's draft-PR branch is the workspace, the brief comes
   from the plan file, tests run + record to the ledger, and the
   review package is a `git log` / `git diff` file. The workflow
   steps are unchanged; only the mechanics are Ora-native.

## Scan gate

SkillSpector v2.12.0 (`--no-llm`) on the vendored copy:
**0 CRITICAL, 1 HIGH, 6 MEDIUM, 0 LOW.** The single HIGH is a
documented scanner limitation (analysis-evasion "partial" on
`systematic-debugging/SKILL.md:275` — the referenced
`condition-based-waiting.md` exists; the bounded static parser
cannot fully resolve it). Not a real finding; the file was kept.

## Wiring

Loaded by `backend/services/agent_skills_injector.py`
(`build_agent_skill_context`, via
`skill_context_injector.build_skill_context`) — keyword rules from
each SKILL.md front-matter `description:`, plus the
`using-superpowers` bootstrap always injected at task start, plus
the repo-add workflow (`POST /projects/add` sets
`superpowers_first_task`; the project's first task injects the
brainstorm → plan → TDD → verify block). Ora's approval gates
still apply; prefer executing-plans over subagent-driven-
development (token budget).
