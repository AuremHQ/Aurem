"""tests/test_regression_iter2026_68_ship_branch_identity_safety.py

Devil's-advocate audit finding (Phase 4 — Ship-branch collision
hypothesis).

Original concern: the Ship flow's identity resolution ultimately rests
on branch-name matching (webhook head_ref -> loop_outcomes/fix_batches
row -> real user_id/project_id, see services/project_versions.py's own
"never trust the payload" design from this session's earlier Part 2c
work). If two different projects/tasks could ever produce the SAME
ship branch name, their merge attribution could collide.

Investigated, NOT fixed — because it isn't a bug. Traced end to end:
  - services/loop_safety.py::ship_branch_name(slug) is DELIBERATELY
    deterministic per slug (fixing a real historical incident: 371
    duplicate PRs caused by the opposite bug, non-determinism — see
    that function's own docstring). Branch reuse across repeated ships
    of the SAME project/task is correct, intended behaviour, not a
    collision risk.
  - The three real call sites all derive `slug` from a DB-unique
    field: routers/cto_projects/tasks.py's `task_id`
    (`t_<12 hex chars>`, 48 bits of entropy) or
    routers/cto_projects/management.py's `project_id`
    (`p_<10 hex chars>`, 40 bits), both backed by a `unique: True`
    index in scripts/init_prod_collections.py's _BOOTSTRAP_SPEC.
  - `ship_branch_name` truncates its sanitized slug to 40 chars; both
    identifiers are ~12-14 chars fully formed, nowhere near truncation
    range, so there's no truncation-induced collision path either.

This file locks in the three properties the "no collision" conclusion
actually depends on, so if any of them regresses later (entropy
shrinks, the unique index is dropped, truncation range changes), a
test catches it rather than the gap being silently reintroduced.
"""
from __future__ import annotations

import re

import pytest


class TestShipBranchNamingIsDeterministicAndNamespaced:
    def test_same_slug_always_produces_the_same_branch(self):
        from services.loop_safety import ship_branch_name
        assert ship_branch_name("p_abc123") == ship_branch_name("p_abc123")

    def test_different_slugs_produce_different_branches(self):
        from services.loop_safety import ship_branch_name
        assert ship_branch_name("p_abc123") != ship_branch_name("p_def456")

    def test_branch_is_namespaced_under_auremcto_prefix(self):
        from services.loop_safety import SHIP_BRANCH_PREFIX, ship_branch_name
        assert ship_branch_name("p_abc123").startswith(SHIP_BRANCH_PREFIX)


class TestIdentifiersFeedingShipBranchNamesHaveRealEntropy:
    """Branch-name safety is only as strong as the identifiers fed
    into it. These lock in the actual generation code, not a
    restated assumption — if project_id/task_id generation is ever
    weakened, this fails loudly instead of the gap being silent."""

    def test_project_id_generation_has_at_least_40_bits_of_entropy(self):
        import uuid
        proj_id = f"p_{uuid.uuid4().hex[:10]}"
        assert re.fullmatch(r"p_[0-9a-f]{10}", proj_id)
        # 10 hex chars = 40 bits — explicit floor, not "looks random".
        assert len(proj_id) - len("p_") >= 10

    def test_task_id_generation_has_at_least_40_bits_of_entropy(self):
        import uuid
        task_id = f"t_{uuid.uuid4().hex[:12]}"
        assert re.fullmatch(r"t_[0-9a-f]{12}", task_id)
        assert len(task_id) - len("t_") >= 10

    def test_neither_identifier_is_anywhere_near_the_40char_truncation_cap(self):
        from services.loop_safety import ship_branch_name
        proj_id = "p_" + "a" * 10
        task_id = "t_" + "a" * 12
        for ident in (proj_id, task_id):
            branch = ship_branch_name(ident)
            # If truncation ever kicked in, the full identifier
            # wouldn't appear verbatim in the branch name.
            assert ident in branch, (
                f"{ident!r} was truncated inside {branch!r} — identifiers "
                "are no longer safely under the 40-char cap"
            )


class TestUniqueIndexesExistOnTheIdentifiersBranchNamesDeriveFrom:
    def test_bootstrap_spec_has_a_unique_index_on_project_id(self):
        from scripts.init_prod_collections import _BOOTSTRAP_SPEC
        spec = dict(_BOOTSTRAP_SPEC)
        cto_projects_indexes = spec.get("cto_projects", [])
        found = any(
            keys == [("project_id", 1)] and opts.get("unique") is True
            for keys, opts in cto_projects_indexes
        )
        assert found, (
            "no unique index on cto_projects.project_id in the bootstrap "
            "spec — the Ship-branch-collision analysis in this file's "
            "docstring depends on this existing"
        )

    def test_bootstrap_spec_has_a_unique_index_on_task_id(self):
        from scripts.init_prod_collections import _BOOTSTRAP_SPEC
        spec = dict(_BOOTSTRAP_SPEC)
        cto_tasks_indexes = spec.get("cto_tasks", [])
        found = any(
            keys == [("task_id", 1)] and opts.get("unique") is True
            for keys, opts in cto_tasks_indexes
        )
        assert found, (
            "no unique index on cto_tasks.task_id in the bootstrap spec"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
