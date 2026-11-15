---
status: draft
approved_by: null
approved_on: null
task_type: fix
---

## Task
"A comment field renders raw HTML from user input."

## Good response pattern
1. Identify this as stored XSS, not just a display bug.
2. Propose escaping/sanitizing on render (or on write, if that's the
   codebase's existing convention) rather than inventing a new pattern.
3. Flag that any existing test asserting the raw-HTML behavior is
   testing the bug, not the feature, and must be corrected rather than
   deleted.
