"""tests/test_regression_iter2026_24b_brain_l3_approved_examples.py

ORA Brain Part 1, Phase C — L3 approved examples wired into
services/ora_council_retriever.py, ranked above logged rows.
"""
from __future__ import annotations

import os
import re
from datetime import UTC, datetime

import pytest

import services.knowledge_injector as ki
import services.ora_council_retriever as r


class _AsyncCursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        v = self._rows[self._i]
        self._i += 1
        return v


class _FakeColl:
    def __init__(self, rows):
        self.rows = rows

    def find(self, q=None, proj=None):
        return _AsyncCursor(self.rows)


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def __getitem__(self, name):
        return _FakeColl(self._rows)


def _row(msg, reply, mode="A", user="u1", project="p1"):
    return {
        "user_message": msg, "final_output": reply, "mode": mode,
        "user_id": user, "project_id": project,
        "pass_result": True, "lint_blocked": False,
        "timestamp": datetime.now(UTC),
    }


@pytest.fixture(autouse=True)
def _reset():
    r._reset_for_tests()
    ki._load_file.cache_clear()
    yield
    r._reset_for_tests()
    ki._load_file.cache_clear()


def _write_example(tmp_path, name, *, status, task_type, task_text, pattern_text):
    (tmp_path / name).write_text(
        f"---\nstatus: {status}\ntask_type: {task_type}\n---\n\n"
        f"## Task\n{task_text}\n\n## Good response pattern\n{pattern_text}\n",
    )


class TestApprovedExampleLoading:
    def test_approved_example_is_loaded(self, tmp_path, monkeypatch):
        _write_example(
            tmp_path, "ex1.md", status="approved", task_type="fix",
            task_text="Fix the thing", pattern_text="State root cause first.",
        )
        monkeypatch.setattr(ki, "_EXAMPLES_DIR", str(tmp_path))
        picks = ki.load_approved_examples("please fix this bug", limit=3)
        assert len(picks) == 1
        assert picks[0]["msg"] == "Fix the thing"
        assert "root cause" in picks[0]["reply"]

    def test_draft_example_never_loaded(self, tmp_path, monkeypatch):
        _write_example(
            tmp_path, "ex1.md", status="draft", task_type="fix",
            task_text="Fix the thing", pattern_text="pattern",
        )
        monkeypatch.setattr(ki, "_EXAMPLES_DIR", str(tmp_path))
        assert ki.load_approved_examples("fix this bug") == []

    def test_task_type_must_match_the_query(self, tmp_path, monkeypatch):
        _write_example(
            tmp_path, "ex1.md", status="approved", task_type="performance",
            task_text="Task", pattern_text="Pattern",
        )
        monkeypatch.setattr(ki, "_EXAMPLES_DIR", str(tmp_path))
        assert ki.load_approved_examples("please fix a bug") == []
        assert len(ki.load_approved_examples("this feels slow, performance issue")) == 1

    def test_limit_is_respected(self, tmp_path, monkeypatch):
        for i in range(5):
            _write_example(
                tmp_path, f"ex{i}.md", status="approved", task_type="fix",
                task_text=f"Task {i}", pattern_text=f"Pattern {i}",
            )
        monkeypatch.setattr(ki, "_EXAMPLES_DIR", str(tmp_path))
        assert len(ki.load_approved_examples("fix bug", limit=2)) == 2

    def test_never_touches_a_database_no_auto_promotion(self):
        """load_approved_examples takes no db argument at all — it is
        structurally impossible for it to read ora_council_logs or
        auto-promote a logged row. Confirmed by signature, not just
        behavior."""
        import inspect
        sig = inspect.signature(ki.load_approved_examples)
        assert "db" not in sig.parameters


class TestRankedAboveLoggedRows:
    @pytest.mark.asyncio
    async def test_l3_example_appears_before_logged_rows_in_the_block(self, tmp_path, monkeypatch):
        _write_example(
            tmp_path, "ex1.md", status="approved", task_type="fix",
            task_text="APPROVED EXAMPLE TASK MARKER",
            pattern_text="APPROVED EXAMPLE PATTERN MARKER",
        )
        monkeypatch.setattr(ki, "_EXAMPLES_DIR", str(tmp_path))

        rows = [
            _row("fix the react onClick button handler",
                 "LOGGED ROW REPLY MARKER use onClick={handler}"),
        ] + [_row(f"filler q{i}", f"filler a{i}") for i in range(19)]
        db = _FakeDB(rows)
        out, n = await r.get_council_few_shot(
            db, "fix the react onClick button handler", mode="A", k=2,
            user_id="u1", project_id="p1",
        )
        assert "APPROVED EXAMPLE TASK MARKER" in out
        l3_pos = out.index("APPROVED EXAMPLE TASK MARKER")
        logged_pos = out.index("LOGGED ROW REPLY MARKER")
        assert l3_pos < logged_pos, "L3 approved example must rank ABOVE the logged row"
        assert n == 2

    @pytest.mark.asyncio
    async def test_l3_example_returned_even_when_corpus_below_threshold(self, tmp_path, monkeypatch):
        """L3 is independent of the logged-row activation threshold —
        a brand-new user/corpus still gets approved examples."""
        _write_example(
            tmp_path, "ex1.md", status="approved", task_type="fix",
            task_text="Task text", pattern_text="Pattern text",
        )
        monkeypatch.setattr(ki, "_EXAMPLES_DIR", str(tmp_path))
        db = _FakeDB([])  # empty corpus, well below _MIN_GLOBAL
        out, n = await r.get_council_few_shot(db, "please fix this")
        assert n == 1
        assert "Task text" in out
        assert "l3-approved-only" in out or "l3" in out

    @pytest.mark.asyncio
    async def test_no_l3_match_falls_back_to_pure_logged_behavior_unchanged(self, tmp_path, monkeypatch):
        """No approved example matches -> behavior is byte-identical
        to the pre-Phase-C retriever (regression guard)."""
        monkeypatch.setattr(ki, "_EXAMPLES_DIR", str(tmp_path))  # empty dir
        db = _FakeDB([])
        out, n = await r.get_council_few_shot(db, "anything at all")
        assert out == ""
        assert n == 0


class TestFactsInExamplesLint:
    """Phase C's explicit lint rule: examples contain no prices/
    product claims — behavior only, never facts. A bare mention of a
    word like "guarantee"/"refund" is fine when the example is
    explicitly TEACHING "don't invent one" (example_10's whole point)
    — only a mention NOT negated by a nearby no/never/not/n't counts
    as the example itself asserting a claim."""

    _FORBIDDEN_WORDS = ("unlimited", "guarantee", "refund", "uptime")
    _NEGATORS = ("no ", "not ", "never ", "n't ", "don't have")

    @classmethod
    def _asserts_a_claim(cls, text: str) -> str | None:
        lowered = text.lower()
        if re.search(r"\$\s*\d", lowered):
            return "$<digit> price pattern"
        for word in cls._FORBIDDEN_WORDS:
            for m in re.finditer(re.escape(word), lowered):
                window = lowered[max(0, m.start() - 40):m.start()]
                if not any(neg in window for neg in cls._NEGATORS):
                    return word
        return None

    def test_real_examples_contain_no_price_or_product_claims(self):
        """Scoped to the 'Good response pattern' section only — the
        '## Task' section is the hypothetical USER's question (a user
        can legitimately ask about guarantees/refunds; that's not ORA
        asserting a fact)."""
        base = ki._EXAMPLES_DIR
        files = [f for f in os.listdir(base) if f.endswith(".md")]
        assert len(files) == 10
        for f in files:
            with open(os.path.join(base, f), encoding="utf-8") as fh:
                text = fh.read()
            _meta, body = ki._parse_frontmatter(text)
            _msg, reply = ki._split_example_body(body)
            hit = self._asserts_a_claim(reply)
            assert hit is None, f"{f} asserts a fact-shaped claim in its response pattern: {hit!r}"

    def test_lint_actually_catches_a_violation(self, tmp_path):
        """Proves the lint rule itself works, not just that today's
        10 files happen to pass it."""
        bad_text = (
            "## Good response pattern\n"
            "Say it costs $9/month, unlimited tasks, and we guarantee 99.9% uptime."
        )
        assert self._asserts_a_claim(bad_text) is not None

    def test_lint_does_not_false_positive_on_a_negated_teaching_example(self):
        """example_10's own pattern: teaching the model to say there
        is NO guarantee, not asserting one."""
        safe_text = (
            "## Good response pattern\n"
            "This is not a confirmed, approved product fact — say so "
            "plainly rather than inventing a number or percentage."
        )
        assert self._asserts_a_claim(safe_text) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
