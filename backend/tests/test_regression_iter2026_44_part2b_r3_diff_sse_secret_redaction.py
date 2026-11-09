"""tests/test_regression_iter2026_44_part2b_r3_diff_sse_secret_redaction.py

Part 2b R3 (SSE-adjacent finding) — routers/fix_pipeline.py's
`_compute_diff_lines` streams the unified diff of a fix's before/after
content DIRECTLY to the browser over SSE (the "fix-diff" event). A fix
for a hardcoded secret would otherwise show the raw secret value in
the "remove" line (and, if the LLM left a literal default, the "add"
line too) in plaintext to anyone watching the Fix Progress Drawer.

Fix: every remove/add/context line is passed through
services.secret_masking.redact_secrets_in_text() before being added
to the SSE payload.
"""
from __future__ import annotations

from routers.fix_pipeline import _compute_diff_lines

_SECRET_VALUE = "abcdef1234567890ZZ"


class TestDiffSseSecretRedaction:
    def test_removed_hardcoded_secret_line_is_masked_in_the_diff_payload(self):
        before = (
            "line1\n"
            f'API_KEY = "{_SECRET_VALUE}"\n'
            "line3\n"
        )
        after = (
            "line1\n"
            "API_KEY = os.environ.get(\"API_KEY\")\n"
            "line3\n"
        )
        diff = _compute_diff_lines(before, after)

        full_text = "\n".join(d["line"] for d in diff)
        assert _SECRET_VALUE not in full_text, (
            "the raw secret value must never reach the SSE payload "
            "sent to the browser"
        )

        remove_lines = [d for d in diff if d["type"] == "remove"]
        assert any("API_KEY" in d["line"] for d in remove_lines), (
            "the key NAME and surrounding code must still be visible "
            "— only the secret VALUE is masked"
        )
        assert any("*" in d["line"] for d in remove_lines), (
            "the masked value must leave a visible asterisk marker"
        )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
