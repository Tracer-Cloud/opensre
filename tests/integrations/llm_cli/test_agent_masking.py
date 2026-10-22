"""Masking across the CLI agent's JSON and plain-text response boundaries."""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

import pytest

from config.constants.masking import (
    OPENSRE_MASK_ENABLED_ENV,
    OPENSRE_MASK_EXTRA_REGEX_ENV,
    OPENSRE_MASK_KINDS_ENV,
)
from core.llm.transports.sdk.agent_clients import CLIBackedAgentClient
from integrations.llm_cli.base import CLIInvocation, CLIProbe


def _recording_adapter(captured: list[str]) -> MagicMock:
    def build(*, prompt: str, **_kwargs: object) -> CLIInvocation:
        captured.append(prompt)
        return CLIInvocation(
            argv=("mock-cli",), stdin=prompt, cwd="/tmp", env=None, timeout_sec=30.0
        )

    adapter = MagicMock()
    adapter.name = "mock-cli"
    adapter.detect.return_value = CLIProbe(
        installed=True, bin_path="/usr/bin/mock-cli", version=None, logged_in=True, detail="ok"
    )
    adapter.build.side_effect = build
    adapter.parse.side_effect = lambda *, stdout, **_kwargs: stdout
    return adapter


@pytest.mark.parametrize("envelope", ["input", "arguments", "final"])
def test_agent_restores_masked_values_after_parsing(
    monkeypatch: pytest.MonkeyPatch, envelope: str
) -> None:
    value = 'ab"cd\\path'
    placeholder = "<QUOTED_VAL_0>"
    monkeypatch.setenv(OPENSRE_MASK_ENABLED_ENV, "true")
    monkeypatch.setenv(OPENSRE_MASK_KINDS_ENV, "ip_address")
    monkeypatch.setenv(
        OPENSRE_MASK_EXTRA_REGEX_ENV, json.dumps({"QUOTED_VAL": re.escape(value), "ENV": "prod"})
    )
    arguments = {"values": [placeholder], "inputs": {placeholder: True}}
    if envelope == "final":
        stdout = f"Found {placeholder}"
    else:
        payload = arguments if envelope == "input" else json.dumps(arguments)
        stdout = json.dumps(
            {"tool_calls": [{"id": "c1", "name": "lookup_<ENV_0>", envelope: payload}]}
        )

    captured: list[str] = []

    adapter = _recording_adapter(captured)
    with (
        patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as guardrails,
        patch("integrations.llm_cli.runner.subprocess.run") as run,
    ):
        guardrails.return_value.is_active = False
        run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
        client = CLIBackedAgentClient(adapter)
        messages = [{"role": "user", "content": f"Look up {value}"}]
        result = client.invoke(
            messages,
            tools=[
                {"name": "lookup_prod", "input_schema": {"type": "object", "examples": [value]}}
            ],
        )
        if envelope != "final":
            assert len(result.tool_calls) == 1
            messages.append(client.build_assistant_message(result.content, result.tool_calls))
            messages.append(
                client.build_tool_result_message(result.tool_calls, [{"result": {value: value}}])
            )
            run.return_value = MagicMock(returncode=0, stdout=f"Found {placeholder}", stderr="")
            final = client.invoke(messages)
            assert final.content == f"Found {value}"

    assert len(captured) == (1 if envelope == "final" else 2)
    for prompt in captured:
        assert value not in prompt
        assert json.dumps(value)[1:-1] not in prompt

    assert value not in captured[0]
    assert placeholder in captured[0]
    if envelope == "final":
        assert result.tool_calls == []
        assert result.content == f"Found {value}"
    else:
        assert result.content == ""
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "lookup_prod"
        assert result.tool_calls[0].input == {"values": [value], "inputs": {value: True}}


@pytest.mark.parametrize("replacement", ["[REDACTED]", '"quoted\nredaction'])
def test_structured_history_applies_guardrails_before_reversible_masking(replacement: str) -> None:
    from infrastructure.safety.masking.policy import MaskingPolicy
    from infrastructure.safety.masking.rules import MaskingRules
    from integrations.llm_cli.text import flatten_messages_to_prompt

    original = "192.168.1.50"
    masking = MaskingRules(MaskingPolicy(enabled=True, kinds=("ip_address",)))

    def redact(text: str) -> str:
        return text.replace(original, replacement)

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as guardrails:
        guardrails.return_value.is_active = True
        guardrails.return_value.apply.side_effect = redact
        prompt = flatten_messages_to_prompt(
            [{"role": "user", "content": [{"type": "json", "data": {"host": original}}]}],
            masking=masking,
        )

    assert original not in prompt
    assert "[REDACTED]" in prompt
    assert masking.placeholder_map == {}


def test_agent_masks_contextual_identifiers_on_later_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_MASK_ENABLED_ENV, "true")
    monkeypatch.setenv(OPENSRE_MASK_KINDS_ENV, "namespace")
    monkeypatch.delenv(OPENSRE_MASK_EXTRA_REGEX_ENV, raising=False)
    captured: list[str] = []
    adapter = _recording_adapter(captured)
    stdout = json.dumps(
        {"tool_calls": [{"id": "c1", "name": "lookup", "input": {"namespace": "<NAMESPACE_0>"}}]}
    )
    with (
        patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as guardrails,
        patch("integrations.llm_cli.runner.subprocess.run") as run,
    ):
        guardrails.return_value.is_active = False
        run.return_value = MagicMock(returncode=0, stdout=stdout, stderr="")
        client = CLIBackedAgentClient(adapter)
        messages = [{"role": "user", "content": "Look up namespace: prod"}]
        result = client.invoke(messages)
        assert result.tool_calls[0].input == {"namespace": "prod"}
        messages.append(client.build_assistant_message(result.content, result.tool_calls))
        messages.append(
            client.build_tool_result_message(result.tool_calls, [{"namespace": "prod"}])
        )
        run.return_value = MagicMock(returncode=0, stdout="Found <NAMESPACE_0>", stderr="")
        final = client.invoke(messages)

    assert final.content == "Found prod"
    assert len(captured) == 2
    assert all("prod" not in prompt for prompt in captured)
