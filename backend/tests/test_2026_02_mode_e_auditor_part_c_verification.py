"""
tests/test_2026_02_mode_e_auditor_part_c_verification.py — Part C
regression test.

Real incident: a bulk "N issues found" Mode E audit report included a
"duplicate font imports" finding that did not exist on inspection —
the LLM's free-text-parsed ISSUE/FILE blocks had zero grounding check
against the files it was actually shown. `verify_llm_findings` closes
that gap deterministically (no second LLM call).
"""
from services.mode_e_auditor import verify_llm_findings


FILE_BLOCKS = {
    "src/App.jsx": (
        "import React from 'react';\n"
        "function App() {\n  return <div className='app'>hi</div>;\n}\n"
        "export default App;\n"
    ),
    "src/auth.py": (
        "def check_token(token):\n"
        "    if not token:\n        raise ValueError('missing token')\n"
        "    return validate(token)\n"
    ),
}


def test_fabricated_finding_with_no_grounding_in_the_real_file_is_dropped():
    # The exact reported shape: a finding whose description has ZERO
    # keyword overlap with the claimed file's real content.
    findings = [{
        "description": "duplicate font imports increase bundle size",
        "filepath": "src/App.jsx", "severity": "low", "source": "llm",
    }]
    out = verify_llm_findings(findings, FILE_BLOCKS)
    assert out == []


def test_finding_referencing_a_file_that_does_not_exist_at_all_is_dropped():
    findings = [{
        "description": "hardcoded secret key in config",
        "filepath": "src/config/secrets.py", "severity": "critical", "source": "llm",
    }]
    out = verify_llm_findings(findings, FILE_BLOCKS)
    assert out == []


def test_genuine_finding_with_real_keyword_overlap_is_kept():
    findings = [{
        "description": "check_token does not validate token expiry before use",
        "filepath": "src/auth.py", "severity": "medium", "source": "llm",
    }]
    out = verify_llm_findings(findings, FILE_BLOCKS)
    assert len(out) == 1
    assert out[0]["verified"] is True
    assert out[0]["filepath"] == "src/auth.py"


def test_abbreviated_path_from_the_llm_still_resolves_to_the_real_file():
    findings = [{
        "description": "auth token check missing validate call guard",
        "filepath": "auth.py", "severity": "medium", "source": "llm",
    }]
    out = verify_llm_findings(findings, FILE_BLOCKS)
    assert len(out) == 1
    assert out[0]["filepath"] == "src/auth.py"  # resolved to the REAL path


def test_never_raises_and_never_drops_everything_on_a_guard_bug():
    # A finding missing the expected keys should not crash verification.
    out = verify_llm_findings([{}], FILE_BLOCKS)
    assert isinstance(out, list)
