"""Tests for coworker-style authenticated GitHub CLI tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.tool.contracts import RegisteredTool
from integrations.github.tools.github_cli.credentials import resolve_github_token
from integrations.github.tools.github_cli.runner import build_gh_argv, denied_gh_command, run_gh
from integrations.github.tools.github_cli.summary import summarize_gh_result
from integrations.github.tools.github_cli.tool import (
    _github_cli_available,
    _github_cli_extract_params,
    github_cli,
)
from tests.tools.conftest import BaseToolContract
from tools.registry import clear_tool_registry_cache, get_registered_tools


def _registered(tool: Any) -> RegisteredTool:
    return tool.__opensre_registered_tool__


class TestGithubCliContract(BaseToolContract):
    def get_tool_under_test(self) -> RegisteredTool:
        return _registered(github_cli)


def test_build_gh_argv_includes_repo_flag() -> None:
    assert build_gh_argv(args=["issue", "list"], repo="acme/widgets") == [
        "gh",
        "-R",
        "acme/widgets",
        "issue",
        "list",
    ]


def test_build_gh_argv_skips_repo_flag_for_api() -> None:
    """``gh api`` rejects ``-R``; repo belongs in the API path."""
    assert build_gh_argv(
        args=["api", "repos/acme/widgets/pulls/1/comments"],
        repo="acme/widgets",
    ) == ["gh", "api", "repos/acme/widgets/pulls/1/comments"]


def test_build_gh_argv_skips_repo_flag_for_api_after_global_flags() -> None:
    assert build_gh_argv(
        args=["--hostname", "github.com", "api", "user"],
        repo="acme/widgets",
    ) == ["gh", "--hostname", "github.com", "api", "user"]


def test_run_gh_blocks_auth_token_before_spawn() -> None:
    with (
        patch("integrations.github.tools.github_cli.runner.resolve_github_token") as resolve_mock,
        patch("integrations.github.tools.github_cli.runner.subprocess.run") as run_mock,
    ):
        result = run_gh(args=["auth", "token"])
    assert result["ok"] is False
    assert result["error_type"] == "policy_error"
    assert "auth" in result["error"]
    resolve_mock.assert_not_called()
    run_mock.assert_not_called()


def test_run_gh_blocks_extension_install_after_global_flags() -> None:
    with patch("integrations.github.tools.github_cli.runner.subprocess.run") as run_mock:
        result = run_gh(args=["--hostname", "github.com", "extension", "install", "evil/x"])
    assert result["ok"] is False
    assert result["error_type"] == "policy_error"
    assert "extension" in result["error"]
    run_mock.assert_not_called()


def test_help_flag_does_not_mask_blocked_command() -> None:
    """``-h`` is ``--help``, not a value flag; must not skip the next token."""
    assert denied_gh_command(["-h", "auth", "token"]) == "auth"
    assert denied_gh_command(["-h", "extension", "install", "evil/x"]) == "extension"
    with patch("integrations.github.tools.github_cli.runner.subprocess.run") as run_mock:
        result = run_gh(args=["-h", "auth", "token"])
    assert result["ok"] is False
    assert result["error_type"] == "policy_error"
    assert "auth" in result["error"]
    run_mock.assert_not_called()


def test_run_gh_allows_run_and_workflow_reads() -> None:
    assert denied_gh_command(["run", "list"]) is None
    assert denied_gh_command(["run", "view", "123"]) is None
    assert denied_gh_command(["workflow", "list"]) is None
    assert denied_gh_command(["workflow", "view", "ci.yml"]) is None


def test_a_flag_between_the_command_and_a_denied_subcommand_does_not_bypass_the_policy() -> None:
    # Arrange: gh accepts global flags after the command word.
    cases = (
        ["run", "-R", "org/repo", "rerun", "123"],
        ["run", "--repo=org/repo", "cancel", "123"],
        ["workflow", "--hostname", "github.com", "run", "deploy.yml"],
        ["-R", "org/repo", "run", "--jq", ".id", "delete", "123"],
    )

    # Act / Assert: every spelling is refused before any process is spawned.
    for args in cases:
        with patch("integrations.github.tools.github_cli.runner.subprocess.run") as run_mock:
            result = run_gh(args=args)
        assert result["ok"] is False, args
        assert result["error_type"] == "policy_error", args
        run_mock.assert_not_called()
    # A flag value that looks like a subcommand is not one.
    assert denied_gh_command(["run", "list", "--workflow", "rerun"]) is None


def test_run_gh_blocks_ci_and_secret_mutation_commands() -> None:
    cases = (
        (["workflow", "run", "deploy.yml"], "workflow"),
        (["workflow", "enable", "ci.yml"], "workflow"),
        (["run", "rerun", "123"], "run"),
        (["run", "cancel", "123"], "run"),
        (["run", "delete", "123"], "run"),
        (["run", "watch", "123"], "run"),
        (["run", "download", "123"], "run"),
        (["secret", "set", "TOKEN"], "secret"),
    )
    for args, blocked in cases:
        with patch("integrations.github.tools.github_cli.runner.subprocess.run") as run_mock:
            result = run_gh(args=list(args))
        assert result["ok"] is False, args
        assert result["error_type"] == "policy_error", args
        assert blocked in result["error"], args
        run_mock.assert_not_called()


def test_run_gh_redacts_token_echo_in_stdout() -> None:
    completed = MagicMock(returncode=0, stdout="token=secret-token\n", stderr="")
    with (
        patch(
            "integrations.github.tools.github_cli.runner.resolve_github_token",
            return_value="secret-token",
        ),
        patch(
            "integrations.github.tools.github_cli.runner.shutil.which", return_value="/usr/bin/gh"
        ),
        patch("integrations.github.tools.github_cli.runner.subprocess.run", return_value=completed),
    ):
        result = run_gh(args=["issue", "view", "1"])
    assert result["ok"] is True
    assert "secret-token" not in result["stdout"]
    assert "***" in result["stdout"]


def test_run_gh_missing_token() -> None:
    with patch("integrations.github.tools.github_cli.runner.resolve_github_token", return_value=""):
        result = run_gh(args=["issue", "list"])
    assert result["ok"] is False
    assert result["error_type"] == "configuration_error"


def test_run_gh_missing_binary() -> None:
    with (
        patch(
            "integrations.github.tools.github_cli.runner.resolve_github_token", return_value="tok"
        ),
        patch("integrations.github.tools.github_cli.runner.shutil.which", return_value=None),
    ):
        result = run_gh(args=["issue", "list"])
    assert result["ok"] is False
    assert result["error_type"] == "missing_binary"


def test_run_gh_injects_token_env() -> None:
    completed = MagicMock(returncode=0, stdout="https://github.com/o/r/issues/1\n", stderr="")
    with (
        patch(
            "integrations.github.tools.github_cli.runner.resolve_github_token",
            return_value="secret-token",
        ),
        patch(
            "integrations.github.tools.github_cli.runner.shutil.which", return_value="/usr/bin/gh"
        ),
        patch(
            "integrations.github.tools.github_cli.runner.subprocess.run", return_value=completed
        ) as run_mock,
    ):
        result = run_gh(args=["issue", "create", "--title", "t"], repo="o/r")

    assert result["ok"] is True
    assert "secret-token" not in str(result)
    env = run_mock.call_args.kwargs["env"]
    assert env["GH_TOKEN"] == "secret-token"
    assert env["GITHUB_TOKEN"] == "secret-token"
    assert run_mock.call_args.args[0] == [
        "gh",
        "-R",
        "o/r",
        "issue",
        "create",
        "--title",
        "t",
    ]


def test_resolve_github_token_prefers_explicit_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_MCP_AUTH_TOKEN", "mcp-token")
    monkeypatch.setenv("GITHUB_TOKEN", "env-github-token")
    monkeypatch.setenv("GH_TOKEN", "env-gh-token")

    assert resolve_github_token("store-token") == "store-token"


def test_resolve_github_token_env_fallback_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_MCP_AUTH_TOKEN", "mcp-token")
    monkeypatch.setenv("GITHUB_TOKEN", "env-github-token")
    monkeypatch.setenv("GH_TOKEN", "env-gh-token")
    assert resolve_github_token(None) == "mcp-token"

    monkeypatch.delenv("GITHUB_MCP_AUTH_TOKEN")
    assert resolve_github_token(None) == "env-github-token"

    monkeypatch.delenv("GITHUB_TOKEN")
    assert resolve_github_token(None) == "env-gh-token"

    monkeypatch.delenv("GH_TOKEN")
    assert resolve_github_token(None) == ""


def test_extract_params_maps_store_token_and_repo() -> None:
    params = _github_cli_extract_params(
        {
            "github": {
                "connection_verified": True,
                "auth_token": "store-token",
                "owner": "Tracer-Cloud",
                "repo": "opensre",
            }
        }
    )

    assert params["github_token"] == "store-token"
    assert params["repo"] == "Tracer-Cloud/opensre"


def test_github_cli_hides_token_from_public_schema() -> None:
    tool = _registered(github_cli)
    assert "github_token" in tool.injected_params
    assert "github_token" not in tool.public_input_schema.get("properties", {})


def test_run_gh_prefers_store_token_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured source present: the env token must not reach gh."""
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setenv("GH_TOKEN", "env-token")
    completed = MagicMock(returncode=0, stdout="", stderr="")
    with (
        patch(
            "integrations.github.tools.github_cli.runner.shutil.which", return_value="/usr/bin/gh"
        ),
        patch(
            "integrations.github.tools.github_cli.runner.subprocess.run", return_value=completed
        ) as run_mock,
    ):
        result = run_gh(args=["issue", "list"], github_token="store-token")

    assert result["ok"] is True
    env = run_mock.call_args.kwargs["env"]
    assert env["GH_TOKEN"] == "store-token"
    assert env["GITHUB_TOKEN"] == "store-token"


def test_run_gh_falls_back_to_env_without_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """No configured source: env-only local setups keep working."""
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_MCP_AUTH_TOKEN", raising=False)
    completed = MagicMock(returncode=0, stdout="", stderr="")
    with (
        patch(
            "integrations.github.tools.github_cli.runner.shutil.which", return_value="/usr/bin/gh"
        ),
        patch(
            "integrations.github.tools.github_cli.runner.subprocess.run", return_value=completed
        ) as run_mock,
    ):
        result = run_gh(args=["issue", "list"], github_token=None)

    assert result["ok"] is True
    assert run_mock.call_args.kwargs["env"]["GH_TOKEN"] == "env-token"


def test_github_cli_available_with_env_token_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.delenv("GITHUB_MCP_AUTH_TOKEN", raising=False)

    assert _github_cli_available({}) is True


def test_github_cli_available_with_mcp_env_token_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_MCP_AUTH_TOKEN", "mcp-token")

    assert _github_cli_available({}) is True


def test_github_cli_available_with_verified_source_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_MCP_AUTH_TOKEN", raising=False)

    assert _github_cli_available({"github": {"connection_verified": True}}) is True
    assert _github_cli_available({}) is False


def test_github_cli_runs_mutate_without_approval() -> None:
    tool = _registered(github_cli)
    assert tool.requires_approval is False
    assert tool.surfaces == ("action",)
    assert "investigation" not in tool.surfaces
    assert "chat" not in tool.surfaces

    with patch(
        "integrations.github.tools.github_cli.tool.run_gh",
        return_value={
            "ok": True,
            "argv": ["gh", "issue", "create", "--title", "t"],
            "exit_code": 0,
            "stdout": "https://github.com/o/r/issues/99\n",
            "stderr": "",
        },
    ) as run_mock:
        result = github_cli(
            args=["issue", "create", "--title", "t", "--body", "b"],
            repo="o/r",
        )
    assert result["ok"] is True
    assert "issues/99" in result["stdout"]
    assert result["summary"] == "Created issue #99: https://github.com/o/r/issues/99"
    run_mock.assert_called_once()


def test_github_cli_runs_read() -> None:
    with patch(
        "integrations.github.tools.github_cli.tool.run_gh",
        return_value={
            "ok": True,
            "argv": ["gh", "issue", "list"],
            "exit_code": 0,
            "stdout": "1\tOpen bug\n",
            "stderr": "",
        },
    ):
        result = github_cli(args=["issue", "list"], repo="o/r")
    assert result["ok"] is True
    assert "Open bug" in result["summary"]


def test_summarize_gh_result_auto_merge() -> None:
    summary = summarize_gh_result(
        args=["pr", "merge", "3996", "--squash", "--auto"],
        ok=True,
        stdout="",
    )
    assert summary == "Enabled auto-merge for PR #3996."


def test_summarize_gh_api_json_is_prose_not_a_json_slice() -> None:
    stdout = (
        '{"login":"Tracer-Cloud","followers_url":'
        '"https://api.github.com/users/Tracer-Cloud/followers",'
        '"type":"Organization"}'
    )
    summary = summarize_gh_result(
        args=["api", "repos/tracer-cloud/opensre"],
        ok=True,
        stdout=stdout,
    )
    assert summary == "GitHub API call succeeded."
    assert "followers_url" not in summary


def test_summarize_gh_result_auto_merge_flags_before_number() -> None:
    summary = summarize_gh_result(
        args=["pr", "merge", "--squash", "--auto", "3996"],
        ok=True,
        stdout="",
    )
    assert summary == "Enabled auto-merge for PR #3996."


def test_summarize_gh_result_auto_merge_ignores_repo_flag_value() -> None:
    """``-R owner/repo`` values must not be mistaken for the PR number."""
    summary = summarize_gh_result(
        args=["pr", "merge", "-R", "acme/widgets", "--auto", "42"],
        ok=True,
        stdout="",
    )
    assert summary == "Enabled auto-merge for PR #42."


def test_summarize_gh_result_failure() -> None:
    summary = summarize_gh_result(
        args=["issue", "create", "--title", "t"],
        ok=False,
        error="GraphQL: Merge commits are not allowed",
        error_type="gh_error",
    )
    assert summary.startswith("GitHub action failed to run:")
    assert "Merge commits" in summary


def test_skill_guidance_attaches_to_github_cli() -> None:
    clear_tool_registry_cache()
    tools_by_name = {t.name: t for t in get_registered_tools()}
    assert "github_cli_write" not in tools_by_name
    tool = tools_by_name["github_cli"]
    assert "Workflow guidance:" in tool.description
    assert "github_cli" in tool.skill_guidance
    assert "shell_run" in tool.skill_guidance.lower() or "Never" in tool.skill_guidance
    # Capability map + failure clause must survive the registry truncation budget.
    assert "Create issue" in tool.skill_guidance
    assert "Arbitrary API" in tool.skill_guidance
    assert "failed to run" in tool.skill_guidance.lower()
    assert "summary" in tool.skill_guidance.lower()
    assert "markdown" in tool.skill_guidance.lower()
    assert not tool.skill_guidance.endswith("...")
