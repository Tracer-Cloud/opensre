"""Manual triggers resolve the newest completed persisted interaction."""

from __future__ import annotations

import json

from config.principal import StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness.session.persistence.paths import session_path
from infrastructure.proactive_messages import latest_completed_interaction_boundary
from tests.infrastructure.proactive_messages.conftest import write_interaction


def test_latest_boundary_starts_after_the_previous_record(scope: StorageScope) -> None:
    trigger = write_interaction(scope, session_id="session-boundary")

    with bound_storage_scope(scope):
        boundary = latest_completed_interaction_boundary(trigger.session_id)

    assert boundary == (trigger.start_record_id, trigger.end_record_id)


def test_latest_boundary_excludes_a_newer_unanswered_user(scope: StorageScope) -> None:
    trigger = write_interaction(scope, session_id="session-unanswered")
    with bound_storage_scope(scope):
        path = session_path(trigger.session_id)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "type": "message",
                        "id": "user-unanswered",
                        "parent_id": trigger.end_record_id,
                        "role": "user",
                        "content": "This newer turn has no answer yet.",
                    }
                )
                + "\n"
            )
            handle.write(
                json.dumps(
                    {
                        "type": "leaf",
                        "id": "end-unanswered",
                        "parent_id": "user-unanswered",
                    }
                )
                + "\n"
            )
        boundary = latest_completed_interaction_boundary(trigger.session_id)

    assert boundary == (trigger.start_record_id, trigger.end_record_id)
