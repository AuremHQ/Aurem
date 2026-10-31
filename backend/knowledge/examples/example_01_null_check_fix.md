---
status: draft
approved_by: null
approved_on: null
task_type: fix
---

## Task
"Fix the crash when the user profile loads with no avatar set."

## Good response pattern
1. State the root cause in one sentence (e.g. "`user.avatar` is accessed
   without a null check at the render call site").
2. Propose the minimal guard, not a broad refactor.
3. Name the exact file and line if known from the scanned repo; if not
   confirmed, say so rather than guessing a path.
4. Do not claim the fix is tested until a real gate (lint/test/Vanguard)
   has actually run.
