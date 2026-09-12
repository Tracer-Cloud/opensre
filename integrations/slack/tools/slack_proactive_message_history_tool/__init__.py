"""Registry entrypoint for Slack proactive-message history."""

from __future__ import annotations

from integrations.slack.tools.slack_proactive_message_history_tool.tool import (
    SlackProactiveMessageHistoryTool,
    slack_proactive_message_history,
)

TOOL_MODULES = ("tool",)

__all__ = [
    "TOOL_MODULES",
    "SlackProactiveMessageHistoryTool",
    "slack_proactive_message_history",
]
