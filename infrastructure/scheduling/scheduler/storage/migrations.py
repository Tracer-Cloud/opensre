"""SQLite schema creation and migrations for scheduled execution history."""

from __future__ import annotations

import sqlite3
import time

#: How long to keep retrying a column add while a competing process holds the
#: write lock.
_MIGRATION_TIMEOUT_SECONDS = 30.0
_MIGRATION_RETRY_DELAY_SECONDS = 0.1

_TASK_RUNS_SCHEMA = """
    CREATE TABLE task_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        fire_time TEXT NOT NULL,
        attempt INTEGER NOT NULL DEFAULT 1,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        posted_message_id TEXT DEFAULT '',
        error TEXT DEFAULT '',
        provider TEXT DEFAULT '',
        targets TEXT DEFAULT '',
        owner_token TEXT NOT NULL DEFAULT '',
        lease_expires_at TEXT NOT NULL DEFAULT '',
        target_filter TEXT NOT NULL DEFAULT '[]',
        UNIQUE(task_id, fire_time, attempt)
    )
"""

_REQUIRED_COLUMNS = frozenset({"attempt", "targets", "target_filter"})

# Recovery only considers pending work and expired running attempts. Keeping the
# indexes partial prevents completed history from growing either candidate set.
_RECOVERY_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_task_runs_recovery_pending_order "
    "ON task_runs (started_at, task_id, fire_time, attempt) "
    "WHERE status = 'pending'",
    "CREATE INDEX IF NOT EXISTS idx_task_runs_recovery_expired "
    "ON task_runs (lease_expires_at, started_at, task_id, fire_time, attempt) "
    "WHERE status = 'running' AND lease_expires_at != ''",
    "CREATE INDEX IF NOT EXISTS idx_task_runs_live_owner "
    "ON task_runs (task_id, lease_expires_at) "
    "WHERE status = 'running'",
)
_RECOVERY_INDEX_NAMES = frozenset(
    {
        "idx_task_runs_recovery_pending_order",
        "idx_task_runs_recovery_expired",
        "idx_task_runs_live_owner",
    }
)


def _table_columns(conn: sqlite3.Connection, table: str = "task_runs") -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _has_targets_column(conn: sqlite3.Connection) -> bool:
    return "targets" in _table_columns(conn)


def _index_names(conn: sqlite3.Connection, table: str = "task_runs") -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA index_list({table})")}


def _schema_is_current(conn: sqlite3.Connection) -> bool:
    """Whether columns and recovery indexes already meet the current contract."""
    return _table_columns(conn) >= _REQUIRED_COLUMNS and _index_names(conn) >= _RECOVERY_INDEX_NAMES


def _migrate_legacy_claim_table(conn: sqlite3.Connection, legacy_columns: set[str]) -> None:
    """Rebuild the pre-lease table so reclaimed attempts retain their history."""
    conn.execute("ALTER TABLE task_runs RENAME TO task_runs_legacy")
    conn.execute(_TASK_RUNS_SCHEMA)
    targets = "targets" if "targets" in legacy_columns else "''"
    conn.execute(
        "INSERT INTO task_runs "
        "(id, task_id, fire_time, attempt, started_at, finished_at, status, "
        "posted_message_id, error, provider, targets, owner_token, lease_expires_at) "
        f"SELECT id, task_id, fire_time, 1, started_at, finished_at, status, "
        f"posted_message_id, error, provider, {targets}, '', started_at "
        "FROM task_runs_legacy"
    )
    conn.execute("DROP TABLE task_runs_legacy")


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was first created.

    Two processes can both see the column missing before either commits its
    ``ALTER TABLE``, so the loser's own write fails — with "duplicate column"
    if the winner already committed, or with a lock-timeout if the winner is
    still in flight and outlasts ``busy_timeout``. Rechecking the schema
    (rather than matching the error text) covers the first case, and retrying
    covers the second: at timeout the winner's column is not visible yet, so
    a single recheck would wrongly conclude the migration failed. Each retry
    re-reads the schema first, so the loser returns the moment the winner's
    commit lands.

    Adding a column to this table is sub-millisecond work, so a writer still
    holding the lock after ``_MIGRATION_TIMEOUT_SECONDS`` is stuck rather than
    slow. Raising then is deliberate: retrying forever would hide a wedged
    database behind a scheduler that never fires.
    """
    deadline = time.monotonic() + _MIGRATION_TIMEOUT_SECONDS
    while True:
        if _has_targets_column(conn):
            return
        try:
            conn.execute("ALTER TABLE task_runs ADD COLUMN targets TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            if _has_targets_column(conn):
                return
            if time.monotonic() >= deadline:
                raise
            time.sleep(_MIGRATION_RETRY_DELAY_SECONDS)
        else:
            return


def _add_recovery_indexes(conn: sqlite3.Connection) -> None:
    """Create the partial indexes used by recovery and live-owner lookups."""
    for statement in _RECOVERY_INDEX_STATEMENTS:
        conn.execute(statement)


def _begin_migration_transaction(conn: sqlite3.Connection) -> bool:
    """Acquire the migration write lock, tolerating a competing index build.

    Creating an index can take longer than SQLite's regular ``busy_timeout``
    on a database with substantial completed-run history. A second scheduler
    process must wait for that migration to commit, then recheck the schema,
    rather than failing its startup just because its first ``BEGIN IMMEDIATE``
    timed out.

    Returns ``False`` when the competing process completed the migration while
    this process was waiting, so the caller has no transaction to commit.
    """
    deadline = time.monotonic() + _MIGRATION_TIMEOUT_SECONDS
    while True:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            error = str(exc).lower()
            if "locked" not in error and "busy" not in error:
                raise
            if _schema_is_current(conn):
                return False
            if time.monotonic() >= deadline:
                raise
            time.sleep(_MIGRATION_RETRY_DELAY_SECONDS)
        else:
            return True


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Create or migrate the task-runs schema under one SQLite write lock."""
    if _schema_is_current(conn):
        return

    if not _begin_migration_transaction(conn):
        return
    try:
        if _schema_is_current(conn):
            conn.commit()
            return
        columns = _table_columns(conn)
        if not columns:
            conn.execute(_TASK_RUNS_SCHEMA)
        elif "attempt" not in columns:
            _migrate_legacy_claim_table(conn, columns)
        _add_missing_columns(conn)
        if "target_filter" not in _table_columns(conn):
            # Old attempts did not record whether delivery was restricted.
            # An empty scope prevents automatic recovery from widening it.
            conn.execute(
                "ALTER TABLE task_runs ADD COLUMN target_filter TEXT NOT NULL DEFAULT '[]'"
            )
        _add_recovery_indexes(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


__all__ = ["apply_migrations"]
