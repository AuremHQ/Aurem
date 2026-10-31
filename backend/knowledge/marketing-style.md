---
status: draft
approved_by: null
approved_on: null
inject_on: []
max_chars: 0
---

# Marketing style — NOT for engineering answers

This file is reserved for future marketing/landing-copy tone guidance.
`services/knowledge_injector.py` does not load this file at all (it is
excluded from the candidate file list by name, not merely by an empty
`inject_on` list) — it must never reach an engineering-answer prompt.
See `tests/test_regression_iter<N>_brain_marketing_never_injected.py`.
