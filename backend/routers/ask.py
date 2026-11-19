"""NLWeb /ask endpoint (read-only, public, no auth, no writes, no external calls).

Answers natural-language questions from the site's own public content
(frontend/public/llms.txt, plus a small built-in index of the public pages
it links to). Implements the minimal NLWeb /ask contract:

- GET  /ask?query=...&streaming=false  (agent-ready.dev C12 /
  isagentready ia.nlweb-ask: JSON with a ``results`` array, each result
  carrying a Schema.org object, ``streaming=false`` for a plain answer)
- POST /ask  {"query": ..., "mode": "list"|"summarize", "streaming": false}
  (NLWeb spec: POST /ask with a JSON body; the response carries the
  required ``_meta`` block and an Answer -- the ``results`` array).

Response shape: {"query_id", "_meta", "results": [{url, name,
schema_object}]}. A query with no matches returns an empty ``results``
array plus a NO_RESULTS failure object, per the NLWeb spec. The detailed
request/response shapes for the site's API live in the linked
openapi.json; this endpoint only answers from public site content.
"""

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()

_SITE = "https://auremcto.com"

# Built-in fallback index of the public pages. Used when llms.txt cannot
# be read (for example a deploy layout without the frontend tree); the
# llms.txt entries are always loaded first when the file is present.
_FALLBACK = [
    ("ORA by Aurem", _SITE, "ORA is an autonomous AI software engineer that reads a GitHub repository and ships code to it."),
    ("Pricing", _SITE + "/pricing", "ORA pricing: Starter $9, Pro $19, Team $49 per month. Annual plans save 20 percent."),
    ("Developers", _SITE + "/developers", "ORA developer docs: REST API, MCP server, and the Aurem Loop harness."),
    ("Aurem Loop harness", _SITE + "/harness", "Download the Aurem Loop, a free local coding harness, version 0.8.0."),
    ("Compare", _SITE + "/compare", "How ORA compares with Cursor, Devin, and other coding agents."),
]

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "what", "which", "who", "whom", "how", "why",
    "when", "where", "this", "that", "these", "those", "it", "its",
    "as", "at", "by", "from", "into", "about", "over", "under",
    "i", "you", "he", "she", "we", "they", "me", "my", "our", "your",
    "can", "could", "should", "would", "may", "might", "will",
    "not", "no", "yes", "if", "then", "than", "so", "such",
}

_LINK_RE = re.compile(
    r"^\s*(?:-\s*)?\[(?P<title>[^\]]+)\]\((?P<url>[^)]+)\)\s*(?::\s*|\s+-\s+)?(?P<desc>.*)$"
)


def _llms_path():
    here = Path(__file__).resolve()
    for base in (here.parents[2], here.parents[1]):
        candidate = base / "frontend" / "public" / "llms.txt"
        if candidate.is_file():
            return candidate
    return None


def _load_entries():
    entries = []
    by_url = {}
    path = _llms_path()
    if path is not None:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            text = ""
        for line in text.splitlines():
            match = _LINK_RE.match(line)
            if not match:
                continue
            url = match.group("url").strip()
            if not url:
                continue
            key = url.rstrip("/")
            title = match.group("title").strip()
            desc = match.group("desc").strip()
            if key in by_url:
                entry = by_url[key]
                extra = (title + " -- " + desc).strip(" -")
                if extra and extra not in entry["description"]:
                    entry["description"] = entry["description"] + " " + extra
                continue
            entry = {"name": title, "url": url, "description": desc}
            by_url[key] = entry
            entries.append(entry)
    for name, url, desc in _FALLBACK:
        if url.rstrip("/") not in by_url:
            by_url[url.rstrip("/")] = {"name": name, "url": url, "description": desc}
            entries.append(by_url[url.rstrip("/")])
    return entries


_ENTRIES = _load_entries()


def _words(text):
    return set(
        token
        for token in re.split(r"[^a-z0-9]+", text.lower())
        if token
    )


def _query_tokens(query):
    return set(
        token
        for token in re.split(r"[^a-z0-9]+", query.lower())
        if token and token not in _STOP
    )


def _score(tokens, entry):
    words = _words(entry["name"] + " " + entry["description"] + " " + entry["url"])
    name_words = _words(entry["name"])
    score = 0
    for token in tokens:
        if token in words:
            score += 2
        elif len(token) >= 4 and any(token in word for word in words):
            score += 1
        if token in name_words:
            score += 1
    if entry["url"].startswith(_SITE):
        score += 1
    return score


# _matches: True when a query token actually occurs in the entry.
# _score() adds a +1 same-site bonus to every entry, so a bare score > 0
# check lets nonsense queries return the whole index.
def _matches(tokens, entry):
    words = _words(entry["name"] + " " + entry["description"] + " " + entry["url"])
    return any(token in words or (len(token) >= 4 and any(token in word for word in words)) for token in tokens)


def search(query, limit=5):
    tokens = _query_tokens(query)
    if not tokens:
        return []
    scored = []
    for entry in _ENTRIES:
        score = _score(tokens, entry)
        if score > 0 and _matches(tokens, entry):
            scored.append((score, entry))
    scored.sort(key=lambda item: -item[0])
    return [entry for _, entry in scored[:limit]]


def _result(entry):
    return {
        "url": entry["url"],
        "name": entry["name"],
        "schema_object": {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "name": entry["name"],
            "url": entry["url"],
            "description": entry["description"],
        },
    }


def _answer(query, mode, streaming):
    results = search(query)
    body = {
        "query_id": "q-" + uuid.uuid4().hex[:12],
        "_meta": {"query": query, "mode": mode, "streaming": bool(streaming)},
        "results": [_result(entry) for entry in results],
    }
    if not results:
        body["error"] = {
            "code": "NO_RESULTS",
            "message": "No public content matched this query.",
        }
    if mode == "summarize" and results:
        top = results[0]
        body["answer"] = "Top match: " + top["name"] + " -- " + top["description"]
    return body


class AskBody(BaseModel):
    query: str = ""
    mode: str = "list"
    streaming: bool = False


@router.get("/ask")
def ask_get(
    query: str = Query(default=""),
    mode: str = "list",
    streaming: bool = False,
):
    return _answer(query, mode, streaming)


@router.post("/ask")
def ask_post(body: AskBody):
    return _answer(body.query, body.mode, body.streaming)
