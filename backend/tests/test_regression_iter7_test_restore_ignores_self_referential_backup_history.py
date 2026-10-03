"""
test_regression_iter7_test_restore_ignores_self_referential_backup_history.py

Real bug found diagnosing quality-gate.yml's "Fitness-function
invariants" job on branch conflict_280926_2109 (2026-09-29):
`tests/live/test_iter_restore_drill_live_verify.py::test_test_restore_regression`
failed with:

    AssertionError: restored_counts missing collections: ['backup_history']

Root cause (confirmed live against a real backend + real Mongo):
`POST /admin/backups/test-restore` (routers/backups_admin.py) picks the
single most-recent row from `db.backup_history` and restores that R2
archive, then diffs its `collection_counts` against a LIVE, right-now
snapshot of every collection (`db_restore.source_collection_counts()`).

`services/db_backup.py::run_backup()` dumps every OTHER collection
FIRST (step 1, `_dump_db_to_gzip_file`), and only writes ITS OWN row
into `backup_history` AFTER the dump is already on disk (step 3,
`_write_history`). So a given backup's own archive can never contain
the history row for its own completion — and on a brand-new database
whose very first backup ever is the one being restored, `backup_history`
does not exist as a collection in that archive at all yet (confirmed
live: a fresh `aurem_ci_smoke` DB's first backup predates
`backup_history`'s own creation).

This is structural and unavoidable for THIS ONE collection — every
other collection round-trips exactly (confirmed live: fixing the test
to exempt only `backup_history` left the "no missing collections"
check just as strict for every real, user-facing collection; the
underlying restore mechanism is correct). Fix: the test now excludes
`backup_history` specifically from its `missing` check.

This unit test locks in the CAUSE (not just patches the live test):
`run_backup()`'s dump step reads `list_collection_names()` BEFORE
`_write_history()` runs, so the dump's own completion is provably
absent from what it captured.
"""
import inspect

from services import db_backup


def test_regression_iter7_run_backup_dumps_before_writing_its_own_history_row():
    """Lock in the ordering that makes `backup_history` structurally
    self-referential: on the SUCCESS path, the dump call must appear
    BEFORE the `_write_history(..., status="success", ...)` call in
    `run_backup`'s source. If this ordering ever flips (write-history
    first, dump second), `backup_history` would stop being exempt and
    test_iter_restore_drill_live_verify.py's exemption would need to
    be re-examined."""
    src = inspect.getsource(db_backup.run_backup)
    dump_pos = src.index("_dump_db_to_gzip_file(")
    success_write_pos = src.index('status="success"')
    assert success_write_pos > dump_pos, (
        "run_backup() now writes its success history row BEFORE dumping — "
        "backup_history would no longer be structurally exempt from "
        "test_iter_restore_drill_live_verify.py's missing-collection check"
    )
