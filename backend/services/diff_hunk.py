"""services/diff_hunk.py — Part VI P1 (ORA audit).

Minimal unified-diff applier for surgical self-heal.

The Loop verify-phase healer used to ask the LLM for the COMPLETE
file body for every fix — even a one-line lint fix — so the model
re-emitted (and could drift) every untouched line.  A unified diff
describes ONLY the changed lines; applying it programmatically
guarantees every other line stays byte-identical.

`apply_unified_diff` is deliberately small and strict:
  * one file only (a second `--- ` header after hunks start rejects)
  * hunk header counts must match the body exactly (catches a
    truncated model reply before it can corrupt a file)
  * context + removed lines must match the file — at the stated
    position, or at exactly ONE other position (anchor search for
    the model's frequent off-by-N line numbers)
  * hunks apply in order, non-overlapping
  * anything malformed returns None — the caller falls back to the
    full-file rewrite, so a bad diff can never corrupt a file

No I/O, no subprocess, no third-party deps.  Pure string surgery so
it is unit-testable without the LLM stack.  Kept as its own module
so the Parliament healer (the live-path healer since Iter 212m-150)
can adopt it later without touching loop_verify.
"""
from __future__ import annotations

import re
from typing import Optional

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _parse_hunks(diff_text: str):
    """Parse unified diff text.

    Returns (hunks, error).  Each hunk is a dict with old_start /
    old_count / new_start / new_count and a "lines" list of
    (kind, text) where kind is " " (context), "-" (removed),
    "+" (added) or "\\\\" ("\\ No newline at end of file" marker).
    The `--- a/...` / `+++ b/...` file headers are optional; the
    `@@` hunk headers are not.
    """
    lines = diff_text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    hunks: list[dict] = []
    cur: Optional[dict] = None
    seen_minus_header = False
    for ln in lines:
        if ln.startswith("--- "):
            if hunks or cur is not None:
                return None, "multi-file diff"
            seen_minus_header = True
            continue
        if ln.startswith("+++ "):
            if not seen_minus_header:
                return None, "+++ without ---"
            continue
        if ln.startswith("@@ "):
            m = _HUNK_RE.match(ln)
            if not m:
                return None, "malformed hunk header"
            cur = {
                "old_start": int(m.group(1)),
                "old_count": int(m.group(2) or 1),
                "new_start": int(m.group(3)),
                "new_count": int(m.group(4) or 1),
                "lines": [],
            }
            hunks.append(cur)
            continue
        if ln.startswith("\\ "):
            if cur is None:
                return None, "marker outside hunk"
            cur["lines"].append(("\\", ln))
            continue
        if cur is None:
            if ln.strip() == "":
                continue
            return None, "content outside hunk"
        if ln[:1] in (" ", "-", "+"):
            cur["lines"].append((ln[0], ln[1:]))
        elif ln == "":
            # An empty line inside a hunk body is a context line
            # whose body is empty (diff omits the trailing space).
            cur["lines"].append((" ", ""))
        else:
            return None, "bad hunk body line"
    if not hunks:
        return None, "no hunks"
    for h in hunks:
        old_n = sum(1 for t, _ in h["lines"] if t in (" ", "-"))
        new_n = sum(1 for t, _ in h["lines"] if t in (" ", "+"))
        if old_n != h["old_count"] or new_n != h["new_count"]:
            return None, "hunk count mismatch"
    return hunks, None


def _find_anchor(lines: list[str], old_text: list[str], start_at: int) -> Optional[int]:
    """Find `old_text` in `lines` at or after `start_at`.

    Returns the 0-based index, or None when there is no match or
    more than one match (ambiguous — refuse rather than guess).
    """
    if not old_text:
        return None
    hits = []
    for i in range(start_at, len(lines) - len(old_text) + 1):
        if lines[i:i + len(old_text)] == old_text:
            hits.append(i)
            if len(hits) > 1:
                return None
    return hits[0] if hits else None


def apply_unified_diff(content: str, diff_text: str) -> Optional[str]:
    """Apply a unified diff to `content`.

    Returns the new file body, or None when the diff is malformed
    or does not apply cleanly.  Never raises on bad input.
    """
    if not diff_text or not diff_text.strip():
        return None
    hunks, err = _parse_hunks(diff_text)
    if err is not None or not hunks:
        return None

    orig_lines = content.split("\n")
    had_trailing_nl = content.endswith("\n") or content == ""
    if had_trailing_nl and orig_lines and orig_lines[-1] == "":
        orig_lines.pop()

    lines = list(orig_lines)
    delta = 0  # net lines added by hunks applied so far
    cursor = 0  # next hunk must start at or after this 0-based index
    no_trailing_nl = False
    for h in hunks:
        old_text = [t for k, t in h["lines"] if k in (" ", "-")]
        new_text = [t for k, t in h["lines"] if k in (" ", "+")]
        # Preferred position: stated old_start in ORIGINAL numbering,
        # shifted by the net delta of hunks already applied.
        want = h["old_start"] - 1 + delta
        if h["old_count"] == 0:
            # Pure insertion: nothing to match; trust the position.
            # In unified diff, -l,0 means "insert after original
            # line l" (l=0 is before line 1), so the 0-based index
            # is l itself, shifted by hunks already applied.
            pos = h["old_start"] + delta
            if pos < cursor or pos > len(lines):
                return None
        else:
            pos = None
            if 0 <= want < len(lines) and lines[want:want + len(old_text)] == old_text:
                pos = want
            else:
                # Anchor search: the model often gets the line number
                # wrong by a few.  Accept exactly one match, else fail.
                pos = _find_anchor(lines, old_text, cursor)
            if pos is None or pos < cursor:
                return None
        lines[pos:pos + len(old_text)] = new_text
        delta += len(new_text) - len(old_text)
        cursor = pos + len(new_text)
        # A "\ No newline at end of file" marker after a "+" line
        # that ends the final hunk means the new file has no trailing
        # newline.
        body = h["lines"]
        if body and body[-1][0] == "\\" and len(body) >= 2 and body[-2][0] == "+":
            no_trailing_nl = True

    if not lines:
        return ""
    out = "\n".join(lines)
    if not no_trailing_nl and had_trailing_nl:
        out += "\n"
    return out
