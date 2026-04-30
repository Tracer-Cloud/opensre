from __future__ import annotations

from app.output import _humanise_message


def test_humanise_message_uses_registered_tool_display_names() -> None:
    message = "Planned actions: ['query_datadog_logs', 'get_sre_guidance']"

    assert _humanise_message(message) == "Datadog logs, SRE runbook"


def test_humanise_message_falls_back_for_unknown_tool_names() -> None:
    message = "Planned actions: ['my_custom_tool']"

    assert _humanise_message(message) == "my custom tool"
