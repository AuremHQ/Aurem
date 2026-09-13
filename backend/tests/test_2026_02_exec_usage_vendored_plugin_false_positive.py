"""
Regression test for the exec_usage false positive reported against
`.agent/skills/plugins/antigravity-awesome-skills-claude/skills/007/
scripts/{full_audit.py, scanners/injection_scanner.py}`.

Root cause: `is_scanner_rule_file()` only recognized AUREM's own
hardcoded scanner-file list. Vendored third-party security-scanner
SKILL plugins (bundled under .agent/skills/plugins/<bundle>/scripts/)
hit the exact same self-referential class of false positive — their
own rule-description strings contain the literal dangerous-code text
(e.g. "exec() usage -- verify input is not user-controlled") they
exist to detect in OTHER code, with zero real exec()/eval() Call
nodes in the file itself (confirmed via `ast.parse`).
"""
from services.scanner_utils import is_scanner_rule_file
from services.vanguard_scanner import scan_text


VENDORED_FULL_AUDIT = (
    ".agent/skills/plugins/antigravity-awesome-skills-claude/"
    "skills/007/scripts/full_audit.py"
)
VENDORED_INJECTION_SCANNER = (
    ".agent/skills/plugins/antigravity-awesome-skills-claude/"
    "skills/007/scripts/scanners/injection_scanner.py"
)


def test_vendored_skill_scanner_files_are_recognized_as_rule_files():
    assert is_scanner_rule_file(VENDORED_FULL_AUDIT) is True
    assert is_scanner_rule_file(VENDORED_INJECTION_SCANNER) is True
    # also matches when nested under a repo checkout prefix
    assert is_scanner_rule_file("myrepo/" + VENDORED_INJECTION_SCANNER) is True


def test_other_vendored_skill_files_are_not_blanket_exempted():
    # A SKILL.md doc or arbitrary plugin code must NOT be exempted —
    # only scanner/audit script conventions inside skill plugin dirs.
    assert is_scanner_rule_file(
        ".agent/skills/plugins/antigravity-awesome-skills-claude/"
        "skills/007/SKILL.md"
    ) is False
    assert is_scanner_rule_file(
        ".agent/skills/plugins/antigravity-bundle-python-pro/app.py"
    ) is False


def test_real_exec_call_in_a_vendored_plugin_would_still_be_caught():
    # Sanity: the exemption is path-based only for the two confirmed
    # scanner-definition files, not a blanket "anything under
    # .agent/skills/plugins/ is safe" rule. A real exec() call in an
    # unrelated plugin file must still be flagged.
    findings = scan_text(
        "exec(user_supplied_code)\n",
        filepath=".agent/skills/plugins/some-other-bundle/run.py",
    )
    names = [f["name"] for f in findings]
    assert "exec_usage" in names


def test_scan_text_still_flags_a_true_exec_usage_in_normal_app_code():
    findings = scan_text("exec(payload)\n", filepath="backend/app.py")
    names = [f["name"] for f in findings]
    assert "exec_usage" in names
