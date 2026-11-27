"""L8 split tests — backend/services/orchestrator_persona.py (moved out of
services/orchestrator.py).

Proves the pure move preserved the persona / system-prompt subsystem:
  * Re-export identity: every one of the 29 moved names in
    services.orchestrator IS the same object as in
    services.orchestrator_persona (so existing
    `from services.orchestrator import build_persona` imports keep working).
  * Behaviour: layer selection, persona composition, and the classifier
    helpers behave as the moved code documents (greeting -> CORE only,
    action verb -> EXECUTE, URL/connected repo -> REPO, founder-ask
    escalation thresholds, slice partition invariant).
  * The main entry point chat_with_tools still lives in orchestrator.py.

services.orchestrator's heavy siblings (llm, tools_bridge, local_tools,
skill_usage) are stubbed — this file tests the split, not the LLM stack.
The real services.identity module is used (it is a leaf module).

Run from backend/: python -m pytest tests/test_p1c_orchestrator_persona.py
"""
import re
import sys
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

MOVED_NAMES = [
    "_BT", "_TOOL_HELP_TEMPLATE", "AUREM_CTO_PERSONA", "_SECTION_LAYER",
    "_slice_persona_into_layers",
    "_PERSONA_INTRO", "_PERSONA_CORE_BODY", "_PERSONA_EXECUTE_BODY",
    "_PERSONA_REPO_BODY", "_PERSONA_CORE", "_PERSONA_EXECUTE", "_PERSONA_REPO",
    "_STRONG_EXECUTE_RX", "_SOFT_EXECUTE_RX", "_CONFIRM_RX", "_PATH_RX",
    "_URL_RX", "_READ_VERB_RX", "_HOW_MANY_RX",
    "_wants_execute", "_should_inject_tool_reminder", "_wants_repo",
    "build_persona", "persona_layers_for",
    "_CODE_KEYWORDS", "_FOUNDER_ASK_RX", "_count_founder_asks",
    "_founder_escalation_note", "_is_code_task",
]


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    # Delegate any other attribute to the REAL module so `from <name>
    # import <other>` keeps working for every other test file
    # collected after this one (same fix as tests/p1e_stubs.py).
    # The old bare fake for services.llm (only call_llm_with_meta)
    # broke `from services.llm import cap_for/_GLM_MODEL` for later
    # files. Defined attributes win, so this file's own tests still
    # see the fakes.
    _real = [None]

    def __getattr__(attr, _name=name, _real=_real):
        if _real[0] is None:
            import importlib
            saved = sys.modules.pop(_name, None)
            try:
                _real[0] = importlib.import_module(_name)
            finally:
                if saved is not None:
                    sys.modules[_name] = saved
        return getattr(_real[0], attr)

    mod.__getattr__ = __getattr__
    sys.modules[name] = mod
    return mod


def _load_orchestrator():
    """Import services.orchestrator with its heavy siblings stubbed."""
    import services.orchestrator_persona as persona  # real module, no stubs needed
    _noop = lambda *a, **k: None
    _stub("services.llm", call_llm_with_meta=_noop)
    _stub("services.tools_bridge",
          list_tools=_noop, invoke_tool=_noop, extract_tool_calls=_noop,
          strip_tool_calls=_noop, detect_unsourced_citations=_noop)
    _stub("services.local_tools",
          TOOL_SPECS=[], invoke_local_tool=_noop, _is_safe_repo_path=lambda p: True)
    _stub("services.skill_usage", log_skill_use=_noop)
    import services.orchestrator as orch
    return persona, orch


PERSONA, ORCH = _load_orchestrator()


def test_reexports_are_identical_objects():
    assert len(MOVED_NAMES) == 29
    for name in MOVED_NAMES:
        assert hasattr(ORCH, name), f"orchestrator.py lost re-export: {name}"
        assert getattr(ORCH, name) is getattr(PERSONA, name), \
            f"{name} is not the same object after the split"


def test_persona_constants_shape():
    assert isinstance(PERSONA.AUREM_CTO_PERSONA, str)
    assert len(PERSONA.AUREM_CTO_PERSONA) > 10_000  # ~20k-char monolith
    assert PERSONA.AUREM_CTO_PERSONA.startswith("You are ORA")
    assert PERSONA._PERSONA_CORE and PERSONA._PERSONA_EXECUTE and PERSONA._PERSONA_REPO
    assert "tool_call" in PERSONA._TOOL_HELP_TEMPLATE
    assert isinstance(PERSONA._SECTION_LAYER, dict)
    assert all(v in ("core", "execute", "repo")
               for v in PERSONA._SECTION_LAYER.values())


def test_slice_partitions_every_section_exactly_once():
    intro, core, execute, repo = PERSONA._slice_persona_into_layers(
        PERSONA.AUREM_CTO_PERSONA)
    assert intro and core and execute and repo

    def headings(text):
        return set(re.findall(r"^# (.+)$", text, re.M))

    layers = {"intro": headings(intro), "core": headings(core),
              "execute": headings(execute), "repo": headings(repo)}
    all_headings = headings(PERSONA.AUREM_CTO_PERSONA)
    assert all_headings, "no sections found in persona"
    assert set().union(*layers.values()) == all_headings
    names = sorted(layers)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not layers[a] & layers[b], f"heading in both {a} and {b}"
    assert len(layers["core"]) == 9 and len(layers["execute"]) == 9
    assert len(layers["repo"]) == 2 and len(layers["intro"]) == 0


def test_layer_selection():
    assert PERSONA.persona_layers_for("hi") == ["core"]
    assert PERSONA.persona_layers_for("fix auth.py") == ["core", "execute"]
    assert PERSONA.persona_layers_for("check https://github.com/x/y") == \
        ["core", "execute", "repo"]
    # greeting composes to CORE exactly — no EXECUTE/REPO surcharge
    assert PERSONA.build_persona("hi") == PERSONA._PERSONA_CORE
    # action turn contains the execute layer body, repo turn the repo body
    assert PERSONA._PERSONA_EXECUTE in PERSONA.build_persona("fix auth.py")
    assert PERSONA._PERSONA_REPO in PERSONA.build_persona(
        "check https://github.com/x/y")


def test_execute_and_repo_classifiers():
    assert PERSONA._wants_execute("fix auth.py", False, None) is True
    assert PERSONA._wants_execute("explain JWT", False, None) is False
    assert PERSONA._wants_execute("explain auth.py", False, None) is True
    assert PERSONA._wants_execute("", False, None) is False
    assert PERSONA._wants_repo("see https://example.com/x", "") is True
    assert PERSONA._wants_repo("hi", "CONNECTED REPO CONTEXT") is True
    assert PERSONA._wants_repo("hi", "") is False
    assert PERSONA._should_inject_tool_reminder("read auth.py", True) is True
    assert PERSONA._should_inject_tool_reminder("read auth.py", False) is False
    assert PERSONA._is_code_task("fix the bug", []) is True
    assert PERSONA._is_code_task("hi", []) is False


def test_founder_ask_escalation_thresholds():
    assert PERSONA._count_founder_asks([], "who is the founder?") == 1
    assert PERSONA._count_founder_asks(
        ["[USER] who is the founder", "[ASSISTANT] no"], "who is the founder?") == 2
    assert PERSONA._count_founder_asks([], "fix auth.py") == 0
    assert PERSONA._founder_escalation_note(0) == ""
    assert PERSONA._founder_escalation_note(2) == ""
    note3 = PERSONA._founder_escalation_note(3)
    assert "FOUNDER ASK #3" in note3
    assert "auremcto.com/support" in note3
    assert "linkedin.com" not in note3  # LinkedIn only at 6+
    assert "FOUNDER ASK #4" in PERSONA._founder_escalation_note(4)
    assert "auremcto.com/support" in PERSONA._founder_escalation_note(5)
    note6 = PERSONA._founder_escalation_note(6)
    assert "FOUNDER ASK #6+" in note6
    assert "linkedin.com/in/tejinder-sandhu" in note6


def test_chat_with_tools_still_lives_in_orchestrator():
    assert callable(ORCH.chat_with_tools)
    # and it is defined there, not re-exported from the persona module
    assert not hasattr(PERSONA, "chat_with_tools")
    for helper in ("_chat_with_tools_build_base_system",
                   "_chat_with_tools_build_extra_context",
                   "_chat_with_tools_init_loop_state"):
        assert hasattr(ORCH, helper), helper
