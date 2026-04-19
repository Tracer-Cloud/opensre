"""Tests for GitHub MCP validation (connectivity, auth, repo-access probe)."""

from __future__ import annotations

from typing import Any

import pytest
from rich.console import Console

import app.integrations.github_mcp as github_mcp_module


def _minimal_toolset_for_validation() -> list[dict[str, Any]]:
    """Tool names required by validate_github_mcp_config plus list_repositories."""

    names = (
        "get_file_contents",
        "get_me",
        "get_repository_tree",
        "list_commits",
        "search_code",
        "list_repositories",
    )
    return [
        {
            "name": n,
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }
        for n in names
    ]


def test_validate_github_mcp_config_success_includes_repo_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _minimal_toolset_for_validation()

    def fake_list_tools(_config: Any) -> list[dict[str, Any]]:
        return tools

    def fake_call(_config: Any, name: str, _args: dict[str, Any] | None = None) -> dict[str, Any]:
        if name == "get_me":
            return {"is_error": False, "structured_content": {"login": "alice"}, "text": ""}
        if name == "list_repositories":
            return {
                "is_error": False,
                "structured_content": [
                    {"full_name": "org/one", "private": False, "fork": False},
                    {"full_name": "org/two", "private": True, "fork": True},
                ],
                "text": "",
            }
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr("app.integrations.github_mcp.list_github_mcp_tools", fake_list_tools)
    monkeypatch.setattr("app.integrations.github_mcp.call_github_mcp_tool", fake_call)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
            "toolsets": ["repos"],
        }
    )
    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is True
    assert result.authenticated_user == "alice"
    assert result.repo_access_count == 2
    assert result.repo_access_scope_owners == ("org",)
    assert result.repo_access_samples == ("org/one", "org/two")
    assert result.repo_access_probe_tool == "list_repositories"
    assert result.repo_access_probe_limit_applied >= 5
    assert len(result.repo_access_probe_rows) == 2
    assert result.repo_access_probe_rows[0].full_name == "org/one"
    assert result.repo_access_probe_rows[0].private is False
    assert result.repo_access_probe_rows[1].private is True
    assert result.repo_access_probe_rows[1].fork is True
    assert "OK @alice" in result.detail
    report = github_mcp_module.format_github_mcp_validation_cli_report(result)
    assert "Configuration validation: succeeded" in report
    assert "GitHub identity: @alice" in report
    assert "Repositories returned (probe): 2" in report
    assert "Repository access source:" in report
    assert "org" in report
    assert "org/one" in report


def test_validate_github_mcp_config_fails_when_repo_list_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _minimal_toolset_for_validation()

    def fake_list_tools(_config: Any) -> list[dict[str, Any]]:
        return tools

    def fake_call(_config: Any, name: str, _args: dict[str, Any] | None = None) -> dict[str, Any]:
        if name == "get_me":
            return {"is_error": False, "structured_content": {"login": "bob"}, "text": ""}
        if name == "list_repositories":
            return {"is_error": True, "text": "403 Forbidden", "structured_content": None}
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr("app.integrations.github_mcp.list_github_mcp_tools", fake_list_tools)
    monkeypatch.setattr("app.integrations.github_mcp.call_github_mcp_tool", fake_call)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
        }
    )
    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is False
    assert result.failure_category == "repository_access"
    assert "bob" in result.detail
    assert "403 Forbidden" in result.detail
    assert "repository access check failed" in result.detail
    fail_report = github_mcp_module.format_github_mcp_validation_cli_report(result)
    assert "Configuration validation: failed" in fail_report
    assert "Failure type:" in fail_report


def test_validate_github_mcp_config_fails_when_no_repo_list_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        {
            "name": n,
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }
        for n in (
            "get_file_contents",
            "get_me",
            "get_repository_tree",
            "list_commits",
            "search_code",
        )
    ]

    def fake_list_tools(_config: Any) -> list[dict[str, Any]]:
        return tools

    def fake_call(_config: Any, name: str, _args: dict[str, Any] | None = None) -> dict[str, Any]:
        if name == "get_me":
            return {"is_error": False, "structured_content": {"login": "carol"}, "text": ""}
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr("app.integrations.github_mcp.list_github_mcp_tools", fake_list_tools)
    monkeypatch.setattr("app.integrations.github_mcp.call_github_mcp_tool", fake_call)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
        }
    )
    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is False
    assert result.failure_category == "repository_access"
    assert "carol" in result.detail
    assert "no repository listing or search tool was usable" in result.detail


def test_validate_github_mcp_config_reports_actual_attempts_for_starred_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        {
            "name": n,
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }
        for n in (
            "get_file_contents",
            "get_me",
            "get_repository_tree",
            "list_commits",
            "search_code",
        )
    ]
    tools.extend(
        [
            {
                "name": "list_starred_repositories",
                "description": "",
                "input_schema": {
                    "type": "object",
                    "properties": {"page": {"type": "integer"}},
                    "required": ["page"],
                },
            },
            {
                "name": "search_repositories",
                "description": "",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        ]
    )

    def fake_list_tools(_config: Any) -> list[dict[str, Any]]:
        return tools

    def fake_call(_config: Any, name: str, _args: dict[str, Any] | None = None) -> dict[str, Any]:
        if name == "get_me":
            return {"is_error": False, "structured_content": {"login": "carol"}, "text": ""}
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr("app.integrations.github_mcp.list_github_mcp_tools", fake_list_tools)
    monkeypatch.setattr("app.integrations.github_mcp.call_github_mcp_tool", fake_call)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
        }
    )
    result = github_mcp_module.validate_github_mcp_config(cfg, repo_view="starred")

    assert result.ok is False
    assert result.failure_category == "repository_access"
    assert "tried: list_starred_repositories" in result.detail
    assert "list_repositories" not in result.detail
    assert "list_user_repositories" not in result.detail
    assert "search_repositories" not in result.detail


def test_validate_github_mcp_config_uses_search_repositories_when_no_list_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hosted MCP exposes search_repositories (query required) but not list_repositories."""

    tools = [
        {
            "name": n,
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }
        for n in (
            "get_file_contents",
            "get_me",
            "get_repository_tree",
            "list_commits",
            "search_code",
        )
    ]
    tools.append(
        {
            "name": "search_repositories",
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }
    )

    def fake_list_tools(_config: Any) -> list[dict[str, Any]]:
        return tools

    def fake_call(
        _config: Any,
        name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if name == "get_me":
            return {"is_error": False, "structured_content": {"login": "dana"}, "text": ""}
        if name == "search_repositories":
            assert args == {"query": "user:dana"}
            return {
                "is_error": False,
                "structured_content": {"items": [{"full_name": "dana/a"}]},
                "text": "",
            }
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr("app.integrations.github_mcp.list_github_mcp_tools", fake_list_tools)
    monkeypatch.setattr("app.integrations.github_mcp.call_github_mcp_tool", fake_call)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
        }
    )
    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is True
    assert result.repo_access_samples == ("dana/a",)
    assert result.repo_access_probe_tool == "search_repositories"


def test_validate_github_mcp_config_succeeds_from_get_me_profile_without_list_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        {
            "name": n,
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }
        for n in (
            "get_file_contents",
            "get_me",
            "get_repository_tree",
            "list_commits",
            "search_code",
        )
    ]

    def fake_list_tools(_config: Any) -> list[dict[str, Any]]:
        return tools

    def fake_call(_config: Any, name: str, _args: dict[str, Any] | None = None) -> dict[str, Any]:
        if name == "get_me":
            return {
                "is_error": False,
                "structured_content": {
                    "login": "erin",
                    "details": {"public_repos": 2, "total_private_repos": 3},
                },
                "text": "",
            }
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr("app.integrations.github_mcp.list_github_mcp_tools", fake_list_tools)
    monkeypatch.setattr("app.integrations.github_mcp.call_github_mcp_tool", fake_call)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
        }
    )
    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is True
    assert result.repo_access_count == 5
    assert result.repo_access_samples == ()
    assert "get_me profile" in result.detail


def test_validate_github_mcp_config_fails_when_get_me_tool_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = [
        {
            "name": n,
            "description": "",
            "input_schema": {"type": "object", "properties": {}},
        }
        for n in (
            "get_file_contents",
            "get_repository_tree",
            "list_commits",
            "search_code",
            "list_repositories",
        )
    ]

    def fake_list_tools(_config: Any) -> list[dict[str, Any]]:
        return tools

    monkeypatch.setattr("app.integrations.github_mcp.list_github_mcp_tools", fake_list_tools)
    monkeypatch.setattr(
        "app.integrations.github_mcp.call_github_mcp_tool",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("get_me should not run")),
    )

    cfg = github_mcp_module.build_github_mcp_config(
        {"url": "https://api.githubcopilot.com/mcp/", "mode": "streamable-http"}
    )
    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is False
    assert result.failure_category == "insufficient_tools"
    assert "required identity tool 'get_me'" in result.detail


def test_validate_github_mcp_config_handles_truthy_non_dict_get_me_structured_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _minimal_toolset_for_validation()

    def fake_list_tools(_config: Any) -> list[dict[str, Any]]:
        return tools

    def fake_call(_config: Any, name: str, _args: dict[str, Any] | None = None) -> dict[str, Any]:
        if name == "get_me":
            return {
                "is_error": False,
                "structured_content": [{"login": "alice"}],
                "text": '{"login": "alice"}',
            }
        if name == "list_repositories":
            return {
                "is_error": False,
                "structured_content": [{"full_name": "org/one", "private": False, "fork": False}],
                "text": "",
            }
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr("app.integrations.github_mcp.list_github_mcp_tools", fake_list_tools)
    monkeypatch.setattr("app.integrations.github_mcp.call_github_mcp_tool", fake_call)

    cfg = github_mcp_module.build_github_mcp_config(
        {"url": "https://api.githubcopilot.com/mcp/", "mode": "streamable-http"}
    )
    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is True
    assert result.authenticated_user == "alice"
    assert result.repo_access_samples == ("org/one",)


def test_repo_probe_capture_limit_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_GITHUB_MCP_REPO_PROBE_LIMIT", raising=False)
    assert (
        github_mcp_module._repo_probe_capture_limit() == github_mcp_module._DEFAULT_REPO_PROBE_LIMIT
    )
    monkeypatch.setenv("OPENSRE_GITHUB_MCP_REPO_PROBE_LIMIT", "120")
    assert github_mcp_module._repo_probe_capture_limit() == 120
    monkeypatch.setenv("OPENSRE_GITHUB_MCP_REPO_PROBE_LIMIT", "9999")
    assert github_mcp_module._repo_probe_capture_limit() == 500


def test_connectivity_failure_detail_unwraps_taskgroup_exception_group() -> None:
    """TaskGroup often wraps the real error; users should see the inner exception."""
    inner = ConnectionError("Connection refused")
    group = ExceptionGroup("unhandled errors in a TaskGroup (1 sub-exception)", [inner])
    msg = github_mcp_module._connectivity_failure_detail(group)  # noqa: SLF001
    assert "ConnectionError" in msg
    assert "Connection refused" in msg
    assert "Check: outbound HTTPS" in msg


def test_format_github_mcp_validation_cli_report_auth_failure() -> None:
    r = github_mcp_module.GitHubMCPValidationResult(
        ok=False,
        detail="token rejected",
        failure_category="authentication",
    )
    text = github_mcp_module.format_github_mcp_validation_cli_report(r)
    assert "Configuration validation: failed" in text
    assert "authentication" in text.lower()
    assert "token rejected" in text


def test_print_github_mcp_validation_report_success_and_failure() -> None:
    ok_console = Console(record=True, width=88, highlight=False)
    ok_result = github_mcp_module.GitHubMCPValidationResult(
        ok=True,
        detail="OK @alice; repos=2; owners=org; examples=org/a,org/b; mcp_tools=9",
        authenticated_user="alice",
        repo_access_count=2,
        repo_access_scope_owners=("org",),
        repo_access_samples=("org/a", "org/b"),
        repo_access_probe_tool="list_starred_repositories",
        repo_access_probe_rows=(
            github_mcp_module.GitHubMCPRepoProbeRow("org/a", False, False),
            github_mcp_module.GitHubMCPRepoProbeRow("org/b", True, False),
        ),
        repo_access_probe_limit_applied=50,
    )
    github_mcp_module.print_github_mcp_validation_report(
        ok_result, console=ok_console, detail_level="full"
    )
    ok_text = ok_console.export_text()
    assert "Configuration validation: succeeded" in ok_text
    assert "alice" in ok_text
    assert "Starred repositories" in ok_text
    assert "public" in ok_text
    assert "private" in ok_text

    fail_console = Console(record=True, width=88, highlight=False)
    fail_result = github_mcp_module.GitHubMCPValidationResult(
        ok=False,
        detail="connection reset",
        failure_category="connectivity",
    )
    github_mcp_module.print_github_mcp_validation_report(
        fail_result, console=fail_console, detail_level="standard"
    )
    fail_text = fail_console.export_text()
    assert "validation failed" in fail_text.lower()
    assert "connection reset" in fail_text
