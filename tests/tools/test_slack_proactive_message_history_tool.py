"""The Slack ledger is retrievable through an agent-callable read-only tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import paths
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from infrastructure.proactive_messages import DecisionLedger, ProactiveMessageDecision
from integrations.slack.tools.slack_proactive_message_history_tool import (
    SlackProactiveMessageHistoryTool,
)


def test_tool_returns_scoped_send_suppress_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(paths.CONTEXT_ROOT_ENV, raising=False)
    scope = StorageScope(principal=Principal.org("org_tool"), actor=Actor("U_TOOL"))
    with bound_storage_scope(scope):
        DecisionLedger().record_decision(
            session_id="session",
            interaction_id="interaction",
            end_record_id="end",
            policy_name="master-judgement",
            policy_version=1,
            decision=ProactiveMessageDecision(
                decision="suppress",
                rationale="Already resolved.",
            ),
            signal_fingerprint="",
            channel_id="C12345678",
            thread_ts="100.1",
        )
        tool = SlackProactiveMessageHistoryTool()
        result = tool.run(limit=10)

    assert tool.is_available({}) is True
    assert result["status"] == "read"
    assert result["decision_count"] == 1
    assert result["decisions"][0]["rationale"] == "Already resolved."
