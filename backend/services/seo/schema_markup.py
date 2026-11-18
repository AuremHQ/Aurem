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
