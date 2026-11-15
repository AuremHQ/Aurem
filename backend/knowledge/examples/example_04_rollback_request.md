---
status: draft
approved_by: null
approved_on: null
task_type: rollback
---

## Task
"Undo the last change to the pricing page."

## Good response pattern
1. Recognize "undo" as a mutation-verb rollback request, same family as
   revert/restore.
2. Confirm which commit/PR is being rolled back before acting — never
   guess which change "the last one" refers to if more than one is
   plausible.
3. Use the repo's existing rollback mechanism rather than hand-writing
   an inverse diff.
