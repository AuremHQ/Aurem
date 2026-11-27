"""
tests/test_kit_answer_blocks.py — Visibility Kit 2.0 P0-3.

answer_blocks is now AUTO: build_answer_blocks() writes a
<section id="ai-answers"> + visibility/answers.md from source FAQs
or a deterministic template. Hard rule under test: every answer is
40–60 words AND carries a real number; anything else is SKIPPED,
never padded with invented copy. Pure functions — no network.
"""
from services.seo import answer_blocks
from services.seo.answer_blocks import (
    apply_managed_block,
    build_answer_blocks,
    render_answers_md,
    render_answers_section,
)

# 41 words, carries numbers (12, 40, 5, 30, 2). Counted by hand.
GOOD_ANSWER = (
    "Acme sells handmade leather wallets in 12 colours, ships to 40 "
    "countries within 5 business days, and offers free returns for 30 "
    "days after delivery, with each wallet carrying a 2 year warranty "
    "against stitching faults and hardware defects, starting today."
)
assert len(GOOD_ANSWER.split()) == 41

# ~45 words, deliberately number-free.
NUMBER_FREE_ANSWER = (
    "Our team answers every question with care and attention to detail, "
    "covering product choices, sizing guidance, material questions, "
    "delivery options, return policies, warranty terms, and general "
    "support for customers who want clear help before they decide to "
    "buy anything from our store today."
)
assert not any(ch.isdigit() for ch in NUMBER_FREE_ANSWER)


def test_answer_blocks_valid_faq_used_with_number_and_word_count():
    result = build_answer_blocks(
        site_name="Acme", site_url="https://acme.test",
        faqs=[{"question": "What does Acme sell?", "answer": GOOD_ANSWER}])
    assert len(result["faqs_used"]) == 1
    assert result["skipped"] == []
    used = result["faqs_used"][0]
    assert 40 <= len(used["answer"].split()) <= 60
    assert any(ch.isdigit() for ch in used["answer"])
    assert "## What does Acme sell?" in result["markdown"]
    assert '<section id="ai-answers">' in result["html"]
    assert "<h2>What does Acme sell?</h2>" in result["html"]


def test_answer_blocks_short_and_number_free_faqs_skipped_not_invented():
    result = build_answer_blocks(
        site_name="Acme",
        faqs=[
            {"question": "What does Acme sell?", "answer": GOOD_ANSWER},
            {"question": "Fast?", "answer": "Fast shipping."},
            {"question": "Support?", "answer": NUMBER_FREE_ANSWER},
        ])
    assert len(result["faqs_used"]) == 1
    assert len(result["skipped"]) == 2
    assert "Fast shipping" not in result["markdown"]
    assert "Our team answers" not in result["markdown"]


def test_answer_blocks_template_uses_only_supplied_facts():
    desc = GOOD_ANSWER  # 41 words, has numbers — usable as-is
    result = build_answer_blocks(
        site_name="Acme", description=desc, pricing_note="")
    assert len(result["faqs_used"]) == 1
    assert result["faqs_used"][0]["question"] == "What is Acme?"
    # No filler sentence was appended: the answer IS the description.
    assert result["faqs_used"][0]["answer"].startswith("Acme sells handmade")


def test_answer_blocks_no_facts_means_nothing_written():
    result = build_answer_blocks(site_name="Acme", description="", pricing_note="")
    assert result["faqs_used"] == []
    assert result["skipped"] != []
    assert "## " not in result["markdown"]


def test_answer_blocks_markdown_and_section_agree():
    faqs = [{"question": "What does Acme sell?", "answer": GOOD_ANSWER}]
    result = build_answer_blocks(site_name="Acme", site_url="https://acme.test", faqs=faqs)
    md = render_answers_md("Acme", "https://acme.test", result["faqs_used"])
    section = render_answers_section(result["faqs_used"])
    assert md == result["markdown"]
    assert section == result["html"]
    assert GOOD_ANSWER.split()[0] in section


def test_answer_blocks_inject_before_body_and_idempotent():
    html = "<html><head></head><body><p>x</p></body></html>"
    section = render_answers_section(
        [{"question": "Q?", "answer": GOOD_ANSWER}])
    once = apply_managed_block(html, section)
    assert once.index('<section id="ai-answers">') < once.index("</body>")
    twice = apply_managed_block(once, section)
    assert twice.count(answer_blocks._START) == 1
    assert twice.count('<section id="ai-answers">') == 1
