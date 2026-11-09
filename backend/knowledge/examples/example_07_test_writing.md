---
status: draft
approved_by: null
approved_on: null
task_type: test
---

## Task
"Add a test for the new discount-code endpoint."

## Good response pattern
1. Write the failing test first, against the real endpoint shape (not a
   hand-rolled mock of the function under test).
2. Cover the obvious edge cases: empty code, expired code, already-used
   code, a code belonging to a different user (IDOR), and a
   Mongo-operator-injection-shaped code string.
3. Name it following the repo's own existing test-naming convention,
   not a generic `test_1`.
