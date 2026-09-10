"""CLI operators can inspect, test, and manually trigger proactive policies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from config.constants import paths
from infrastructure.proactive_messages import ProactiveMessageDecision
from integrations.slack import SlackBotTarget
from surfaces.cli.commands import proactive as proactive_module
from surfaces.cli.commands.proactive import proactive_command


class _StructuredLLM:
    def with_structured_output(self, _model: object) -> _StructuredLLM:
        return self

    def invoke(self, _prompt: str) -> ProactiveMessageDecision:
        return ProactiveMessageDecision(
            decision="send",
            rationale="A merged flaky test remains actionable.",
            message=(
                "CI follow-up: flaky test test_retry failed. <@U1>, isolate its shared "
                "fixture before the next merge."
            ),
            signal_key="ci:test_retry:flaky",
            new_verified_information=True,
            clear_owner_and_action=True,
            material_timing=True,
            verified_information="flaky test test_retry failed",
            evidence_quote="flaky test test_retry failed",
            evidence_source="session",
            owner="U1",
            next_action="Isolate the shared fixture.",
            material_timing_reason="Before the next merge.",
        )


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)


def test_policies_lists_the_starter_rules() -> None:
    result = CliRunner().invoke(proactive_command, ["policies", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["rules"] == ["ci_blocker", "threshold_event", "integration_health"]


def test_send_test_explains_missing_slack_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proactive_module, "resolve_bot_token", lambda: (None, "missing"))

    result = CliRunner().invoke(proactive_command, ["send-test", "--channel-id", "C1"])

    assert result.exit_code == 1
    assert "integrations setup slack" in result.output


def test_trigger_evaluates_latest_interaction_and_records_slack_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "manual-session"
    session = tmp_path / "sessions" / f"{session_id}.jsonl"
    session.parent.mkdir(parents=True)
    records = [
        {"type": "session", "version": 2, "id": session_id},
        {"type": "message", "id": "before", "role": "assistant", "content": "Earlier"},
        {
            "type": "message",
            "id": "user",
            "role": "user",
            "content": "Review this merged CI run.",
        },
        {
            "type": "message",
            "id": "assistant",
            "role": "assistant",
            "content": "The merged run shows flaky test test_retry failed.",
        },
        {"type": "leaf", "id": "end", "parent_id": "assistant"},
    ]
    session.write_text("\n".join(json.dumps(row) for row in records) + "\n")
    sent: list[dict[str, Any]] = []

    monkeypatch.setattr(
        proactive_module,
        "resolve_bot_token",
        lambda: (SlackBotTarget(bot_token="xoxb-test"), ""),
    )
    monkeypatch.setattr(proactive_module, "resolve_channel_id", lambda *_a: ("C1", ""))
    monkeypatch.setattr(proactive_module, "fetch_channel_messages", lambda *_a, **_k: ([], ""))

    def _post(*_args: Any, **kwargs: Any) -> tuple[str, str]:
        sent.append(kwargs)
        return "200.2", ""

    monkeypatch.setattr(proactive_module, "post_channel_message_with_id", _post)
    monkeypatch.setattr(
        "infrastructure.proactive_messages.judgement.get_llm",
        lambda _role: _StructuredLLM(),
    )

    result = CliRunner().invoke(
        proactive_command,
        [
            "trigger",
            "--session-id",
            session_id,
            "--channel-id",
            "C1",
            "--thread-ts",
            "100.1",
            "--user-id",
            "U1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "delivered"
    assert payload["slack_message_ts"] == "200.2"
    assert sent[0]["channel_id"] == "C1"
    assert sent[0]["thread_ts"] == "100.1"
    assert Path(payload["decision_ledger"]).is_file()
