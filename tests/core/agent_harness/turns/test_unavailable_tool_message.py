"""A tool the turn cannot offer must fail recoverably, not cryptically.

``slack_send_message`` gates itself on a configured webhook. Without one it is
dropped from the turn's tool map, and the model calling it got back
``unknown tool: slack_send_message`` — which reads as "no such tool", offers no
alternative, and was printed verbatim into the middle of the user's briefing.

The message has two audiences: the model, which should be able to pick a
working sibling on the next iteration, and the user, who should not be shown a
bare internal identifier.
"""

from __future__ import annotations

from core.llm.types import ToolCall
from core.tool.execution import execute_tools


class _StubTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _run_missing(available: list[str]) -> str:
    """Run a call for a tool the turn does not offer, and return the error text."""
    results = execute_tools(
        [ToolCall(id="t1", name="slack_send_message", input={"message": "hi"})],
        [_StubTool(name) for name in available],
        {},
    )
    payload = results[0]
    if isinstance(payload, dict):
        return str(payload.get("error") or payload.get("content") or payload)
    return str(payload)


def test_an_unavailable_tool_says_so_rather_than_unknown() -> None:
    """ "unknown" implies it does not exist; the truth is it is not available here."""
    # Act
    message = _run_missing(["slack_reply_message"])

    # Assert
    assert "slack_send_message" in message
    assert "not available" in message.lower()


def test_the_model_is_told_which_sibling_it_can_use_instead() -> None:
    """Recovery beats instruction: name the alternative in the error itself."""
    # Act
    message = _run_missing(["slack_reply_message", "telegram_send_message", "shell_run"])

    # Assert
    assert "slack_reply_message" in message
    assert "shell_run" not in message, "unrelated tools add noise, not recovery"
