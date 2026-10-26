from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from integrations.config_models import RailwayIntegrationConfig
from integrations.railway.tools.railway_deployment_tool.inspect_tool import (
    inspect_railway_deployment,
)
from integrations.slack.tools.slack_thread_replay_tool.tool import replay_slack_thread_locally
from integrations.slack.web_client import SlackBotTarget


def _completed(
    *, stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["railway"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_runtime_tools_are_discoverable_on_declared_surfaces() -> None:
    from tools.registry import clear_tool_registry_cache, get_registered_tool_map

    clear_tool_registry_cache()
    chat = get_registered_tool_map("chat")
    action = get_registered_tool_map("action")

    assert "inspect_railway_deployment" in chat
    assert "replay_slack_thread_locally" in chat
    assert "redeploy_railway_service" not in action
    assert chat["inspect_railway_deployment"].requires_approval is False


def test_inspect_uses_configured_scope_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.railway.client.shutil.which", lambda command: command)
    monkeypatch.setattr(
        "integrations.railway.client.subprocess.run",
        lambda *_args, **_kwargs: _completed(
            stdout=json.dumps(
                [{"id": "deployment-id", "status": "SUCCESS", "createdAt": "2026-07-21"}]
            )
        ),
    )

    result = inspect_railway_deployment.run(
        railway_config=RailwayIntegrationConfig(
            project="project-id", service="service-id", environment="production"
        )
    )

    assert result["status"] == "ok"
    assert result["deployment"]["project"] == "project-id"


def test_inspect_searches_more_than_twenty_deployments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.railway.client.shutil.which", lambda command: command)
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return _completed(stdout=json.dumps([{"id": "deployment-id", "status": "SUCCESS"}]))

    monkeypatch.setattr("integrations.railway.client.subprocess.run", fake_run)

    result = inspect_railway_deployment.run(
        project="project-id", service="service-id", environment="production"
    )

    assert result["status"] == "ok"
    assert commands[0][commands[0].index("--limit") + 1] == "100"


def test_replay_uses_shared_bot_token_and_replies_client(monkeypatch: pytest.MonkeyPatch) -> None:
    target = SlackBotTarget(bot_token="xoxb-test")
    calls: list[tuple[str, str, str, dict[str, object]]] = []
    monkeypatch.setattr("integrations.slack.thread_client.resolve_bot_token", lambda: (target, ""))

    def fake_request(
        method: str, path: str, token: str, *, params: dict[str, object]
    ) -> tuple[dict[str, object], str]:
        calls.append((method, path, token, params))
        return {"ok": True, "messages": [{"user": "U1", "text": "hello", "ts": "1"}]}, ""

    monkeypatch.setattr("integrations.slack.thread_client._request_json", fake_request)

    result = replay_slack_thread_locally.run(thread_ref="C1/1")

    assert result["status"] == "ok"
    assert calls == [
        ("GET", "conversations.replies", "xoxb-test", {"channel": "C1", "ts": "1", "limit": 50})
    ]


def test_replay_is_available_for_a_resolved_slack_source() -> None:
    assert replay_slack_thread_locally.is_available(
        {"slack": {"config": {"bot_token": "xoxb-test"}}}
    )


def test_replay_accepts_null_messages_and_reactions(monkeypatch: pytest.MonkeyPatch) -> None:
    target = SlackBotTarget(bot_token="xoxb-test")
    monkeypatch.setattr("integrations.slack.thread_client.resolve_bot_token", lambda: (target, ""))
    monkeypatch.setattr(
        "integrations.slack.thread_client._request_json",
        lambda *_args, **_kwargs: (
            {
                "ok": True,
                "messages": [
                    {"user": "U1", "text": "hello", "ts": "1", "reactions": None},
                    None,
                ],
            },
            "",
        ),
    )

    result = replay_slack_thread_locally.run(thread_ref="C1/1")

    assert result["status"] == "ok"
    assert result["thread"]["messages"][0]["reactions"] == []


def test_replay_is_not_truncated_when_slack_repeats_a_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A Slack response that keeps advertising the same next_cursor is an API
    # loop, not "more pages" — the dedup guard stops paging and truncated must
    # stay False rather than reflecting the leftover cursor.
    target = SlackBotTarget(bot_token="xoxb-test")
    monkeypatch.setattr("integrations.slack.thread_client.resolve_bot_token", lambda: (target, ""))
    monkeypatch.setattr(
        "integrations.slack.thread_client._request_json",
        lambda *_args, **_kwargs: (
            {
                "ok": True,
                "messages": [{"user": "U1", "text": "hello", "ts": "1"}],
                "response_metadata": {"next_cursor": "SAME"},
            },
            "",
        ),
    )

    result = replay_slack_thread_locally.run(thread_ref="C1/1")

    assert result["status"] == "ok"
    assert result["thread"]["truncated"] is False


def test_replay_classifies_missing_shared_token_as_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.slack.thread_client.resolve_bot_token",
        lambda: (
            None,
            "Slack bot token is not configured. Set SLACK_BOT_TOKEN or configure the Slack integration.",
        ),
    )

    result = replay_slack_thread_locally.run(thread_ref="C1/1")

    assert result["status"] == "failed"
    assert result["error_type"] == "configuration_or_delivery_error"
