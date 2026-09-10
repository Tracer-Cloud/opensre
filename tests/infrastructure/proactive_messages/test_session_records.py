"""Manual triggers resolve the newest completed persisted interaction."""

from __future__ import annotations

from config.principal import StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.proactive_messages import latest_completed_interaction_boundary
from tests.infrastructure.proactive_messages.conftest import write_interaction


def test_latest_boundary_starts_after_the_previous_record(scope: StorageScope) -> None:
    trigger = write_interaction(scope, session_id="session-boundary")

    with bound_storage_scope(scope):
        boundary = latest_completed_interaction_boundary(trigger.session_id)

    assert boundary == (trigger.start_record_id, trigger.end_record_id)
