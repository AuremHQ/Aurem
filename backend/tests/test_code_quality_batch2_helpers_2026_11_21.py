"""tests/test_code_quality_batch2_helpers_2026_11_21.py

Code Quality batch 2: ten functions over the complexity limit were split into
small helpers with NO behaviour change. The existing suites exercise the public
functions; these tests pin the new helpers' behaviour (edge cases included) so a
later edit cannot change it silently.
"""
from __future__ import annotations

import pytest


# ── services/aurem_managed_db.py ──────────────────────────────────────
def test_missing_required_errors_lists_each_absent_field_in_order():
    from services.aurem_managed_db import _missing_required_errors
    assert _missing_required_errors({"a": 1}, ["a", "b", "c"]) == [
        "missing_required_field: b", "missing_required_field: c"]
    assert _missing_required_errors({}, []) == []


def test_type_mismatch_errors_checks_only_present_known_types():
    from services.aurem_managed_db import _type_mismatch_errors
    fields = {"name": {"type": "string"}, "age": {"type": "integer"}, "flag": {"type": "boolean"},
              "ghost": {"type": "string"}, "weird": {"type": "uuid"}, "untyped": {}}
    doc = {"name": 5, "age": True, "flag": True, "weird": 1, "untyped": object()}   # bool is not an integer
    assert _type_mismatch_errors(doc, fields) == [
        "type_mismatch: name expected string", "type_mismatch: age expected integer"]


def test_validate_against_schema_combines_both_and_no_schema_is_ok():
    from services.aurem_managed_db import validate_against_schema
    schema = {"required": ["id"], "properties": {"n": {"type": "number"}}}
    assert validate_against_schema({"n": "x"}, schema) == {
        "ok": False, "errors": ["missing_required_field: id", "type_mismatch: n expected number"]}
    assert validate_against_schema({"id": 1, "n": 2.5}, schema) == {"ok": True, "errors": []}
    assert validate_against_schema({"anything": 1}, None) == {"ok": True, "errors": []}


# ── services/page_resolver.py ─────────────────────────────────────────
def test_no_match_shape():
    from services.page_resolver import _no_match
    assert _no_match("home") == {"category": "home", "candidates": [], "best": None, "ambiguous": False}
    assert _no_match(None)["category"] is None


def test_tied_for_shallowest_only_counts_the_top_depth():
    from services.page_resolver import _is_tied_for_shallowest
    assert _is_tied_for_shallowest(["a.js", "b.js", "x/c.js"]) is True
    assert _is_tied_for_shallowest(["a.js", "x/b.js", "x/c.js"]) is False
    assert _is_tied_for_shallowest(["only.js"]) is False


def test_resolve_unique_best_is_the_shallowest_and_tie_is_ambiguous():
    from services.page_resolver import resolve
    unique = resolve(["src/pages/About.jsx", "src/about/deep/AboutUs.jsx"], "update the about page")
    assert unique["category"] == "about" and unique["ambiguous"] is False
    assert unique["best"] == "src/pages/About.jsx"
    tied = resolve(["a/About.jsx", "b/About.jsx"], "about page")
    assert tied["ambiguous"] is True and tied["best"] is None and len(tied["candidates"]) == 2
    assert resolve([], "about")["best"] is None
    assert resolve(["x.py"], "about")["candidates"] == []


# ── services/plan_scan_contract.py ────────────────────────────────────
def test_split_by_plan_partitions_by_normalised_path():
    from services.plan_scan_contract import _split_by_plan
    findings = [{"filepath": "./a.py"}, {"filepath": "b.py"}, {"filepath": None}]
    covered, deferred = _split_by_plan(findings, {"a.py"})
    assert covered == [{"filepath": "./a.py"}]
    assert deferred == [{"filepath": "b.py"}, {"filepath": None}]


def test_check_plan_scan_consistency_reports_mismatch_and_deferred():
    from services.plan_scan_contract import check_plan_scan_consistency
    out = check_plan_scan_consistency(
        {"files_to_change": ["a.py", "./c.py", ""]},
        [{"filepath": "a.py", "line": 3, "description": "x"}, {"filepath": "b.py", "line": 9}],
    )
    assert out["total_findings"] == 2 and out["covered_count"] == 1 and out["deferred_count"] == 1
    assert out["mismatched_files"] == ["c.py"]
    assert out["deferred"] == [{"filepath": "b.py", "line": 9, "description": ""}]


# ── services/seo/image_alts.py ────────────────────────────────────────
def test_images_needing_alt_skips_present_blank_aware_and_presentation():
    from bs4 import BeautifulSoup

    from services.seo.image_alts import _images_needing_alt
    soup = BeautifulSoup(
        '<img src="a.png" alt="ok"><img src="b.png"><img src="c.png" alt="  ">'
        '<img src="d.png" role="Presentation"><img src="e.png" alt="">', "html.parser")
    assert [i["src"] for i in _images_needing_alt(soup)] == ["b.png", "c.png", "e.png"]


@pytest.mark.asyncio
async def test_patch_image_alts_uses_provider_then_fallback_and_none_when_nothing_to_do():
    from services.seo.image_alts import patch_image_alts

    async def provider(src, ctx):
        return "" if src.endswith("b.png") else "described"

    patch = await patch_image_alts(path="p.html", html='<img src="a.png"><img src="b.png">', alt_provider=provider)
    assert patch is not None and "added alt= on 2 image(s)" in patch["reason"]
    assert 'alt="described"' in patch["after"] and patch["after"].count("alt=") == 2
    assert await patch_image_alts(path="p.html", html='<img src="a.png" alt="x">') is None


# ── services/seo/meta_tags.py ─────────────────────────────────────────
def _head():
    from bs4 import BeautifulSoup
    soup = BeautifulSoup("<html><head></head><body></body></html>", "html.parser")
    return soup, soup.head


def test_add_title_and_description_adds_only_what_is_missing():
    from services.seo.meta_tags import _add_title_and_description
    soup, head = _head()
    assert _add_title_and_description(soup, head, "T", "D") is True
    assert soup.title.string == "T" and soup.find("meta", attrs={"name": "description"})["content"] == "D"
    assert _add_title_and_description(soup, head, "T2", "D2") is False        # already there: untouched
    assert soup.title.string == "T"
    soup2, head2 = _head()
    assert _add_title_and_description(soup2, head2, "", "") is False


def test_add_missing_og_tags_skips_empty_values_and_existing_tags():
    from services.seo.meta_tags import _add_missing_og_tags
    soup, head = _head()
    og = [("og:title", "T"), ("og:image", ""), ("og:type", "website")]
    assert _add_missing_og_tags(soup, head, og) is True
    assert soup.find("meta", attrs={"property": "og:title"})["content"] == "T"
    assert soup.find("meta", attrs={"property": "og:image"}) is None
    assert _add_missing_og_tags(soup, head, og) is False                      # second run: nothing new


def test_patch_meta_tags_no_head_or_nothing_to_add_returns_none():
    from services.seo.meta_tags import patch_meta_tags
    assert patch_meta_tags(path="x.html", html="<p>no head</p>", title="T") is None
    html = ('<html><head><title>T</title><meta name="description" content="D">'
            '<meta property="og:title" content="T"><meta property="og:description" content="D">'
            '<meta property="og:type" content="website"></head></html>')
    assert patch_meta_tags(path="x.html", html=html, title="T", description="D") is None
    patch = patch_meta_tags(path="x.html", html="<html><head></head></html>", title="T", url="https://x.test")
    assert patch is not None and 'property="og:url"' in patch["after"] and "<title>T</title>" in patch["after"]


# ── services/tool_failure_evidence.py ─────────────────────────────────
def test_failure_signature_cases():
    from services.tool_failure_evidence import _failure_signature
    assert _failure_signature("nope") is None and _failure_signature(None) is None
    assert _failure_signature({"tool": "x", "ok": True}) is None
    assert _failure_signature({"tool": "x"}) is None
    assert _failure_signature({"tool": "git push!", "ok": False}) == "tool:gitpush:tool_failed"
    assert _failure_signature({"name": "n", "result": {"ok": False, "error_class": "Time-out"}}) == "tool:n:Timeout"
    assert _failure_signature({"ok": False}) == "tool:unknown:tool_failed"
    assert _failure_signature({"tool": "t" * 200, "ok": False}).startswith("tool:" + "t" * 80 + ":")


def test_failure_signatures_dedupes_sorts_and_tolerates_none():
    from services.tool_failure_evidence import failure_signatures
    calls = [{"tool": "b", "ok": False}, {"tool": "a", "ok": False}, {"tool": "b", "ok": False}, 5, {"tool": "c"}]
    assert failure_signatures(calls) == ["tool:a:tool_failed", "tool:b:tool_failed"]
    assert failure_signatures(None) == [] and failure_signatures([]) == []


# ── services/loop_ship_diff.py ────────────────────────────────────────
def test_file_diff_row_all_four_sources():
    from services.loop_ship_diff import _file_diff_row
    line = _file_diff_row("a.py", "x\ny\n", "x\n", 0)
    assert line["diff_source"] == "line" and line["is_new"] is False and line["delta_bytes"] == len("x\ny\n") - len("x\n")
    byte = _file_diff_row("b.py", "12345", None, 100)
    assert byte == {"path": "b.py", "additions": 0, "deletions": 0, "is_new": False,
                    "delta_bytes": -95, "diff_source": "bytes"}
    new = _file_diff_row("c.py", "a\nb", None, 0)
    assert new["is_new"] is True and new["additions"] == 2 and new["diff_source"] == "line"
    empty_new = _file_diff_row("d.py", "", None, 0)
    assert empty_new["is_new"] is True and empty_new["additions"] == 0 and empty_new["delta_bytes"] == 0


def test_compute_files_diff_one_row_per_new_file_and_none_values_are_empty():
    from services.loop_ship_diff import compute_files_diff
    rows = compute_files_diff({"a.py": "old"}, {"a.py": "new!", "b.py": None}, {"b.py": 10})
    assert [r["path"] for r in rows] == ["a.py", "b.py"]
    assert rows[1]["diff_source"] == "bytes" and rows[1]["delta_bytes"] == -10
    assert compute_files_diff({}, None) == []


# ── services/mcp_scoped_tools.py ──────────────────────────────────────
@pytest.mark.parametrize("parsed,expected", [
    (["read", "write"], ["read", "write"]),
    (["write", "write", "security", "read"], ["write", "security"]),     # dedupe, cap at 2
    (["bogus", "project", 5, None], ["project"]),                        # unknown / non-string dropped
    (["bogus"], []), ([], []), (None, []), ("read", []), ({"a": 1}, []),
])
def test_valid_groups(parsed, expected):
    from services.mcp_scoped_tools import _valid_groups
    assert _valid_groups(parsed) == expected


@pytest.mark.asyncio
async def test_classify_tool_groups_falls_back_to_the_safe_default(monkeypatch):
    import core.intent_gateway as gw
    from services.mcp_scoped_tools import classify_tool_groups

    async def fake(prompt, timeout, max_tokens):
        return ["nope"]

    monkeypatch.setattr(gw, "classify_llm_json", fake)
    assert await classify_tool_groups("anything") == ["read", "project"]
    assert await classify_tool_groups("   ") == ["read", "project"]

    async def good(prompt, timeout, max_tokens):
        return ["security", "write", "read"]

    monkeypatch.setattr(gw, "classify_llm_json", good)
    assert await classify_tool_groups("harden auth") == ["security", "write"]


# ── services/codebase_indexer.py ──────────────────────────────────────
def test_deps_lines_caps_at_30_and_skips_empty():
    from services.codebase_indexer import _deps_lines
    assert _deps_lines({}) == []
    out = _deps_lines({"python": [f"p{i}" for i in range(40)], "node": ["n"]})
    assert out[0].startswith("Python deps: p0, p1") and out[0].endswith("p29") and out[1] == "Node deps: n"


def test_role_sections_orders_roles_caps_and_neutralises_code_fences():
    from services.codebase_indexer import _role_sections
    files = [{"role": "models", "path": "m.py", "snippet": "class M: ```x```", "lang": "py"},
             {"role": "routes", "path": "r.py", "snippet": "  ", "lang": "py"},          # blank snippet skipped
             {"role": "routes", "path": "r2.py", "snippet": "@app.get('/')", "lang": "py"},
             {"role": "other", "path": "o.py", "snippet": "ignored"}]
    out = _role_sections(files)
    joined = "\n".join(out)
    assert out[0].endswith("ROUTES (2 shown):") and joined.index("ROUTES") < joined.index("MODELS")
    assert "### r2.py" in joined and "### r.py" not in joined and "o.py" not in joined
    assert "```x```" not in joined and "´´´" in joined
    assert _role_sections([]) == []


def test_format_context_block_header_assembly_and_trim():
    from services.codebase_indexer import _format_context_block
    doc = {"repo_owner": "o", "repo_name": "r", "file_count": 3,
           "deps": {"python": ["fastapi"]},
           "files": [{"role": "routes", "path": "r.py", "snippet": "x" * 700, "lang": "py"}]}
    block = _format_context_block(doc)
    assert "repo o/r@main, 3 files" in block and "Python deps: fastapi" in block
    assert "### r.py" in block and "x" * 600 in block and "x" * 601 not in block   # preview clipped at 600
    trimmed = _format_context_block(doc, max_chars=50)
    assert trimmed.endswith("\n…(context trimmed)") and len(trimmed) == 50 + len("\n…(context trimmed)")


# ── services/health_notifier.py::_process_check ───────────────────────
class _StateColl:
    def __init__(self, row=None):
        self.row, self.updates = row, []

    async def find_one(self, *a, **k):
        return self.row

    def with_options(self, **k):
        return self

    async def update_one(self, flt, upd, upsert=False):
        self.updates.append((flt, upd, upsert))


class _Check:
    id, name, category = "c1", "Check One", "infra"


def _db(row=None):
    class D:
        pass
    d = D()
    d.health_check_state = _StateColl(row)
    return d


@pytest.mark.asyncio
async def test_process_check_unconfirmed_persists_candidate_only_when_it_changed(monkeypatch):
    import services.health_notifier as hn
    monkeypatch.setattr(hn, "_advance_candidate",
                        lambda row, last, new: (None, {"candidate_status": "red", "candidate_count": 1}))
    fired = []

    async def _fire(*a, **k):
        fired.append(a)

    monkeypatch.setattr(hn, "_fire_notification", _fire)
    db = _db({"candidate_status": None, "candidate_count": 0})
    await hn._process_check(db, _Check(), {"status": "red"})
    assert db.health_check_state.updates == [({"_id": "c1"}, {"$set": {"candidate_status": "red", "candidate_count": 1}}, True)]
    assert fired == []
    same = _db({"candidate_status": "red", "candidate_count": 1})
    await hn._process_check(same, _Check(), {"status": "red"})
    assert same.health_check_state.updates == []                      # unchanged candidate: no write
    count_only = _db({"candidate_status": "red", "candidate_count": 0})   # same status, count moved 0 -> 1
    await hn._process_check(count_only, _Check(), {"status": "red"})
    assert len(count_only.health_check_state.updates) == 1


@pytest.mark.asyncio
async def test_process_check_confirmed_fires_once_and_records_the_new_baseline(monkeypatch):
    import services.health_notifier as hn
    monkeypatch.setattr(hn, "_advance_candidate", lambda row, last, new: ("red", {"candidate_status": None}))

    async def _decide(acked, last, confirmed, row):
        return True

    fired = []

    async def _fire(db, cid, name, category, old, new, detail):
        fired.append((cid, old, new, detail))

    monkeypatch.setattr(hn, "_decide_should_notify", _decide)
    monkeypatch.setattr(hn, "_fire_notification", _fire)
    db = _db({"last_known": "green"})
    await hn._process_check(db, _Check(), {"status": "red", "detail": "boom"})
    assert fired == [("c1", "green", "red", "boom")]
    (flt, upd, upsert), = db.health_check_state.updates
    assert flt == {"_id": "c1"} and upsert is True
    assert upd["$set"]["last_known"] == "red" and "last_known_at" in upd["$set"]
    assert upd["$set"]["candidate_status"] is None


@pytest.mark.asyncio
async def test_process_check_confirmed_but_not_notifiable_still_records_baseline(monkeypatch):
    import services.health_notifier as hn
    monkeypatch.setattr(hn, "_advance_candidate", lambda row, last, new: ("green", {}))

    async def _decide(*a):
        return False

    async def _fire(*a, **k):
        raise AssertionError("must not fire")

    monkeypatch.setattr(hn, "_decide_should_notify", _decide)
    monkeypatch.setattr(hn, "_fire_notification", _fire)
    db = _db(None)
    await hn._process_check(db, _Check(), {"status": "green"})
    assert db.health_check_state.updates[0][1]["$set"]["last_known"] == "green"

