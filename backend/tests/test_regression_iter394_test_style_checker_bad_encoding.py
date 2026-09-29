"""Regression — one badly-encoded test file must not crash the style gate.

`Static-grep threshold on new tests` died with UnicodeDecodeError because
`TeamManagementPanel.phase1.test.jsx` on main held a Windows-1252 em dash
(0x97) in a describe() title, and `_file_exempts_itself` opened it as strict
UTF-8. The checker now tolerates bad bytes, and the file is real UTF-8.
"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "ci_check_test_style", REPO / "backend" / "scripts" / "ci_check_test_style.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_regression_iter394_exemption_check_survives_a_non_utf8_byte(tmp_path):
    bad = tmp_path / "some.test.jsx"
    bad.write_bytes(b'// static-grep-ok: reason kept\ndescribe("A \x97 b", () => {});\n')
    reason = _load_checker()._file_exempts_itself(str(bad))
    assert reason == "reason kept"


def test_regression_iter394_test_files_are_valid_utf8():
    roots = [REPO / "backend" / "tests", REPO / "frontend" / "src"]
    patterns = ("test_*.py", "*_test.py", "*.test.js", "*.test.jsx", "*.spec.js")
    bad = []
    for root in roots:
        for pat in patterns:
            for p in root.rglob(pat):
                try:
                    p.read_bytes().decode("utf-8")
                except UnicodeDecodeError:
                    bad.append(str(p.relative_to(REPO)))
    assert bad == [], f"not valid UTF-8: {bad}"
