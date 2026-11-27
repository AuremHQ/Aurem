"""
services/seo/answer_blocks.py — Visibility Kit 2.0 (P0-3): GEO apply.

Promotes the catalog item `answer_blocks` from advisory (weight 7,
"we list your gaps — you decide") to auto (weight 15): the Kit now
WRITES answer-first passages — one <section id="ai-answers"> injected
before </body> (or a standalone visibility/answers.md page when there
is no HTML entry file) — plus the same FAQs as a FAQPage block in the
JSON-LD (kit_engine passes them into the schema dict).

Hard rule, inherited from the catalog copy and the Kit's no-invention
policy: every answer must come from facts the project already has
(site_meta.faqs, the site description, the pricing note). An answer
must be 40–60 words AND contain at least one real number (a price, a
count, a time). A question with no source fact or no number is
SKIPPED and reported — never padded with invented copy. That is why
build_answer_blocks() returns `skipped` alongside `faqs_used`.

Pure functions only (no GitHub, no DB, no LLM) — the whole module is
unit-testable without a network call.
"""
from __future__ import annotations

import html
import re

_START = "<!-- AUREM Visibility Kit — auremcto.com (managed block: ai-answers) -->"
_END = "<!-- end AUREM Visibility Kit: ai-answers -->"

_MIN_WORDS = 40
_MAX_WORDS = 60


def _word_count(text: str) -> int:
    return len((text or "").split())


def _has_number(text: str) -> bool:
    return bool(re.search(r"\d", text or ""))


def _trim_to_max_words(text: str, max_words: int = _MAX_WORDS) -> str:
    """Trim to <= max_words, preferring a sentence boundary at or
    below the cap; hard-cut at the cap only if no boundary exists."""
    words = (text or "").split()
    if len(words) <= max_words:
        return text.strip()
    cut = " ".join(words[:max_words])
    # Last sentence end within the cut keeps the answer grammatical.
    ends = [m.end() for m in re.finditer(r"[.!?]\s", cut)]
    if ends and ends[-1] > len(cut) * 0.6:
        return cut[: ends[-1]].strip()
    return cut.rstrip(".,;:") + "…"


def _normalise_faq(faq: dict) -> dict | None:
    """Validate one source FAQ. Returns a cleaned {question, answer}
    or None when it cannot be used honestly (empty, too short to be
    an answer-first passage, or number-free)."""
    q = str((faq or {}).get("question") or "").strip()
    a = str((faq or {}).get("answer") or "").strip()
    if not q or not a:
        return None
    if not q.endswith("?"):
        q = q + "?"
    if _word_count(a) < _MIN_WORDS:
        return None  # too short to be a 40–60 word answer-first passage
    if not _has_number(a):
        return None  # an answer engine quotes numbers; a number-free
        # answer is filler, and we will not invent a number for it
    return {"question": q, "answer": _trim_to_max_words(a)}


def default_faqs(
    site_name: str, description: str = "", pricing_note: str = "",
) -> list[dict]:
    """Deterministic template FAQs, built ONLY from supplied facts.
    Each template answer IS the supplied fact text (trimmed to 60
    words) — no filler sentences are appended, because any sentence
    not in the description/pricing would be an invented claim. A
    template whose fact text is under 40 words or number-free is
    dropped by _normalise_faq downstream and reported in `skipped`."""
    out: list[dict] = []
    desc = (description or "").strip()
    price = (pricing_note or "").strip()
    if desc and _has_number(desc):
        out.append({
            "question": f"What is {site_name}?",
            "answer": desc,
        })
    if price and _has_number(price):
        out.append({
            "question": f"How much does {site_name} cost?",
            "answer": price,
        })
    return out


def build_answer_blocks(
    *,
    site_name: str,
    site_url: str = "",
    description: str = "",
    pricing_note: str = "",
    faqs: list[dict] | None = None,
    max_faqs: int = 10,
) -> dict:
    """Build the answer-block artefacts for a project.

    Input: project name/description/pricing + 5–10 source FAQs
    (project.site_meta.faqs). Output: visibility/answers.md markdown
    + <section id="ai-answers"> HTML + the normalised FAQ list (also
    fed to the JSON-LD FAQPage block by the caller). FAQs that fail
    validation are listed in `skipped` with the reason — the PR body
    note surfaces the count, never the invented copy.
    """
    used: list[dict] = []
    skipped: list[dict] = []
    source = list(faqs or [])
    if not source:
        # Deterministic template from README/pricing facts only.
        for faq in default_faqs(site_name, description, pricing_note):
            source.append(faq)
        if not source:
            skipped.append({
                "question": "(template)",
                "reason": "no source FAQs and no description/pricing "
                          "fact carrying a real number",
            })
    for faq in source[:max_faqs]:
        clean = _normalise_faq(faq)
        if clean:
            used.append(clean)
        else:
            skipped.append({
                "question": str((faq or {}).get("question") or "(unnamed)")[:120],
                "reason": "answer is under 40 words, has no number, or "
                          "is empty — skipped rather than invented",
            })
    return {
        "faqs_used": used,
        "skipped": skipped,
        "markdown": render_answers_md(site_name, site_url, used),
        "html": render_answers_section(used),
    }


def render_answers_md(site_name: str, site_url: str, faqs: list[dict]) -> str:
    """visibility/answers.md — one H2 per question, 40–60 word
    answer, at least one number per answer (guaranteed by
    _normalise_faq upstream)."""
    lines = [f"# {site_name} — Answers", ""]
    if site_url:
        lines += [f"> {site_url}", ""]
    for faq in faqs:
        lines += [f"## {faq['question']}", "", faq["answer"], ""]
    lines.append(
        "_Answers written by AUREM Visibility Kit from the site's own "
        "description and pricing — no claims beyond those sources._")
    return "\n".join(lines) + "\n"


def render_answers_section(faqs: list[dict]) -> str:
    """<section id="ai-answers"> for injection before </body>."""
    parts = [f"{_START}", '<section id="ai-answers">']
    for faq in faqs:
        parts.append(f"<h2>{html.escape(faq['question'])}</h2>")
        parts.append(f"<p>{html.escape(faq['answer'])}</p>")
    parts.append("</section>")
    parts.append(_END)
    return "\n".join(parts) + "\n"


def apply_managed_block(existing_html: str, section: str) -> str:
    """Idempotent re-apply: replace only our own delimited block, or
    inject before </body> on first apply. Caller owns the no-</body>
    conflict check (same convention as preferred_sources)."""
    if _START in existing_html and _END in existing_html:
        pre, rest = existing_html.split(_START, 1)
        _, post = rest.split(_END, 1)
        return pre + section + post
    return existing_html.replace("</body>", section + "</body>", 1)
