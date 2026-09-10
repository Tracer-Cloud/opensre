"""Read the current actor's durable proactive-message decision ledger."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework import tool
from infrastructure.proactive_messages import DecisionLedger


class SlackProactiveMessageHistoryTool(BaseTool):
    """Retrieve recent send/suppress decisions and Slack delivery identifiers."""

    name = "slack_proactive_message_history"
    source = "slack"
    surfaces = (ToolSurface.CHAT, ToolSurface.ACTION)
    description = (
        "Read this user's recent proactive-message judgement ledger, including "
        "send/suppress rationales and Slack delivery status/message timestamps. "
        "Use when the user asks what was sent proactively, why a follow-up was "
        "suppressed, or whether a proactive Slack message was delivered."
    )
    use_cases = [
        "Explaining why a proactive Slack follow-up was sent or suppressed",
        "Checking the delivery status of a proactive message",
    ]
    anti_examples = [
        "Reading ordinary Slack conversation history (use slack_read_messages)",
        "Sending or retrying a proactive message",
    ]
    side_effect_level = SideEffectLevel.READ_ONLY
    requires_approval = False
    input_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Number of recent decisions to return (default 20).",
            }
        },
        "additionalProperties": False,
    }
    outputs = {
        "status": "'read' when the ledger was queried",
        "decisions": "newest-first send/suppress decisions with delivery identifiers",
        "decision_count": "number of decisions returned",
    }

    def run(self, limit: int = 20, **_kwargs: Any) -> dict[str, Any]:
        decisions = DecisionLedger().recent(limit)
        return {
            "source": self.source,
            "status": "read",
            "decisions": decisions,
            "decision_count": len(decisions),
        }


slack_proactive_message_history = tool(
    SlackProactiveMessageHistoryTool(),
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
)
