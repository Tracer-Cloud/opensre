"""
Description:
--------------------------------
We want to have a very specific tests that validates wether the agent is working or not.
The test goes like this:
- We start the gateway and get the agent
- We send a message to the agent: "send a message to slack with the temperature in antartica, compute the temperature first and then send the message"
- We expect the agent to produce three action turns (compute, Slack send, finalize).
  The ReAct same-LLM reviewer is off by default, so there is no fourth invoke.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.tools.action_tools import action_tool_names
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.action_driver import ActionTurnRunner
from core.llm.types import AgentLLMResponse, SchemaDescribedTool, ToolCall
from tools.interactive_shell.subprocess_presenter import (
    headless_subprocess_presenter_factory,
)
from tools.registry import clear_tool_registry_cache

_USER_MESSAGE = (
    "send a message to slack with the temperature in antartica, "
    "compute the temperature first and then send the message"
)
# Genuinely computed by the first (shell) turn.
_COMPUTED_C = -20 + -20
_COMPUTE_COMMAND = f"python3 -c \"print('Antarctica:', {-20} + {-20}, 'C')\""
_SLACK_WEBHOOK = "https://hooks.slack.test/abc"


class _ComputeThenSlackLLM:
    """Scripted LLM that runs a compute step, then sends the result to Slack.

    This mirrors a real model handling the compound request as a short sequence
    of turns:

    * Turn 1 emits ``shell_run`` to compute the Antarctica temperature.
    * Turn 2 (once the compute step has run) emits ``slack_send_message`` with the
      temperature embedded in the message body.
    * Turn 3 concludes with a plain reply and no tool call.
    The ReAct same-LLM reviewer is off by default, so this client is not
    invoked a fourth time.

    """

    def __init__(self) -> None:
        self.turns = 0
        self.sent_slack_message: str | None = None

    def tool_schemas(self, _tools: Sequence[SchemaDescribedTool]) -> list[dict[str, Any]]:
        return []

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = tools
        self.turns += 1
        # Goal review reuses this client with a "User goal:" prompt; answer it
        # as reached so the action loop does not treat the review as a new plan.
        if self._is_goal_review(messages, system):
            return AgentLLMResponse(content='{"verdict": "GOAL_REACHED"}')
        shell_output = self._shell_output(messages)
        if not shell_output:
            return AgentLLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_compute",
                        name="shell_run",
                        input={"command": _COMPUTE_COMMAND},
                    )
                ],
            )
        if self.sent_slack_message is None:
            match = re.search(r"Antarctica:\s*(-?\d+)\s*C", shell_output)
            assert match is not None, shell_output
            message = f"Antarctica temperature: {match.group(1)}C"
            self.sent_slack_message = message
            return AgentLLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_slack",
                        name="slack_send_message",
                        input={"message": message},
                    )
                ],
            )
        return AgentLLMResponse(content="Done — sent the Antarctica temperature to Slack.")

    @staticmethod
    def _is_goal_review(messages: list[dict[str, Any]], system: str | None) -> bool:
        if system and "completed the user's goal" in system:
            return True
        for message in messages:
            content = message.get("content")
            if isinstance(content, str) and content.startswith("User goal:"):
                return True
        return False

    @staticmethod
    def _shell_output(messages: list[dict[str, Any]]) -> str:
        """Return stdout from the ``shell_run`` provider result, if present."""
        for message in messages:
            if message.get("role") != "tool":
                continue
            content = message.get("content")
            try:
                entries = json.loads(content) if isinstance(content, str) else content
            except (TypeError, ValueError):
                continue
            for entry in entries or []:
                if not isinstance(entry, dict) or entry.get("name") != "shell_run":
                    continue
                result = entry.get("result")
                if isinstance(result, str):
                    try:
                        result = json.loads(result)
                    except (TypeError, ValueError):
                        return result.strip()
                if isinstance(result, dict):
                    for key in ("response_text", "stdout"):
                        value = result.get(key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()
        return ""

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.input} for tc in tool_calls
            ],
        }

    @staticmethod
    def build_tool_result_message(
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "content": json.dumps(
                [
                    {"id": tc.id, "name": tc.name, "result": result}
                    for tc, result in zip(tool_calls, results)
                ],
                default=str,
            ),
        }


def test_agent_computes_temperature_then_sends_it_to_slack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gateway agent computes a value then sends it to Slack across turns."""
    clear_tool_registry_cache()
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _SLACK_WEBHOOK)

    delivered: dict[str, str] = {}

    def _capture_send(message: str, *, webhook_url: str = "") -> tuple[bool, str]:
        delivered["message"] = message
        delivered["webhook_url"] = webhook_url
        return True, ""

    monkeypatch.setattr(
        "integrations.slack.tools.slack_send_message_tool.delivery.send_slack_webhook_message",
        _capture_send,
    )

    # Build the gateway agent's action surface exactly as ``start_gateway`` does:
    # shared action tools wrapped in the core-owned default provider.
    session = SessionCore(store=InMemorySessionStore())
    integrations: dict[str, Any] = {"slack": {"webhook_url": _SLACK_WEBHOOK}}
    session.resolved_integrations_cache = integrations
    console = Console(force_terminal=False)
    provider = DefaultToolProvider(session, console)
    action_tools = provider.action_tools(confirm_fn=None, is_tty=True)
    tool_names = action_tool_names(action_tools)
    assert "shell_run" in tool_names
    assert "slack_send_message" in tool_names

    provider = DefaultToolProvider(
        session,
        console,
        precomputed_action_tools=action_tools,
        subprocess_presenter_factory=headless_subprocess_presenter_factory,
    )
    llm = _ComputeThenSlackLLM()

    result = ActionTurnRunner(
        output=MagicMock(),
        tools=provider,
        llm_factory=lambda: llm,
    ).run(
        _USER_MESSAGE,
        session,
        confirm_fn=lambda _prompt: "y",
        is_tty=True,
    )

    # Compute → Slack → finalize. The ReAct reviewer is off, so no fourth call.
    # The behavioral contract is the two tools.
    assert llm.turns == 3
    # Turn 1 actually executed a shell command to compute the temperature.
    shell_entries = [entry for entry in session.history if entry.get("type") == "shell"]
    assert shell_entries, "expected the compute turn to run a shell command"

    # Turn 2 sent the computed temperature to Slack via the real Slack tool.
    assert str(_COMPUTED_C) in delivered.get("message", "")
    assert delivered["webhook_url"] == _SLACK_WEBHOOK

    # Both tool calls (shell_run + slack_send_message) were planned and succeeded.
    assert result.handled is True
    assert result.executed_success_count >= 2
