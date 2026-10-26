from __future__ import annotations

from typing import Any, ClassVar

from core.domain.types.evidence import EvidenceSource, record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework import tool
from integrations.slack.thread_client import fetch_thread, parse_thread_ref
from integrations.slack.web_client import bot_token_configured


def _map_replay_slack_thread_locally(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Record the replayed thread as citeable evidence when messages were fetched."""
    thread = output.get("thread")
    if not isinstance(thread, dict):
        return
    messages = thread.get("messages")
    if not isinstance(messages, list) or not messages:
        return
    channel = str(thread.get("channel") or "").strip()
    timestamp = str(thread.get("ts") or "").strip()
    ref = f" from {channel}/{timestamp}" if channel and timestamp else ""
    truncated = " (truncated)" if thread.get("truncated") else ""
    record_evidence_entry(
        evidence,
        source="replay_slack_thread_locally",
        label="Slack Thread Replay",
        summary=f"{len(messages)} thread messages{ref}{truncated}",
    )


class ReplaySlackThreadLocallyTool(BaseTool):
    name = "replay_slack_thread_locally"
    source: ClassVar[EvidenceSource] = "slack"
    evidence_mapper = _map_replay_slack_thread_locally
    surfaces = (ToolSurface.CHAT, ToolSurface.ACTION)
    side_effect_level = SideEffectLevel.READ_ONLY
    description = "Fetch a captured Slack thread for local replay and Slack bot behavior testing."
    input_schema = {
        "type": "object",
        "properties": {
            "thread_ref": {"type": "string", "description": "Slack thread in CHANNEL/TS format."}
        },
        "required": ["thread_ref"],
        "additionalProperties": False,
    }
    outputs = {"thread": "Captured Slack thread messages.", "error": "Failure detail."}

    def is_available(self, sources: dict[str, dict[object, object]]) -> bool:
        return bot_token_configured(sources)

    def run(self, thread_ref: str, **_kwargs: Any) -> dict[str, Any]:
        try:
            channel, timestamp = parse_thread_ref(thread_ref)
        except ValueError as exc:
            return {"status": "failed", "error": str(exc), "error_type": "validation_error"}
        thread = fetch_thread(channel, timestamp)
        if "error" in thread:
            error = str(thread["error"])
            error_type = (
                "configuration_or_delivery_error"
                if error.startswith("Slack bot token is not configured.")
                else "delivery_error"
            )
            return {"status": "failed", "error": error, "error_type": error_type}
        return {"status": "ok", "thread": thread}


replay_slack_thread_locally = ReplaySlackThreadLocallyTool()
tool(
    replay_slack_thread_locally,
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
)
