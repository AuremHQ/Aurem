---
status: draft
approved_by: null
approved_on: null
task_type: explain
---

## Task
"How does a fix actually get from my chat message into my repo?"

## Good response pattern
1. Describe the real flow at a high level: chat intent is classified,
   a fix is proposed, it goes through verification gates, then ships as
   a draft PR on a throwaway branch.
2. Never say "commits to your branch" — ORA never pushes directly.
3. If a specific internal module/file name isn't confirmed from an
   approved fact, describe the behavior instead of guessing the name.
