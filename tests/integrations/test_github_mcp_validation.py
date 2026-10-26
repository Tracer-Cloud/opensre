"""Tests for GitHub MCP validation (connectivity, auth, repo-access probe)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from mcp import types as mcp_types
from rich.console import Console

import integrations.github.mcp as github_mcp_module


def _patch_fake_session(
    monkeypatch: pytest.MonkeyPatch,
    list_fn: Any,
    call_fn: Any = None,
    opened: list[int] | None = None,
) -> None:
    """Replace _open_github_mcp_session with a fake driven by the given callbacks.

    list_fn: A function (config) -> list[dict] returning tool definitions.
    call_fn: A function (config, name, args) -> dict returning simulated tool responses.
    opened: Optional single-slot list incremented once per session opened.
    """

    @asynccontextmanager
    async def _fake_open(config: Any):  # type: ignore[return]
        if opened is not None:
            opened[0] += 1
        session = MagicMock()
        session.initialize = AsyncMock()

        tools_dicts = list_fn(config)
        list_result = MagicMock()
        list_result.tools = [
            mcp_types.Tool(
                name=str(t["name"]),
                input_schema=t.get("input_schema") or {},
            )
            for t in tools_dicts
        ]
        session.list_tools = AsyncMock(return_value=list_result)

        async def _call_tool(name: str, args: dict) -> Any:
            if call_fn is None:
                raise AssertionError(f"Unexpected call_tool({name!r}) - no call_fn provided")
            payload: dict[str, Any] = call_fn(config, name, args or {})
            return mcp_types.CallToolResult(
                is_error=payload.get("is_error", False),
                content=[mcp_types.TextContent(type="text", text=payload.get("text", ""))],
                structured_content=payload.get("structured_content"),
            )

        session.call_tool = _call_tool
        yield session

    monkeypatch.setattr("integrations.github.mcp._open_github_mcp_session", _fake_open)


def test_run_async_closes_coroutine_when_runner_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _noop() -> None:
        return None

    def _fail_before_awaiting(_coro: Any) -> None:
        raise RuntimeError("asyncio.run cannot be called from a running event loop")

    monkeypatch.setattr(github_mcp_module.asyncio, "run", _fail_before_awaiting)

    coro = _noop()
    with pytest.raises(RuntimeError):
        github_mcp_module._run_async(coro)

    assert coro.cr_frame is None


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

    opened: list[int] = [0]
    _patch_fake_session(monkeypatch, fake_list_tools, fake_call, opened)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
            "toolsets": ["repos"],
        }
    )
    result = github_mcp_module.validate_github_mcp_config(cfg)

    # list_tools, get_me, and the repo probe all share one session (#3565).
    assert opened == [1]
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


def test_is_github_copilot_host_detects_hosted_endpoint() -> None:
    assert github_mcp_module._is_github_copilot_host("https://api.githubcopilot.com/mcp/")
    assert github_mcp_module._is_github_copilot_host("https://api.githubcopilot.com:443/mcp")
    assert not github_mcp_module._is_github_copilot_host("https://mcp.internal.example.com/mcp")
    assert not github_mcp_module._is_github_copilot_host("")


def test_validate_github_mcp_config_credential_less_hosted_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale store entry with empty credentials must short-circuit to
    not_configured without hitting the network or emitting a 401 failure."""

    def _must_not_connect(_config: Any) -> list[dict[str, Any]]:
        raise AssertionError("network must not be probed for credential-less config")

    _patch_fake_session(monkeypatch, _must_not_connect)

    cfg = github_mcp_module.build_github_mcp_config({})
    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is False
    assert result.failure_category == "not_configured"
    assert "without an auth token" in result.detail


def test_validate_github_mcp_config_custom_url_without_token_still_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Self-hosted MCP servers may not require a token, so a custom URL without
    a token must NOT be short-circuited — it should still attempt the probe."""

    probed: dict[str, bool] = {"called": False}

    def _fake_list_tools(_config: Any) -> list[dict[str, Any]]:
        probed["called"] = True
        return []

    _patch_fake_session(monkeypatch, _fake_list_tools)

    cfg = github_mcp_module.build_github_mcp_config(
        {"url": "https://mcp.internal.example.com/mcp", "mode": "streamable-http"}
    )
    github_mcp_module.validate_github_mcp_config(cfg)

    assert probed["called"] is True


def test_verify_github_reports_credential_less_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from integrations.github.verifier import verify_github as _verify_github

    def _must_not_connect(_config: Any) -> list[dict[str, Any]]:
        raise AssertionError("network must not be probed for credential-less config")

    _patch_fake_session(monkeypatch, _must_not_connect)

    verdict = _verify_github("local store", {})

    assert verdict["service"] == "github"
    assert verdict["status"] == "missing"
    assert "without an auth token" in verdict["detail"]


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

    _patch_fake_session(monkeypatch, fake_list_tools, fake_call)

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

    _patch_fake_session(monkeypatch, fake_list_tools, fake_call)

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

    _patch_fake_session(monkeypatch, fake_list_tools, fake_call)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
        }
    )
    result = github_mcp_module.validate_github_mcp_config(cfg, repo_view="starred")

    assert result.ok is False
    assert result.failure_category == "repository_access"
    assert "tried: list_starred_repositories" in result.detail
    assert "list_repositories" not in result.detail
    assert "list_user_repositories" not in result.detail
    assert "search_repositories" not in result.detail


def test_auto_probe_prefers_user_search_over_starred() -> None:
    """`auto` must not surface starred repos — prefer the user's own repos via search."""
    tools = [
        {
            "name": "list_starred_repositories",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "search_repositories",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    ]

    plan = github_mcp_module._plan_repo_access_probe(tools, "octocat", view="auto")
    assert plan is not None
    name, args = plan
    assert name == "search_repositories"
    assert args == {"query": "user:octocat"}

    attempts = github_mcp_module._repo_probe_attempts(tools, "octocat", view="auto")
    assert "list_starred_repositories" not in attempts
    assert attempts[-1] == "search_repositories"


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

    _patch_fake_session(monkeypatch, fake_list_tools, fake_call)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
        }
    )
    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is True
    assert result.repo_access_samples == ("dana/a",)
    assert result.repo_access_probe_tool == "search_repositories"


def _hosted_tools_with_search() -> list[dict[str, Any]]:
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
    return tools


def test_validate_github_mcp_config_falls_back_to_get_me_when_user_search_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Org-centric users often get 422 on ``user:<login>`` even with valid org access."""

    tools = _hosted_tools_with_search()
    search_422 = (
        "422 Validation Failed "
        "[{Resource:Search Field:q Code:invalid Message:The listed users and repositories "
        "cannot be searched either because the resources do not exist or you do not have "
        "permission to view them.}]"
    )

    def fake_list_tools(_config: Any) -> list[dict[str, Any]]:
        return tools

    def fake_call(
        _config: Any,
        name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if name == "get_me":
            return {
                "is_error": False,
                "structured_content": {
                    "login": "larsspinetta12",
                    "details": {"public_repos": 0, "total_private_repos": 0},
                },
                "text": "",
            }
        if name == "search_repositories":
            assert args == {"query": "user:larsspinetta12"}
            return {"is_error": True, "text": search_422, "structured_content": None}
        raise AssertionError(f"unexpected tool {name}")

    _patch_fake_session(monkeypatch, fake_list_tools, fake_call)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
        }
    )
    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is True
    assert result.authenticated_user == "larsspinetta12"
    assert result.repo_access_count == 0
    assert "get_me profile" in result.detail
    assert "search_repositories" in result.detail


def test_validate_github_mcp_config_tries_org_search_after_user_search_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _hosted_tools_with_search()
    search_422 = "422 Validation Failed"
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def fake_list_tools(_config: Any) -> list[dict[str, Any]]:
        return tools

    def fake_call(
        _config: Any,
        name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        calls.append((name, args))
        if name == "get_me":
            return {"is_error": False, "structured_content": {"login": "dev1"}, "text": ""}
        if name == "search_repositories":
            query = str((args or {}).get("query") or "")
            if query == "user:dev1":
                return {"is_error": True, "text": search_422, "structured_content": None}
            if query == "org:Tracer-Cloud":
                return {
                    "is_error": False,
                    "structured_content": {
                        "items": [{"full_name": "Tracer-Cloud/opensre", "private": True}]
                    },
                    "text": "",
                }
            raise AssertionError(f"unexpected search query {query!r}")
        raise AssertionError(f"unexpected tool {name}")

    _patch_fake_session(monkeypatch, fake_list_tools, fake_call)
    monkeypatch.setenv("OPENSRE_GITHUB_MCP_VERIFY_ORGS", "Tracer-Cloud")

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
        }
    )
    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is True
    assert result.repo_access_samples == ("Tracer-Cloud/opensre",)
    assert result.repo_access_probe_tool == "search_repositories"
    assert calls.count(("search_repositories", {"query": "user:dev1"})) == 1
    assert calls.count(("search_repositories", {"query": "org:Tracer-Cloud"})) == 1


def test_validate_github_mcp_config_auth_only_when_search_fails_without_profile_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _hosted_tools_with_search()

    def fake_list_tools(_config: Any) -> list[dict[str, Any]]:
        return tools

    def fake_call(
        _config: Any,
        name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if name == "get_me":
            return {"is_error": False, "structured_content": {"login": "dev2"}, "text": ""}
        if name == "search_repositories":
            return {"is_error": True, "text": "422 Validation Failed", "structured_content": None}
        raise AssertionError(f"unexpected tool {name}")

    _patch_fake_session(monkeypatch, fake_list_tools, fake_call)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
        }
    )
    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is True
    assert result.authenticated_user == "dev2"
    assert result.repo_access_count is None
    assert "authenticated; repo probes inconclusive" in result.detail


def test_is_recoverable_repo_probe_error_only_matches_search_validation_failures() -> None:
    assert github_mcp_module._is_recoverable_repo_probe_error(
        "search_repositories",
        {"query": "user:octocat"},
        "422 Validation Failed",
    )
    assert github_mcp_module._is_recoverable_repo_probe_error(
        "search_repositories",
        {"query": "org:Tracer-Cloud"},
        "The listed users and repositories cannot be searched",
    )
    assert not github_mcp_module._is_recoverable_repo_probe_error(
        "search_repositories",
        {"query": "user:octocat"},
        "403 Forbidden",
    )
    assert not github_mcp_module._is_recoverable_repo_probe_error(
        "list_repositories",
        {},
        "403 Forbidden",
    )


def test_validate_github_mcp_config_fails_when_user_search_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _hosted_tools_with_search()

    def fake_list_tools(_config: Any) -> list[dict[str, Any]]:
        return tools

    def fake_call(
        _config: Any,
        name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if name == "get_me":
            return {"is_error": False, "structured_content": {"login": "dev3"}, "text": ""}
        if name == "search_repositories":
            assert args == {"query": "user:dev3"}
            return {"is_error": True, "text": "403 Forbidden", "structured_content": None}
        raise AssertionError(f"unexpected tool {name}")

    _patch_fake_session(monkeypatch, fake_list_tools, fake_call)

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
    assert "403 Forbidden" in result.detail
    assert "repository access check failed" in result.detail


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

    _patch_fake_session(monkeypatch, fake_list_tools, fake_call)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
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

    _patch_fake_session(monkeypatch, fake_list_tools)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
        }
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

    _patch_fake_session(monkeypatch, fake_list_tools, fake_call)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
        }
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
    msg = github_mcp_module._connectivity_failure_detail(group)
    assert "ConnectionError" in msg
    assert "Connection refused" in msg
    assert "Check: outbound HTTPS" in msg


def test_validate_github_mcp_config_reports_scope_challenge_as_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://api.githubcopilot.com/mcp/x/all/readonly")
    response = httpx.Response(
        HTTPStatus.FORBIDDEN,
        request=request,
        headers={
            "WWW-Authenticate": ('Bearer error="insufficient_scope", scope="repo security_events"')
        },
    )
    failure = httpx.HTTPStatusError("scope rejected", request=request, response=response)

    @asynccontextmanager
    async def _failing_open(_config: Any):  # type: ignore[return]
        raise ExceptionGroup("MCP transport", [failure])
        yield  # pragma: no cover

    monkeypatch.setattr("integrations.github.mcp._open_github_mcp_session", _failing_open)
    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "gho_test",
        }
    )

    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is False
    assert result.failure_category == "authentication"
    assert "repo, security_events" in result.detail
    assert "account login" in result.detail


def test_validate_github_mcp_config_reports_session_open_failure_as_connectivity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session is now opened outside the probe steps; a failure there stays connectivity."""

    @asynccontextmanager
    async def _failing_open(_config: Any):  # type: ignore[return]
        raise ConnectionRefusedError("MCP server unreachable")
        yield  # pragma: no cover - never reached, keeps this a generator

    monkeypatch.setattr("integrations.github.mcp._open_github_mcp_session", _failing_open)

    cfg = github_mcp_module.build_github_mcp_config(
        {
            "url": "https://api.githubcopilot.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
            "toolsets": ["repos"],
        }
    )
    result = github_mcp_module.validate_github_mcp_config(cfg)

    assert result.ok is False
    assert result.failure_category == "connectivity"
    assert result.tool_names == ()


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


def test_github_mcp_is_usably_configured_requires_token_for_hosted_copilot() -> None:
    config = github_mcp_module.build_github_mcp_config(
        {
            "mode": "streamable-http",
            "url": github_mcp_module.DEFAULT_GITHUB_MCP_URL,
            "auth_token": "",
        }
    )
    assert github_mcp_module.github_mcp_is_usably_configured(config) is False


def test_github_mcp_is_usably_configured_accepts_hosted_copilot_with_token() -> None:
    config = github_mcp_module.build_github_mcp_config(
        {
            "mode": "streamable-http",
            "url": github_mcp_module.DEFAULT_GITHUB_MCP_URL,
            "auth_token": "gho_test",
        }
    )
    assert github_mcp_module.github_mcp_is_usably_configured(config) is True


def test_build_github_mcp_config_strips_persisted_username_metadata() -> None:
    config = github_mcp_module.build_github_mcp_config(
        {
            "mode": "streamable-http",
            "url": github_mcp_module.DEFAULT_GITHUB_MCP_URL,
            "auth_token": "gho_test",
            "username": "octocat",
        }
    )
    assert config.auth_token == "gho_test"
    assert "username" not in config.model_fields_set


@pytest.mark.asyncio
async def test_open_session_accepts_legacy_two_stream_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older streamable-HTTP clients yield ``(read, write)`` only."""
    read, write = object(), object()
    opened: list[object] = []

    @asynccontextmanager
    async def _two_stream_client(*_args: Any, **_kwargs: Any):
        yield (read, write)

    class _HttpClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        async def __aenter__(self) -> _HttpClient:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    class _Session:
        def __init__(self, read_stream: Any, write_stream: Any) -> None:
            assert read_stream is read
            assert write_stream is write
            opened.append(self)
            self.initialize = AsyncMock()

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(github_mcp_module, "streamable_http_client", _two_stream_client)
    monkeypatch.setattr(github_mcp_module.httpx, "AsyncClient", _HttpClient)
    monkeypatch.setattr("mcp.client.session.ClientSession", _Session)

    config = github_mcp_module.GitHubMCPConfig(
        url="https://mcp.example.test/mcp",
        mode="streamable-http",
    )
    async with github_mcp_module._open_github_mcp_session(config) as session:
        assert session is opened[0]
        session.initialize.assert_awaited_once()
