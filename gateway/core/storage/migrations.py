"""Create or migrate every table the gateway owns, once per process, before any repository runs.

Each domain package declares its table in ``tables.py``; this module is the list.
Statements are idempotent (``IF NOT EXISTS`` / ``ADD COLUMN IF NOT EXISTS``), so
applying them on every start is safe and doubles as the migration step.
"""

from __future__ import annotations

from gateway.core.storage.events import tables as events
from gateway.core.storage.postgres import PostgresDatabase


def apply_migrations(database: PostgresDatabase) -> None:
    """Create or migrate every gateway table on ``database``."""
    database.run_schema(events.SCHEMA, events.MIGRATE_COMMITTED)


__all__ = ["apply_migrations"]
