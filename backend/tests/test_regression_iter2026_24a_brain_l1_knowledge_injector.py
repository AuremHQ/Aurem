"""tests/test_regression_iter2026_24a_brain_l1_knowledge_injector.py

ORA Brain Part 1, Phase A — services/knowledge_injector.py.

Every backend/knowledge/*.md file ships as `status: draft` by design
— nothing is ever injected until a human flips the flag to
`approved`. These tests therefore patch in synthetic approved/draft
fixtures via monkeypatch rather than relying on the real (draft)
files ever matching, so the suite proves the MECHANISM, not today's
content state.
"""
from __future__ import annotations

import os

import pytest

pytestmark = []


def _reload():
    import importlib

    import services.knowledge_injector as mod
    importlib.reload(mod)
    mod._load_file.cache_clear()
    return mod


class TestFrontmatterParsing:
    def test_valid_frontmatter_parses(self):
        mod = _reload()
        meta, body = mod._parse_frontmatter(
            "---\nstatus: approved\ninject_on: [foo]\nmax_chars: 10\n---\nHello world",
        )
        assert meta["status"] == "approved"
        assert meta["inject_on"] == ["foo"]
        assert body.strip() == "Hello world"

    def test_missing_frontmatter_returns_empty_meta_not_a_crash(self):
        mod = _reload()
        meta, body = mod._parse_frontmatter("Just plain text, no frontmatter at all.")
        assert meta == {}
        assert "plain text" in body

    def test_garbage_yaml_degrades_to_empty_meta(self):
        mod = _reload()
        meta, _ = mod._parse_frontmatter("---\n: : : not valid yaml : : [[[\n---\nbody")
        assert meta == {}

    def test_empty_string_does_not_crash(self):
        mod = _reload()
        meta, body = mod._parse_frontmatter("")
        assert meta == {}
        assert body == ""

    def test_none_input_does_not_crash(self):
        mod = _reload()
        meta, body = mod._parse_frontmatter(None)
        assert meta == {}
        assert body == ""

    def test_unicode_frontmatter_and_body(self):
        mod = _reload()
        meta, body = mod._parse_frontmatter(
            "---\nstatus: approved\ninject_on: [日本語, émoji🎉]\n---\n本文 émoji 🎉",
        )
        assert meta["status"] == "approved"
        assert "日本語" in meta["inject_on"]
        assert "émoji" in body

    def test_huge_frontmatter_body_does_not_hang(self):
        mod = _reload()
        huge = "x" * 500_000
        meta, body = mod._parse_frontmatter(f"---\nstatus: approved\n---\n{huge}")
        assert meta["status"] == "approved"
        assert len(body) == 500_000


class TestDraftNeverInjected:
    def test_draft_file_never_selected_even_with_matching_keyword(self, tmp_path, monkeypatch):
        mod = _reload()
        d = tmp_path / "knowledge"
        d.mkdir()
        (d / "product.md").write_text(
            "---\nstatus: draft\ninject_on: [hello]\nmax_chars: 100\n---\nSecret draft content",
        )
        monkeypatch.setattr(mod, "_KNOWLEDGE_DIR", str(d))
        monkeypatch.setattr(mod, "_CANDIDATE_FILES", ("product.md",))
        mod._load_file.cache_clear()
        picks = mod.select_knowledge("hello there")
        assert picks == []

    def test_approved_file_with_matching_keyword_is_selected(self, tmp_path, monkeypatch):
        mod = _reload()
        d = tmp_path / "knowledge"
        d.mkdir()
        (d / "product.md").write_text(
            "---\nstatus: approved\ninject_on: [hello]\nmax_chars: 100\n---\nReal approved content",
        )
        monkeypatch.setattr(mod, "_KNOWLEDGE_DIR", str(d))
        monkeypatch.setattr(mod, "_CANDIDATE_FILES", ("product.md",))
        mod._load_file.cache_clear()
        picks = mod.select_knowledge("hello there")
        assert len(picks) == 1
        assert picks[0][0] == "product.md"
        assert "Real approved content" in picks[0][1]

    def test_approved_file_with_no_matching_keyword_is_not_selected(self, tmp_path, monkeypatch):
        mod = _reload()
        d = tmp_path / "knowledge"
        d.mkdir()
        (d / "product.md").write_text(
            "---\nstatus: approved\ninject_on: [zzzznevermatches]\nmax_chars: 100\n---\ncontent",
        )
        monkeypatch.setattr(mod, "_KNOWLEDGE_DIR", str(d))
        monkeypatch.setattr(mod, "_CANDIDATE_FILES", ("product.md",))
        mod._load_file.cache_clear()
        assert mod.select_knowledge("completely unrelated task") == []


class TestCapsAndBudget:
    def test_per_file_max_chars_truncates(self, tmp_path, monkeypatch):
        mod = _reload()
        d = tmp_path / "knowledge"
        d.mkdir()
        (d / "product.md").write_text(
            "---\nstatus: approved\ninject_on: [hello]\nmax_chars: 5\n---\n1234567890",
        )
        monkeypatch.setattr(mod, "_KNOWLEDGE_DIR", str(d))
        monkeypatch.setattr(mod, "_CANDIDATE_FILES", ("product.md",))
        mod._load_file.cache_clear()
        picks = mod.select_knowledge("hello")
        assert picks[0][1] == "12345"

    def test_total_char_cap_stops_adding_more_files(self, tmp_path, monkeypatch):
        mod = _reload()
        d = tmp_path / "knowledge"
        d.mkdir()
        big = "x" * 50
        (d / "a.md").write_text(f"---\nstatus: approved\ninject_on: [hi]\nmax_chars: 50\n---\n{big}")
        (d / "b.md").write_text(f"---\nstatus: approved\ninject_on: [hi]\nmax_chars: 50\n---\n{big}")
        monkeypatch.setattr(mod, "_KNOWLEDGE_DIR", str(d))
        monkeypatch.setattr(mod, "_CANDIDATE_FILES", ("a.md", "b.md"))
        monkeypatch.setattr(mod, "_TOTAL_CHAR_CAP", 60)
        mod._load_file.cache_clear()
        picks = mod.select_knowledge("hi")
        total = sum(len(c) for _, c in picks)
        assert total <= 60


class TestKeywordSelection:
    def test_empty_folder_returns_nothing(self, tmp_path, monkeypatch):
        mod = _reload()
        d = tmp_path / "knowledge"
        d.mkdir()
        monkeypatch.setattr(mod, "_KNOWLEDGE_DIR", str(d))
        monkeypatch.setattr(mod, "_CANDIDATE_FILES", ("product.md",))
        mod._load_file.cache_clear()
        assert mod.select_knowledge("anything") == []

    def test_build_knowledge_context_empty_when_no_picks(self, tmp_path, monkeypatch):
        mod = _reload()
        d = tmp_path / "knowledge"
        d.mkdir()
        monkeypatch.setattr(mod, "_KNOWLEDGE_DIR", str(d))
        monkeypatch.setattr(mod, "_CANDIDATE_FILES", ("product.md",))
        mod._load_file.cache_clear()
        assert mod.build_knowledge_context("anything") == ""

    def test_build_knowledge_context_wraps_picks_in_a_labeled_block(self, tmp_path, monkeypatch):
        mod = _reload()
        d = tmp_path / "knowledge"
        d.mkdir()
        (d / "product.md").write_text(
            "---\nstatus: approved\ninject_on: [hello]\nmax_chars: 100\n---\nApproved body here",
        )
        monkeypatch.setattr(mod, "_KNOWLEDGE_DIR", str(d))
        monkeypatch.setattr(mod, "_CANDIDATE_FILES", ("product.md",))
        mod._load_file.cache_clear()
        block = mod.build_knowledge_context("hello")
        assert "ORA KNOWLEDGE" in block
        assert "Approved body here" in block

    def test_duplicate_filenames_in_candidate_list_only_loaded_once(self, tmp_path, monkeypatch):
        mod = _reload()
        d = tmp_path / "knowledge"
        d.mkdir()
        (d / "product.md").write_text(
            "---\nstatus: approved\ninject_on: [hello]\nmax_chars: 100\n---\nonce",
        )
        monkeypatch.setattr(mod, "_KNOWLEDGE_DIR", str(d))
        monkeypatch.setattr(mod, "_CANDIDATE_FILES", ("product.md", "product.md"))
        mod._load_file.cache_clear()
        picks = mod.select_knowledge("hello")
        assert len(picks) == 2  # candidate list itself has 2 entries — selection doesn't de-dupe by name
        assert all(c == "once" for _, c in picks)


class TestPathTraversalSafety:
    def test_candidate_files_is_a_fixed_tuple_not_built_from_input(self):
        mod = _reload()
        assert isinstance(mod._CANDIDATE_FILES, tuple)
        for name in mod._CANDIDATE_FILES:
            assert "/" not in name
            assert ".." not in name

    def test_task_string_containing_traversal_sequences_cannot_reach_the_filesystem(self, tmp_path, monkeypatch):
        """The task description is only ever matched as a KEYWORD
        substring, never used to build a path — confirm a hostile
        task string full of traversal sequences still only returns
        files from the real, fixed candidate list."""
        mod = _reload()
        d = tmp_path / "knowledge"
        d.mkdir()
        (d / "product.md").write_text(
            "---\nstatus: approved\ninject_on: [hello]\nmax_chars: 100\n---\nsafe content",
        )
        monkeypatch.setattr(mod, "_KNOWLEDGE_DIR", str(d))
        monkeypatch.setattr(mod, "_CANDIDATE_FILES", ("product.md",))
        mod._load_file.cache_clear()
        hostile = "../../../../etc/passwd ../../secrets.env hello"
        picks = mod.select_knowledge(hostile)
        assert len(picks) == 1
        assert picks[0][0] == "product.md"

    def test_missing_file_on_disk_returns_empty_not_a_crash(self, tmp_path, monkeypatch):
        mod = _reload()
        d = tmp_path / "knowledge"
        d.mkdir()
        monkeypatch.setattr(mod, "_KNOWLEDGE_DIR", str(d))
        monkeypatch.setattr(mod, "_CANDIDATE_FILES", ("does_not_exist.md",))
        mod._load_file.cache_clear()
        assert mod.select_knowledge("anything") == []


class TestMarketingStyleExcluded:
    def test_marketing_style_not_in_candidate_list(self):
        mod = _reload()
        assert "marketing-style.md" not in mod._CANDIDATE_FILES

    def test_marketing_style_never_returned_regardless_of_keyword(self):
        mod = _reload()
        # Even a task that happens to mention "marketing" must never
        # surface marketing-style.md through the engineering injector.
        picks = mod.select_knowledge("marketing style tone voice")
        assert all(name != "marketing-style.md" for name, _ in picks)


class TestRealKnowledgeFilesOnDisk:
    """Sanity checks against the REAL backend/knowledge/ files this
    phase created — confirms they parse, and confirms every one ships
    draft (so nothing is live yet, by design)."""

    def test_all_real_files_parse_without_crashing_and_ship_as_draft(self):
        mod = _reload()
        mod._load_file.cache_clear()
        for filename in mod._CANDIDATE_FILES:
            meta, body = mod._load_file(filename)
            assert meta, f"{filename} has no parseable front-matter"
            assert meta.get("status") == "draft", f"{filename} must ship draft, not pre-approved"
            assert body.strip()

    def test_real_files_currently_inject_nothing_anywhere(self):
        mod = _reload()
        mod._load_file.cache_clear()
        for q in ("what is ora", "pricing", "fix a bug", "security checklist"):
            assert mod.select_knowledge(q) == [], f"{q!r} unexpectedly selected a draft file"

    def test_examples_dir_has_ten_files_all_draft(self):
        import yaml
        ex_dir = os.path.join(os.path.dirname(mod_knowledge_dir()), "knowledge", "examples")
        files = sorted(f for f in os.listdir(ex_dir) if f.endswith(".md"))
        assert len(files) == 10
        for f in files:
            with open(os.path.join(ex_dir, f), encoding="utf-8") as fh:
                text = fh.read()
            import re
            m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
            assert m, f"{f} missing frontmatter"
            meta = yaml.safe_load(m.group(1)) or {}
            assert meta.get("status") == "draft"

    def test_no_secret_shaped_strings_in_any_knowledge_file(self):
        _reload()
        base = os.path.dirname(mod_knowledge_dir())
        kdir = os.path.join(base, "knowledge")
        bad_patterns = ["sk_live_", "AKIA", "-----BEGIN", "ghp_", "xoxb-"]
        for root, _dirs, files in os.walk(kdir):
            for f in files:
                if not f.endswith(".md"):
                    continue
                with open(os.path.join(root, f), encoding="utf-8") as fh:
                    text = fh.read()
                for bad in bad_patterns:
                    assert bad not in text, f"possible real secret shape {bad!r} in {f}"


def mod_knowledge_dir():
    import services.knowledge_injector as mod
    return mod._KNOWLEDGE_DIR


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
