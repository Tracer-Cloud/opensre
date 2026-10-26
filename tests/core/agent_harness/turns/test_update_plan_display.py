"""An update_plan result must not print through the generic formatter.

The plan renders as the pinned overlay above the prompt. Its tool result carries
a ``summary`` (``Plan · n/m ○ …``) for the model's durable record, so the
end-of-turn generic formatter must stay silent — otherwise the checklist would
also print as text in the transcript, restating what the overlay already shows.
"""

from __future__ import annotations

import json

from core.agent_harness.turns.display_text import format_generic_tool_payload
from core.llm.types import ToolCall


def _plan_result() -> dict[str, object]:
    return {
        "ok": True,
        "current": 2,
        "total": 3,
        "summary": "Plan · 2/3\n  ✓ Capture samples\n  ● Trace to deploy\n  ○ Confirm 2xx  (verify)",
    }


def test_update_plan_emits_nothing_from_the_generic_formatter() -> None:
    # Arrange
    result = _plan_result()

    class _Result:
        content = json.dumps(result)
        details = result
        is_error = False

    # Act
    shown = format_generic_tool_payload(
        ToolCall(id="t1", name="update_plan", input={"plan": []}), _Result()
    )

    # Assert: the overlay owns the plan; the formatter stays silent.
    assert shown == "", f"expected silence, got {len(shown)} chars"
