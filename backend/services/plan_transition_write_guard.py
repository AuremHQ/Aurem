"""
services/plan_transition_write_guard.py — Master Prompt v2 PART 6.1.

A hard, structural guarantee that backend/scripts/plan_transition_dry_run.py
(and the admin read endpoint wrapping it) can never write to real
Mongo state, even if a future edit introduced a bug that tried to.

2026-10-25 (ITEM 3, independent review) — converted from a DENY-LIST
of 12 named write methods to an ALLOW-LIST. The deny-list left real
gaps open: `aggregate` with a `$out`/`$merge` stage, `update`, `save`,
`create_index`, `rename`, `find_and_modify`, `create_collection`, and
any unknown/future Motor method all fell through `__getattr__`'s
pass-through fallback untouched. Worse, `._real` (a plain instance
attribute, never routed through `__getattr__` at all), `.database`,
`.client`, `.get_collection(...)`, and `db["name"]` were live escape
hatches straight to the raw, unguarded connection.

Now: only `find`/`find_one`/`count_documents`/`distinct`/
`estimated_document_count`/`aggregate` pass through — `aggregate`'s
pipeline is additionally scanned RECURSIVELY (any nesting depth,
including `$facet`'s named sub-pipelines and `$lookup`'s `pipeline`
key) for `$out`/`$merge`/`$function`/`$accumulator`/`$where`.
Everything else — including an attribute name this module has never
seen before — raises `WriteBlockedError`. The known escape names are
blocked explicitly rather than left to fall through to default
Python attribute resolution.

Not to be confused with services/write_guard.py — that is an
unrelated, pre-existing module (the GitHub-commit deny-list
guardrail). This one is Mongo-write-specific and PART-6-specific.
"""
from __future__ import annotations

_ALLOWED_READ_METHODS = frozenset({
    "find", "find_one", "count_documents", "distinct",
    "estimated_document_count", "aggregate",
})

# Known ways to reach the raw, unguarded connection/collection/database
# that must never be reachable through a WriteGuardedDB, regardless of
# whether Python's normal attribute resolution would otherwise find
# them (e.g. `_real` is a real instance attribute — `__getattr__`
# alone would never even be consulted for it).
_BLOCKED_ESCAPE_NAMES = frozenset({"_real", "database", "client", "command", "get_collection"})

_FORBIDDEN_AGG_KEYS = frozenset({"$out", "$merge", "$function", "$accumulator", "$where"})


class WriteBlockedError(RuntimeError):
    """Raised instead of ever reaching Mongo for a write-shaped call,
    an unrecognised call, or an escape-hatch attribute access made
    through WriteGuardedDB."""


def _scan_for_forbidden_agg_keys(obj) -> str | None:
    """Recursively walk an aggregation pipeline (or any nested
    sub-structure within it — `$facet`'s dict of named sub-pipelines,
    `$lookup`'s `pipeline` list, operator arguments) for a forbidden
    stage/operator key, at any depth. Returns the first forbidden key
    found, or None."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _FORBIDDEN_AGG_KEYS:
                return k
            found = _scan_for_forbidden_agg_keys(v)
            if found:
                return found
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            found = _scan_for_forbidden_agg_keys(item)
            if found:
                return found
    return None


class _GuardedCollection:
    def __init__(self, real_collection, *, write_allowed: bool):
        object.__setattr__(self, "_store", real_collection)
        object.__setattr__(self, "_write_allowed", write_allowed)

    def __getitem__(self, key):
        raise WriteBlockedError(
            "blocked access: subscripting a write-guarded collection "
            "is not permitted",
        )

    def __getattr__(self, name):
        # __getattr__ only fires when normal attribute lookup (which
        # would find _store/_write_allowed as real instance
        # attributes) fails — so any OTHER name, including the
        # escape-hatch ones, always lands here.
        if name in _BLOCKED_ESCAPE_NAMES:
            raise WriteBlockedError(
                f"blocked access to {name!r} — not permitted through a "
                f"write-guarded collection",
            )
        real = object.__getattribute__(self, "_store")
        write_allowed = object.__getattribute__(self, "_write_allowed")

        if name == "aggregate":
            def _guarded_aggregate(pipeline, *args, **kwargs):
                forbidden = _scan_for_forbidden_agg_keys(pipeline)
                if forbidden:
                    raise WriteBlockedError(
                        f"blocked aggregate stage/operator {forbidden!r} — "
                        f"not permitted through a write-guarded collection",
                    )
                return real.aggregate(pipeline, *args, **kwargs)
            return _guarded_aggregate

        if name in _ALLOWED_READ_METHODS:
            return getattr(real, name)

        if write_allowed:
            return getattr(real, name)

        def _raise(*_args, **_kwargs):
            raise WriteBlockedError(
                f"blocked call: {name}() is not on the read allow-list "
                f"for a write-guarded collection",
            )
        return _raise


class WriteGuardedDB:
    """Wraps a real Motor database handle. `allow_write_to` is an
    optional set of collection names that remain fully writable (used
    only for the fake-apply idempotency bookkeeping collection — never
    for `dev_users`/`wallets`/`wallet_ledger`/anything that represents
    real billing state)."""

    def __init__(self, real_db, *, allow_write_to: frozenset[str] | set[str] = frozenset()):
        object.__setattr__(self, "_store", real_db)
        object.__setattr__(self, "_allow_write_to", frozenset(allow_write_to))

    def __getitem__(self, key):
        raise WriteBlockedError(
            "blocked access: subscripting a WriteGuardedDB is not permitted",
        )

    def __getattr__(self, name):
        if name in _BLOCKED_ESCAPE_NAMES:
            raise WriteBlockedError(
                f"blocked access to {name!r} — not permitted through a "
                f"WriteGuardedDB",
            )
        real = object.__getattribute__(self, "_store")
        allow_write_to = object.__getattribute__(self, "_allow_write_to")
        real_collection = getattr(real, name)
        return _GuardedCollection(real_collection, write_allowed=name in allow_write_to)
