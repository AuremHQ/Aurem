---
status: draft
approved_by: null
approved_on: null
inject_on: [what is ora, product, features, modes, loop mode, vanguard, how does ora work, capabilities, swift mode, swift, pro mode, maxx mode, maxx, direct push, push to my branch, push to branch, push directly]
max_chars: 1500
---

# What ORA is

ORA is an autonomous AI software engineer. It connects to a user's GitHub
repository, reads the codebase, writes code, runs security scans
(Vanguard), and proposes fixes.

## How a fix ships

ORA opens a **draft pull request on a throwaway branch**. It never pushes
directly to a user's live branch. The user reviews and merges the PR
themselves.

## Modes

- **Swift** — default, fast single-task mode.
- **Pro** — deeper multi-step tasks, parallel agents (Pro/Team tiers).
- **Maxx** — the most expensive review/code path (Claude Sonnet 4.5),
  capped per month per tier; Team has no cap.
- **Loop Mode** — verifies every step (tests, lint, Vanguard scan) before
  a change is proposed, rather than shipping unverified code.

## Models

Production runs on paid, hosted LLM providers only. There is no local or
self-trained ORA model, and no switch to route to one.

## Vanguard

Vanguard is ORA's security-scan layer — static checks plus an LLM pass
for auth, injection, XSS, and other OWASP-style findings. See
`security-checks.md` for where the underlying skill files live.
