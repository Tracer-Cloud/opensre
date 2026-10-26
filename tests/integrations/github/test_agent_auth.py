"""Agent GitHub authentication cannot be promoted by local or model metadata."""

from typing import Any
from unittest.mock import Mock

import pytest

from core.llm.types import ToolCall
from core.tool.execution import execute_tool_calls
from infrastructure.harness_providers import bound_github_connection
from integrations.catalog import classify_integrations, merge_integrations_by_service
from integrations.github import app_connection, filter_github_connected_services
from integrations.github.rest_token import github_rest_token
from integrations.github.tools.github_cli.tool import github_cli
from integrations.store import load_integrations, replace_integrations


def _record(connection: str, token: str, *, origin: str = "webapp") -> dict[str, Any]:
    return {
        "id": connection,
        "service": "github",
        "status": "active",
        "origin": "webapp",
        "instances": [
            {
                "name": "default",
                "tags": {"connection_origin": origin},
                "credentials": {
                    "auth_token": token,
                    "url": "https://api.githubcopilot.com/mcp/",
                    "mode": "streamable-http",
                },
            }
        ],
    }


def test_local_copied_app_metadata_is_ineligible_and_cannot_shadow_remote(monkeypatch, tmp_path):
    monkeypatch.setattr("integrations.store.STORE_PATH", tmp_path / "integrations.json")
    replace_integrations([_record("copied", "local-token")])
    local = load_integrations()
    assert not github_rest_token(classify_integrations(local))
    assert filter_github_connected_services(["github", "slack"], local) == ["slack"]
    app = _record("app", "app-token")
    merged = merge_integrations_by_service([app], local)
    assert github_rest_token(classify_integrations(merged)) == "app-token"
    assert filter_github_connected_services(["github", "slack"], merged) == ["github", "slack"]


@pytest.mark.parametrize("origin", ["cli", "unknown"])
def test_server_requires_reconnection_for_unattested_credentials(origin):
    assert not github_rest_token(
        classify_integrations([_record("old", "old-token", origin=origin)])
    )


def test_model_cannot_override_injected_token_or_provenance(monkeypatch):
    seen = []

    def run_gh(**kwargs):
        seen.append(kwargs["github_token"])
        return {"ok": True, "stdout": "", "stderr": "", "exit_code": 0}

    monkeypatch.setattr("integrations.github.tools.github_cli.tool.run_gh", run_gh)
    tool = github_cli.__opensre_registered_tool__
    call = ToolCall(
        id="1",
        name=tool.name,
        input={
            "args": ["api", "user"],
            "github_token": "model-token",
            "github_connection_origin": "webapp",
        },
    )
    execute_tool_calls(
        [call], [tool], {"github": {"auth_token": "app-token", "connection_origin": "webapp"}}
    )
    assert seen == ["app-token"]
    execute_tool_calls(
        [call], [tool], {"github": {"auth_token": "local-token", "connection_origin": "unknown"}}
    )
    assert seen == ["app-token"]


def test_empty_injection_never_falls_back_to_environment(monkeypatch):
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_MCP_AUTH_TOKEN"):
        monkeypatch.setenv(name, "local-token")
    assert github_rest_token(explicit="", connection_origin="webapp") == ""
    run = Mock(side_effect=AssertionError("GitHub I/O"))
    monkeypatch.setattr("integrations.github.tools.github_cli.tool.run_gh", run)
    result = github_cli(args=["api", "user"], github_token="", github_connection_origin="webapp")
    assert result["work_outcome"]["status"] == "blocked"
    assert "setup_command" not in result
    run.assert_not_called()


def test_background_connection_is_refreshed_and_never_replaced_by_another_grant(monkeypatch):
    rows = [_record("chosen", "fresh-token")]
    monkeypatch.setattr("integrations.webapp_vault.webapp_vault_configured", lambda: False)
    monkeypatch.setattr(app_connection, "load_account_integrations", lambda **_kwargs: rows)
    assert app_connection.refreshed_github_token("chosen") == "fresh-token"
    rows[:] = [_record("other", "other-token")]
    assert app_connection.refreshed_github_token("chosen") == ""


def test_direct_report_schedule_preserves_request_scoped_selection(monkeypatch):
    rows = [_record("chosen", "chosen-token"), _record("default", "default-token")]
    rows[1]["instances"][0]["tags"]["is_default"] = "true"
    monkeypatch.setattr("integrations.webapp_vault.webapp_vault_configured", lambda: False)
    monkeypatch.setattr(app_connection, "load_account_integrations", lambda **_kwargs: rows)
    with bound_github_connection("chosen"):
        inputs = app_connection.github_schedule_inputs(
            "reporting-github-ci-failures", {"owner": "acme", "repo": "api"}
        )
    assert inputs["github_connection_id"] == "chosen"
    assert app_connection.refreshed_github_token(inputs["github_connection_id"]) == "chosen-token"
    rows.pop(0)
    with bound_github_connection("chosen"):
        unavailable = app_connection.github_schedule_inputs(
            "reporting-github-ci-failures", {"owner": "acme", "repo": "api"}
        )
    assert unavailable["github_connection_id"] == "chosen"
    assert app_connection.refreshed_github_token(unavailable["github_connection_id"]) == ""


def test_background_sweep_binds_refreshed_connection_and_blocks_without_it(monkeypatch):
    from core.agent_harness import SessionCore
    from integrations.github import pr_sweep_runner

    calls = []
    session = SessionCore()

    def run_turn(_prompt, **kwargs):
        kwargs["prepare_session"](session)
        calls.append(session.integrations.github_connection_id)
        return type("Result", (), {"primary_response_text": "sweep", "answered": True})()

    monkeypatch.setattr(pr_sweep_runner, "refreshed_github_token", lambda _id: "fresh")
    monkeypatch.setattr(pr_sweep_runner.AgentSession, "run_headless_turn", run_turn)
    monkeypatch.setattr(
        pr_sweep_runner.ScheduledOutcomes, "report", lambda *_args, **_kwargs: "sweep"
    )
    assert pr_sweep_runner.run_github_pr_sweep({"github_connection_id": "chosen"}) == "sweep"
    assert calls == ["chosen"]
    monkeypatch.setattr(pr_sweep_runner, "refreshed_github_token", lambda _id: "")
    report = pr_sweep_runner.run_github_pr_sweep({"github_connection_id": "chosen"})
    assert report.outcome.status == "blocked"
    assert calls == ["chosen"]
