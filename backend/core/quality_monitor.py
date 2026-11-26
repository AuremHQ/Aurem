"""
core/quality_monitor.py — Iter 212m-154

Silent, non-blocking output quality scorer.  Fires as
`asyncio.create_task` AFTER the user has received their reply, so it
NEVER affects user-facing latency.  Writes to `quality_scores` Mongo
collection; raises drift alerts to `quality_alerts` when the last 10
turns' average drops more than 0.15 below the previous 40.

Scoring is heuristic only — no extra LLM call.  Costs: ~0 ms, $0.
Tunable thresholds live at the top of the class for future ops work.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger("aurem-dev.quality_monitor")


_HALLUCINATION_SIGNALS = (
    "as of my knowledge cutoff",
    "i don't have access to real-time",
    "i cannot browse the internet",
    "as an ai language model",
    "i apologize, but i",
)
_REFUSAL_SIGNALS = (
    "i cannot", "i'm unable to", "i won't",
    "that's not something i can",
)

# Part XII (d2) — topical-overlap stopwords. Content words are what
# remain after these are dropped; a reply sharing NO content word
# with a substantive user question is off-topic by construction.
_TOPICAL_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "to", "of", "in", "on", "for",
    "with", "is", "are", "was", "were", "be", "been", "do", "does",
    "did", "how", "what", "why", "when", "where", "which", "who",
    "i", "you", "he", "she", "it", "we", "they", "my", "your", "his",
    "her", "our", "their", "me", "him", "us", "them", "this", "that",
    "these", "those", "as", "at", "by", "from", "not", "no", "yes",
    "am", "an", "can", "could", "should", "would", "please", "help",
    "want", "need", "get", "got", "just", "like", "know", "tell",
})

_WORD_RE = re.compile(r"[a-z0-9]+")


def _content_words(text: str) -> set[str]:
    """Lowercased alphanumeric tokens minus stopwords, lightly
    stemmed (first 6 chars) so `validate`/`validation` and
    `password`/`passwords` count as the same topic instead of a
    miss."""
    out: set[str] = set()
    for w in _WORD_RE.findall((text or "").lower()):
        if w in _TOPICAL_STOPWORDS:
            continue
        out.add(w[:6] if len(w) > 5 else w)
    return out


class QualityMonitor:
    """Scores LLM outputs silently after delivery."""

    ALERT_THRESHOLD = 0.45
    DRIFT_WINDOW    = 50
    DRIFT_TRIGGER   = 0.15

    def __init__(self, db=None):
        self._db = db

    # ── Public entry point — non-blocking ────────────────────────
    async def score_async(self, *, response: str, user_message: str,
                          tier: str, session_id: Optional[str],
                          tenant_id: Optional[str]) -> None:
        try:
            score_data = self._compute_score(response, user_message, tier)
            await self._persist(score_data, session_id, tenant_id, tier,
                                user_message, response)
            await self._check_drift(tenant_id)
        except Exception as e:                          # noqa: BLE001
            logger.warning("quality_monitor error: %r", e)

    # ── Heuristic scoring (no LLM) ───────────────────────────────
    def _compute_score(self, response: str, user_msg: str,
                       tier: str) -> dict[str, Any]:
        if not response:
            return {"score": 0.0, "flags": ["empty_response"],
                    "word_count": 0, "tier": tier}

        score = 1.0
        flags: list[str] = []
        text = response.lower()
        words = len(response.split())

        # 1) Length appropriateness.
        if tier == "casual" and words > 150:
            score -= 0.15
            flags.append("casual_too_long")
        if tier == "agentic" and words < 20:
            score -= 0.25
            flags.append("agentic_too_short")

        # 2) Hallucination signals (known canned phrases).
        for sig in _HALLUCINATION_SIGNALS:
            if sig in text:
                score -= 0.20
                flags.append(f"hallucination_signal:{sig[:30]}")

        # 3) Repetition check on sentence heads.
        sentences = [s.strip() for s in response.split(".") if s.strip()]
        if len(sentences) > 3:
            uniq = len({s[:40] for s in sentences})
            repeat_ratio = 1 - (uniq / len(sentences))
            if repeat_ratio > 0.4:
                score -= 0.20
                flags.append("high_repetition")

        # 4) Code fence sanity for agentic responses.
        if tier == "agentic" and "```" in response:
            code_blocks = response.split("```")
            # Each non-empty fenced block contributes 2 splits; need ≥3
            # parts for a non-empty pair.
            if len(code_blocks) < 3:
                score -= 0.10
                flags.append("empty_code_block")

        # 5) Refusal detection for agentic tier.
        if tier == "agentic":
            for sig in _REFUSAL_SIGNALS:
                if sig in text:
                    score -= 0.30
                    flags.append("unexpected_refusal")
                    break

        # 6) Topical overlap with the user's message (Part XII d2).
        # `_compute_score` used to ignore `user_msg` entirely, so a
        # fluent banana-bread recipe answering a Signup.jsx question
        # scored a perfect 1.0 — indistinguishable from a perfect
        # answer, and the drift detector could never fire on topical
        # drift. Content-word overlap between question and reply is
        # now scored: zero overlap on a substantive question cannot
        # score above 0.5. Skipped when the question has too few
        # content words to judge (< 3 — e.g. "hello", "thanks" —
        # where a low-overlap reply can still be fine).
        topical_overlap: float | None = None
        user_words = _content_words(user_msg)
        if len(user_words) >= 3:
            resp_words = _content_words(response)
            topical_overlap = len(user_words & resp_words) / len(user_words)
            if topical_overlap == 0:
                score -= 0.50
                flags.append("off_topic_no_overlap")
            elif topical_overlap < 0.34:
                score -= 0.20
                flags.append("low_topical_overlap")

        return {
            "score":      max(0.0, min(1.0, score)),
            "flags":      flags,
            "word_count": words,
            "tier":       tier,
            "topical_overlap": (round(topical_overlap, 3)
                                if topical_overlap is not None else None),
        }

    # ── Persistence ──────────────────────────────────────────────
    async def _persist(self, score_data: dict, session_id: Optional[str],
                       tenant_id: Optional[str], tier: str,
                       user_message: str, response: str) -> None:
        if self._db is None:
            return
        try:
            await self._db.quality_scores.insert_one({
                "session_id":      session_id,
                "tenant_id":       tenant_id,
                "score":           score_data["score"],
                "flags":           score_data["flags"],
                "tier":            tier,
                "word_count":      score_data["word_count"],
                # ts as epoch seconds for fast Mongo numeric range queries.
                "timestamp_ts":    time.time(),
                "user_preview":    (user_message or "")[:120],
                "response_preview": (response or "")[:240],
            })
        except Exception as e:                          # noqa: BLE001
            logger.debug("quality_scores insert failed: %r", e)

    # ── Drift detection ──────────────────────────────────────────
    async def _check_drift(self, tenant_id: Optional[str]) -> None:
        if self._db is None or not tenant_id:
            return
        try:
            cursor = self._db.quality_scores.find(
                {"tenant_id": tenant_id},
                sort=[("timestamp_ts", -1)],
                limit=self.DRIFT_WINDOW,
            )
            recent = await cursor.to_list(self.DRIFT_WINDOW)
        except Exception as e:                          # noqa: BLE001
            logger.debug("quality_monitor drift fetch failed: %r", e)
            return
        if len(recent) < 20:
            return
        last_10 = [r["score"] for r in recent[:10]]
        prev_40 = [r["score"] for r in recent[10:]]
        if not prev_40:
            return
        avg_recent = sum(last_10) / len(last_10)
        avg_prev   = sum(prev_40) / len(prev_40)
        if avg_prev - avg_recent > self.DRIFT_TRIGGER:
            try:
                await self._db.quality_alerts.insert_one({
                    "tenant_id":   tenant_id,
                    "alert_type":  "quality_drift",
                    "avg_recent":  round(avg_recent, 3),
                    "avg_prev":    round(avg_prev,   3),
                    "drop":        round(avg_prev - avg_recent, 3),
                    "timestamp_ts": time.time(),
                    "acknowledged": False,
                })
                logger.warning(
                    "quality_monitor DRIFT alert for tenant=%s: "
                    "%.2f → %.2f (drop=%.2f)",
                    tenant_id, avg_prev, avg_recent, avg_prev - avg_recent,
                )
            except Exception as e:                      # noqa: BLE001
                logger.debug("quality_alerts insert failed: %r", e)


__all__ = ["QualityMonitor"]
