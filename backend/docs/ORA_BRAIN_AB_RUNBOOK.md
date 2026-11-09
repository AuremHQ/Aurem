# ORA Brain — Manual A/B Runbook

For the founder to run by hand on a real test project. This is NOT
automated — `ORA_KNOWLEDGE_MODE` stays `off` in every deployed
environment until you decide otherwise; this runbook is how you
personally judge whether `on` is worth flipping.

## What you're comparing

- **Off** (today's default): ORA answers with no L1 knowledge
  facts, no approved examples beyond what the council retriever
  already does, no project notes, no "say unknown" rule.
- **On**: the same question, but ORA's prompt also carries any
  approved `backend/knowledge/*.md` facts that match, your approved
  project memory notes (once you've let it accumulate some), and a
  fixed instruction to say "unknown" rather than invent a fact.

## Prerequisites

1. At least one file in `backend/knowledge/` has `status: approved`
   (you flip this by hand — nothing in the codebase does it for
   you). An all-draft knowledge directory means `on` behaves almost
   identically to `off` for L1 facts.
2. A test project you don't mind ORA answering real questions about.

## Running one A/B round

1. Pick a real bug or question on your test project — something
   with a knowable right answer (a specific price, a specific past
   fix, a specific "don't do X" rule).
2. With `ORA_KNOWLEDGE_MODE` unset (off), ask ORA the question.
   Save the reply.
3. Set `ORA_KNOWLEDGE_MODE=on` for your own session (env var on
   your local/staging instance — never flip it in the shared
   production environment without reviewing the "open founder
   decisions" list in the Part 2/3 reports first).
4. Ask the EXACT same question again. Save the reply.
5. Compare. You're specifically looking for:
   - Did "on" correctly cite an approved fact that "off" missed or
     guessed at?
   - Did "on" correctly say "unknown" instead of inventing a price,
     date, or promise that "off" fabricated?
   - Did "on" leak anything from the PROJECT NOTES block as if it
     were an instruction (it shouldn't — that block is explicitly
     labeled untrusted; flag it immediately if you see this)?
6. Set `ORA_KNOWLEDGE_MODE` back to unset/off when you're done.

## Weekly note (keep this running, don't overwrite)

Each week, add two lines below — one mistake the "on" layer actually
prevented, and one gap you noticed it should cover but doesn't yet
(e.g. a fact with no approved file, a keyword phrasing that didn't
match any `inject_on` list — see
`scripts/ora_brain_offline_eval.py`'s own output for a starting
list of known keyword-coverage gaps).

- `YYYY-MM-DD` — prevented: _______. gap to add: _______.

## Rollback

`ORA_KNOWLEDGE_MODE` unset (or any value other than `shadow`/`on`)
is always off — there is no other switch to flip back.
