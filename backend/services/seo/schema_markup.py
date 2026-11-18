"""
services/seo/schema_markup.py — Inject a JSON-LD <script> into the
<head> of an HTML page, type-detected from the page content.
"""
from __future__ import annotations

import json

def _json_ld_script(payload):
    # Escape <, > and & (and U+2028/U+2029, left literal by ensure_ascii=False) so a value containing </script>
    # cannot close the JSON-LD block and run as HTML. JSON decodes the escapes back to the original characters.
    return json.dumps(payload, indent=2, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

from typing import Optional

from bs4 import BeautifulSoup

from .meta_tags import SeoPatch


def detect_page_type(html: str) -> str:
    """Heuristic page-type detection. Conservative: defaults to WebPage."""
    lower = html.lower()
    # Strongest signals first.
    if '"@type":"product"' in lower or '"@type": "product"' in lower:
        return "Product"
    if 'itemtype="https://schema.org/product"' in lower:
        return "Product"
    if "<article" in lower or "/blog/" in lower:
        return "Article"
    if "faqpage" in lower or "<details" in lower or "data-faq" in lower:
        return "FAQPage"
    if "<form" in lower and ("contact" in lower or "email" in lower):
        return "ContactPage"
    return "WebPage"


def generate_schema(
    page_type: str,
    *,
    title: str = "",
    description: str = "",
    url: str = "",
    author: str = "",
    image: str = "",
    price: str = "",
    currency: str = "USD",
    date_published: str = "",
) -> dict:
    base: dict = {
        "@context": "https://schema.org",
        "@type":    page_type,
    }
    if title:
        base["name"] = title
    if description:
        base["description"] = description
    if url:
        base["url"] = url
    if image:
        base["image"] = image

    if page_type == "Article":
        if title:
            base["headline"] = title
        if author:
            base["author"] = {"@type": "Person", "name": author}
        if date_published:
            base["datePublished"] = date_published
    elif page_type == "Product":
        if price:
            base["offers"] = {
                "@type":         "Offer",
                "price":         price,
                "priceCurrency": currency,
            }
    return base


def patch_schema_markup(
    *,
    path: str,
    html: str,
    title: str = "",
    description: str = "",
    url: str = "",
    author: str = "",
    image: str = "",
    price: str = "",
    currency: str = "USD",
    date_published: str = "",
) -> Optional[SeoPatch]:
    """Inject JSON-LD if the page doesn't already carry one.
    Returns None if the page already has `application/ld+json`."""
    soup = BeautifulSoup(html, "html.parser")
    head = soup.head
    if head is None:
        return None
    if soup.find("script", attrs={"type": "application/ld+json"}) is not None:
        return None

    page_type = detect_page_type(html)
    schema = generate_schema(
        page_type,
        title=title, description=description, url=url,
        author=author, image=image,
        price=price, currency=currency,
        date_published=date_published,
    )
    tag = soup.new_tag("script", attrs={"type": "application/ld+json"})
    tag.string = _json_ld_script(schema)
    head.append(tag)
    return SeoPatch(
        path=path,
        before=html,
        after=str(soup),
        action="modify",
        reason=f"injected JSON-LD schema (@type={page_type})",
    )

# --- Merged from former visibility kit (schema) (Fix #20) ---
# Visibility-kit structured data. Uses the canonical _json_ld_script above;
# the visibility copy escaped the same characters but had no indent and no
# U+2028/U+2029 handling, so it was deleted instead of kept.
_START = "<!-- AUREM Visibility Kit — auremcto.com (managed block) -->"
_END = "<!-- end AUREM Visibility Kit -->"


def render_json_ld(site: dict) -> str:
    """`site` = {name, url, logo_url, sameAs, faqs, author}."""
    org_id = f"{site['url'].rstrip('/')}/#organization"
    blocks = [{
        "@context": "https://schema.org",
        "@type": "Organization",
        "@id": org_id,
        "name": site["name"], "url": site["url"],
        **({"logo": site["logo_url"]} if site.get("logo_url") else {}),
        **({"sameAs": site["sameAs"]} if site.get("sameAs") else {}),
    }, {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": site["name"], "url": site["url"],
        "potentialAction": {
            "@type": "SearchAction",
            "target": f"{site['url'].rstrip('/')}/?q={{search_term_string}}",
            "query-input": "required name=search_term_string",
        },
    }]
    faqs = site.get("faqs") or []
    if faqs:
        blocks.append({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [{
                "@type": "Question", "name": f["question"],
                "acceptedAnswer": {"@type": "Answer", "text": f["answer"]},
            } for f in faqs],
        })
    author = site.get("author")
    if author and author.get("name"):
        # t_author_schema — Person+sameAs emitted ONLY when author data present.
        person = {"@context": "https://schema.org", "@type": "Person", "name": author["name"]}
        if author.get("sameAs"):
            person["sameAs"] = author["sameAs"]
        blocks.append(person)

    scripts = "\n".join(
        f'<script type="application/ld+json">{_json_ld_script(b)}</script>' for b in blocks
    )
    return f"{_START}\n{scripts}\n{_END}\n"


def apply_managed_block(existing_head: str, site: dict) -> str:
    """R6 idempotent re-apply — replaces only our own delimited block."""
    block = render_json_ld(site)
    if _START in existing_head and _END in existing_head:
        pre, rest = existing_head.split(_START, 1)
        _, post = rest.split(_END, 1)
        return pre + block + post
    return existing_head.rstrip("\n") + "\n" + block
