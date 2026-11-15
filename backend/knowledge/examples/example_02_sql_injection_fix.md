---
status: draft
approved_by: null
approved_on: null
task_type: fix
---

## Task
"The search endpoint builds a query by string-formatting the search
term directly into SQL."

## Good response pattern
1. Name the vulnerability class (SQL injection) explicitly.
2. Propose a parameterized query / ORM builder replacement, not a
   regex-based "sanitizer" band-aid.
3. Note that the fix must not change the endpoint's existing response
   shape, only how the query is built.
