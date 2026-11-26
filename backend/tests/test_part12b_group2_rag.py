"""Part XII group 2 — RAG / index / context fixes (part12b lane).

Covers verify-part12-rag-index.md:
  b1  knowledge_injector limit=0 must inject 0 examples
  b2  word-boundary keyword matching (skill + knowledge injectors)
  b3  @lru_cache must not cache "" on load error (mtime-keyed cache)
  c1  project_brain _build_context_string must not IndexError
  c2  update_brain_after_COMMIT must not overwrite recurring_bugs
  d   ora_context boundary regex + validate_founder_pod_command
      must refuse --file=/app/backend/.env at BOTH gates
  (a) is WRONG-or-STALE — no test, no fix (see FIX-REPORT.md).

Run from the fixed tree (default) or against pristine as a negative
control:  PART12B_BACKEND=<pristine backend dir> pytest <this file>
"""
import os
import sys
import types
from pathlib import Path

import pytest

BACKEND = Path(os.environ.get(
    "PART12B_BACKEND", Path(__file__).resolve().parents[1]))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


# ── stubs (only for modules absent from this lane's seeded tree) ──
def _stub_bin_context():
    if "services.bin_context" in sys.modules:
        return
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class BINContext:
        bin_id: str = ""
        pid: str = ""
        repo_owner: str = ""
        repo_name: str = ""
        branch: str = "main"
        pat: str = ""
        is_founder: bool = False

    m = types.ModuleType("services.bin_context")
    m.BINContext = BINContext

    async def build_bin_context(**kw):
        return BINContext()
    m.build_bin_context = build_bin_context
    sys.modules["services.bin_context"] = m


# ────────────────────────── (b1) limit=0 ──────────────────────────
EXAMPLE_BODY = """---
status: approved
task_type: refund
---
## Task
please refund me for the broken widget

## Good response pattern
Sure — I have started the refund for the broken widget.
"""


@pytest.fixture()
def examples_dir(tmp_path):
    d = tmp_path / "examples"
    d.mkdir()
    for i in range(3):
        (d / f"ex{i}.md").write_text(EXAMPLE_BODY)
    return d


@pytest.fixture()
def knowledge(monkeypatch, examples_dir):
    import services.knowledge_injector as ki
    monkeypatch.setattr(ki, "_EXAMPLES_DIR", str(examples_dir))
    ki._load_file.cache_clear()
    return ki


class TestLimitZero:
    def test_limit_0_injects_nothing(self, knowledge):
        assert knowledge.load_approved_examples("please refund me", limit=0) == []

    def test_negative_limit_injects_nothing(self, knowledge):
        assert knowledge.load_approved_examples("please refund me", limit=-1) == []
        assert knowledge.load_approved_examples("please refund me", limit=-5) == []

    def test_limit_1_returns_exactly_one(self, knowledge):
        assert len(knowledge.load_approved_examples("please refund me", limit=1)) == 1

    def test_limit_3_returns_three(self, knowledge):
        assert len(knowledge.load_approved_examples("please refund me", limit=3)) == 3


# ─────────────── (b2) word-boundary keyword matching ──────────────
@pytest.fixture()
def skills_dir(tmp_path):
    d = tmp_path / "vanguard_skills"
    d.mkdir()
    (d / "api-security.md").write_text("API SECURITY CONTENT " * 20)
    (d / "frontend-security.md").write_text("FRONTEND SECURITY CONTENT " * 20)
    (d / "security-review.md").write_text("SECURITY REVIEW CONTENT " * 20)
    return d


@pytest.fixture()
def injector(monkeypatch, skills_dir):
    import services.skill_context_injector as sci
    monkeypatch.setattr(sci, "_SKILLS_DIR", str(skills_dir))
    sci._load_skill.cache_clear()
    return sci


class TestWordBoundarySkills:
    def test_build_does_not_inject_frontend_security(self, injector):
        names = [n for n, _ in injector.select_skills("build the backend")]
        assert "frontend-security.md" not in names

    def test_require_and_suite_do_not_inject_frontend(self, injector):
        for task in ("require the new module", "run the test suite"):
            names = [n for n, _ in injector.select_skills(task)]
            assert "frontend-security.md" not in names, task

    def test_real_ui_word_still_injects(self, injector):
        names = [n for n, _ in injector.select_skills("fix the ui layout")]
        assert "frontend-security.md" in names

    def test_restart_and_interest_do_not_inject_api_security(self, injector):
        for task in ("restart the service", "i have no interest"):
            names = [n for n, _ in injector.select_skills(task)]
            assert "api-security.md" not in names, task

    def test_real_rest_endpoint_still_injects(self, injector):
        names = [n for n, _ in injector.select_skills("add a rest endpoint")]
        assert "api-security.md" in names


KNOWLEDGE_BODY = """---
status: approved
inject_on: ["rest"]
max_chars: 500
---
REST endpoint guidance body.
"""


@pytest.fixture()
def knowledge_dir(tmp_path):
    d = tmp_path / "knowledge"
    d.mkdir()
    (d / "common-fixes.md").write_text(KNOWLEDGE_BODY)
    return d


@pytest.fixture()
def knowledge_select(monkeypatch, knowledge_dir):
    import services.knowledge_injector as ki
    monkeypatch.setattr(ki, "_KNOWLEDGE_DIR", str(knowledge_dir))
    ki._load_file.cache_clear()
    return ki


class TestWordBoundaryKnowledge:
    def test_restart_does_not_match_rest_trigger(self, knowledge_select):
        assert knowledge_select.select_knowledge("restart the service") == []

    def test_interest_does_not_match_rest_trigger(self, knowledge_select):
        assert knowledge_select.select_knowledge("i have no interest") == []

    def test_real_rest_matches(self, knowledge_select):
        picks = knowledge_select.select_knowledge("add a rest endpoint")
        assert [n for n, _ in picks] == ["common-fixes.md"]

    def test_task_type_word_boundary(self, knowledge, tmp_path):
        # task_type "refund" must not match "refunded" prose? It MUST
        # match the word itself; a task_type of "rest" must not match
        # "restart" — covered via a fresh examples dir.
        ex = tmp_path / "ex2"
        ex.mkdir()
        (ex / "a.md").write_text(EXAMPLE_BODY.replace(
            "task_type: refund", "task_type: rest"))
        import services.knowledge_injector as ki
        ki._EXAMPLES_DIR = str(ex)
        assert ki.load_approved_examples("restart the service") == []
        assert len(ki.load_approved_examples("add a rest endpoint")) == 1


# ─────────────── (b3) cache must not cache failures ───────────────
class TestLoadCacheFailures:
    def test_skill_appearing_later_is_picked_up(self, injector, skills_dir):
        # Missing file -> "" (and NOT cached as a miss).
        assert injector._load_skill("auth-implementation.md", 2500) == ""
        (skills_dir / "auth-implementation.md").write_text("REAL CONTENT")
        assert injector._load_skill("auth-implementation.md", 2500) == "REAL CONTENT"

    def test_skill_edit_is_picked_up(self, injector, skills_dir):
        p = skills_dir / "api-security.md"
        assert "API SECURITY CONTENT" in injector._load_skill("api-security.md", 2000)
        p.write_text("EDITED API CONTENT " * 20)
        # Force a new mtime so the change is unambiguous.
        st = os.stat(p)
        os.utime(p, (st.st_atime + 5, st.st_mtime + 5))
        assert "EDITED API CONTENT" in injector._load_skill("api-security.md", 2000)

    def test_knowledge_file_appearing_later_is_picked_up(
            self, knowledge_select, knowledge_dir):
        import services.knowledge_injector as ki
        assert ki._load_file("product.md") == ({}, "")
        (knowledge_dir / "product.md").write_text(
            "---\nstatus: approved\ninject_on: [\"price\"]\n---\nPRICE BODY")
        meta, body = ki._load_file("product.md")
        assert meta.get("status") == "approved"
        assert "PRICE BODY" in body


# ─────────────── (c1) empty descriptions ──────────────────────────
class TestProjectBrainContextString:
    def _brain(self, descriptions):
        events = []
        for i, d in enumerate(descriptions):
            ev = {"type": "commit", "files": ["a.py"]}
            if d is not _MISSING:
                ev["description"] = d
            events.append(ev)
        return {"event_log": events}

    def test_empty_description_no_raise(self):
        import services.project_brain as pb
        out = pb._build_context_string(self._brain([""]))
        assert isinstance(out, str)

    def test_whitespace_description_no_raise(self):
        import services.project_brain as pb
        out = pb._build_context_string(self._brain(["   \n  "]))
        assert isinstance(out, str)

    def test_missing_description_no_raise(self):
        import services.project_brain as pb
        out = pb._build_context_string(self._brain([_MISSING]))
        assert isinstance(out, str)

    def test_none_description_no_raise(self):
        import services.project_brain as pb
        out = pb._build_context_string(self._brain([None]))
        assert isinstance(out, str)

    def test_normal_multiline_takes_first_line_only(self):
        import services.project_brain as pb
        out = pb._build_context_string(
            self._brain(["fix login bug\nsecond line"]))
        assert "fix login bug" in out
        assert "second line" not in out


class _Missing:
    pass


_MISSING = _Missing()


# ─────────────── (c2) recurring_bugs $push ────────────────────────
class FakeCollection:
    def __init__(self, existing_descriptions=()):
        self.existing = set(existing_descriptions)
        self.update_calls = []
        self.find_calls = []

    async def find_one(self, filt):
        self.find_calls.append(filt)
        desc = filt.get("recurring_bugs.description")
        if desc in self.existing:
            return {"project_id": filt.get("project_id"),
                    "recurring_bugs": [{"description": desc, "count": 1}]}
        return None

    async def update_one(self, filt, update, upsert=False):
        self.update_calls.append((filt, update, upsert))

        class R:
            matched_count = 1
        return R()


class FakeDB:
    def __init__(self, existing_descriptions=()):
        self.coll = FakeCollection(existing_descriptions)

    def __getitem__(self, name):
        assert name == "project_brains"
        return self.coll


@pytest.mark.asyncio
async def test_both_new_bugs_survive():
    import services.project_brain as pb
    db = FakeDB()
    await pb.update_brain_after_commit(
        db, "p1", "fix stuff", ["a.py"], True,
        ["bug A: login crash", "bug B: token expiry"])
    push_updates = [u for _, u, _ in db.coll.update_calls if "$push" in u]
    assert push_updates, "no $push update recorded (fake db broken?)"
    each = push_updates[-1]["$push"]["recurring_bugs"]["$each"]
    descs = [b["description"] for b in each]
    assert descs == ["bug A: login crash", "bug B: token expiry"]


@pytest.mark.asyncio
async def test_known_bug_increments_new_bug_pushed():
    import services.project_brain as pb
    db = FakeDB(existing_descriptions={"bug A: login crash"})
    await pb.update_brain_after_commit(
        db, "p1", "fix stuff", ["a.py"], True,
        ["bug A: login crash", "bug B: token expiry"])
    inc_calls = [u for _, u, _ in db.coll.update_calls if "$inc" in u]
    assert len(inc_calls) == 1, "known bug must take the $inc path once"
    push_updates = [u for _, u, _ in db.coll.update_calls if "$push" in u]
    each = push_updates[-1]["$push"]["recurring_bugs"]["$each"]
    assert [b["description"] for b in each] == ["bug B: token expiry"]


# ─────────────── (d) ora_context boundary ─────────────────────────
@pytest.fixture(scope="module")
def ora_context():
    _stub_bin_context()
    import services.ora_context as oc
    return oc


class TestBoundaryRegex:
    def test_file_flag_env_refused(self, ora_context):
        assert ora_context.path_hits_ora_boundary(
            "--file=/app/backend/.env") == "/app/backend"

    def test_cat_file_flag_env_refused(self, ora_context):
        assert ora_context.path_hits_ora_boundary(
            "cat --file=/app/backend/.env") == "/app/backend"

    def test_equals_joined_path_refused(self, ora_context):
        assert ora_context.path_hits_ora_boundary(
            "x=/app/backend/.env") == "/app/backend"

    def test_double_slash_refused(self, ora_context):
        assert ora_context.path_hits_ora_boundary(
            "//app/backend/.env") == "/app/backend"

    def test_output_flag_tmp_refused(self, ora_context):
        assert ora_context.path_hits_ora_boundary(
            "--output=/tmp/x") == "/tmp"

    def test_space_form_still_caught(self, ora_context):
        assert ora_context.path_hits_ora_boundary(
            "cat /app/backend/.env") == "/app/backend"

    def test_repo_relative_still_allowed(self, ora_context):
        assert ora_context.path_hits_ora_boundary("backend/.env") is None

    def test_unrelated_path_still_allowed(self, ora_context):
        assert ora_context.path_hits_ora_boundary("/tmp/foo") == "/tmp"


class TestFounderPodValidator:
    def test_file_flag_env_denied(self, ora_context):
        ok, reason = ora_context.validate_founder_pod_command(
            "cat --file=/app/backend/.env")
        assert ok is False
        assert "denylist" in reason

    def test_bare_denied_path_still_denied(self, ora_context):
        ok, reason = ora_context.validate_founder_pod_command(
            "cat /app/backend/.env")
        assert ok is False
        assert "denylist" in reason

    def test_file_flag_shadow_denied(self, ora_context):
        ok, _ = ora_context.validate_founder_pod_command(
            "cat --file=/etc/shadow")
        assert ok is False

    def test_file_flag_allowed_path_passes(self, ora_context):
        ok, reason = ora_context.validate_founder_pod_command(
            "cat --file=/tmp/x")
        assert ok is True, reason

    def test_bare_allowed_path_passes(self, ora_context):
        ok, reason = ora_context.validate_founder_pod_command("cat /tmp/x")
        assert ok is True, reason

    def test_etc_passwd_passes_by_design(self, ora_context):
        # /etc/passwd is NOT on the denylist (only /etc/passwd- and
        # /etc/shadow are) — passes by design, per the verifier.
        ok, _ = ora_context.validate_founder_pod_command(
            "cat --file=/etc/passwd")
        assert ok is True
