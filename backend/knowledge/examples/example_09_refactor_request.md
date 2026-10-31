---
status: draft
approved_by: null
approved_on: null
task_type: refactor
---

## Task
"This function is doing too much, can you clean it up?"

## Good response pattern
1. Confirm the function's observable behavior must not change —
   a refactor is not a feature change.
2. Keep the diff minimal and reviewable; do not rewrite unrelated code
   in the same file "while you're in there."
3. If existing tests only cover part of the function's behavior, add
   coverage for the uncovered branches before refactoring, not after.
