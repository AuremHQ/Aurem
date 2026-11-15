---
status: draft
approved_by: null
approved_on: null
task_type: performance
---

## Task
"The dashboard feels slow when it loads."

## Good response pattern
1. Ask what "slow" means concretely (first paint? a specific API call?)
   if it isn't already clear, rather than guessing a cause.
2. Check for an obvious N+1 query pattern or a synchronous call blocking
   an async path before proposing a fix.
3. Never claim a specific percentage improvement without having
   measured it.
