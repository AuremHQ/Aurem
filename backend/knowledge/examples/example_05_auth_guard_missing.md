---
status: draft
approved_by: null
approved_on: null
task_type: fix
---

## Task
"Any logged-in user can view another user's project settings by
changing the id in the URL."

## Good response pattern
1. Name this as an IDOR (insecure direct object reference), not a
   generic "bug."
2. Propose scoping the query by the AUTHENTICATED user's id (from the
   session/token), never from a client-supplied id in the path or body.
3. Point to the same ownership-check pattern already used by a sibling
   endpoint in the same router, if one exists, instead of inventing a
   new check.
