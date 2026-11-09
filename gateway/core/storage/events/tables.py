"""The ``slack_handled_events`` table: name, DDL and its column migration. Applied by ``migrations``."""

from __future__ import annotations

TABLE = "slack_handled_events"

SCHEMA = """
CREATE TABLE IF NOT EXISTS slack_handled_events (
    event_id TEXT PRIMARY KEY,
    handled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    committed BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS slack_handled_events_handled_at_idx
    ON slack_handled_events (handled_at);
"""

# Older deploys created the table without ``committed``. Default TRUE so
# pre-existing rows stay refused (they already ran or were treated as handled).
MIGRATE_COMMITTED = """
ALTER TABLE slack_handled_events
    ADD COLUMN IF NOT EXISTS committed BOOLEAN NOT NULL DEFAULT TRUE;
"""

__all__ = ["MIGRATE_COMMITTED", "SCHEMA", "TABLE"]
