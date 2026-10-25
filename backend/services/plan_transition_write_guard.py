"""
services/plan_transition_write_guard.py — Master Prompt v2 PART 6.1.

A hard, structural guarantee that backend/scripts/plan_transition_dry_run.py
(and the admin read endpoint wrapping it) can never write to real
Mongo state, even if a future edit introduced a bug that tried to.
Every collection accessed through `WriteGuardedDB` returns a proxy
that raises `WriteBlockedError` on any write-shaped Motor call;
read-only calls (find, find_one, aggregate, count_documents, ...)
pass straight through untouched.

Not to be confused with services/write_guard.py — that is an
unrelated, pre-existing module (the GitHub-commit deny-list
guardrail). This one is Mongo-write-specific and PART-6-specific.
"""
from __future__ import annotations

_WRITE_METHODS = frozenset({
    "insert_one", "insert_many", "update_one", "update_many",
    "delete_one", "delete_many", "replace_one",
    "find_one_and_update", "find_one_and_replace", "find_one_and_delete",
    "bulk_write", "drop",
})


class WriteBlockedError(RuntimeError):
    """Raised instead of ever reaching Mongo for a write call made
    through WriteGuardedDB on a non-allowlisted collection."""


class _GuardedCollection:
    def __init__(self, real_collection, *, blocked: bool):
        self._real = real_collection
        self._blocked = blocked

    def __getattr__(self, name):
        real_attr = getattr(self._real, name)
        if self._blocked and name in _WRITE_METHODS:
            async def _raise(*_args, **_kwargs):
                raise WriteBlockedError(
                    f"blocked write attempt: {name}() on a write-guarded "
                    f"collection — this tool must never write to real state",
                )
            return _raise
        return real_attr


class WriteGuardedDB:
    """Wraps a real Motor database handle. `allow_write_to` is an
    optional set of collection names that remain writable (used only
    for the fake-apply idempotency bookkeeping collection — never for
    `dev_users`/`wallets`/`wallet_ledger`/anything that represents
    real billing state)."""

    def __init__(self, real_db, *, allow_write_to: frozenset[str] | set[str] = frozenset()):
        self._real = real_db
        self._allow_write_to = frozenset(allow_write_to)

    def __getattr__(self, name):
        real_collection = getattr(self._real, name)
        return _GuardedCollection(real_collection, blocked=name not in self._allow_write_to)
