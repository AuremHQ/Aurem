---
status: draft
approved_by: null
approved_on: null
inject_on: [security checklist, vanguard skills, what security checks]
max_chars: 500
---

# Security checks — pointer, not a copy

The real security-check content lives in `backend/vanguard_skills/*.md`
(auth-implementation.md, api-security.md, pci-compliance.md,
privacy-by-design.md, frontend-security.md, backend-security.md,
security-review.md) and is already injected by
`services/skill_context_injector.py` on every Mode C task. This file
exists only so a "what security checks does ORA run" question has an
approved fact to point to — it does not duplicate the skill content.
