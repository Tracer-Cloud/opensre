"""Tests for GitHubActionsTool functions."""

from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import patch

from integrations.github.tools.actions import (
    extract_step_log,
    get_github_actions_step_log,
    list_github_actions_active_runs,
    list_github_actions_run_jobs,
    list_github_actions_workflow_runs,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


def _registered_tool(tool: Any) -> Any:
    return tool.__opensre_registered_tool__


class TestListGitHubActionsWorkflowRunsContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(list_github_actions_workflow_runs)


class TestListGitHubActionsActiveRunsContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(list_github_actions_active_runs)


class TestListGitHubActionsRunJobsContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(list_github_actions_run_jobs)


class TestGetGitHubActionsStepLogContract(BaseToolContract):
    def get_tool_under_test(self):
        return _registered_tool(get_github_actions_step_log)


def _mcp_response(_config: object, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    method = arguments.get("method", "")
    if tool == "actions_list" and method == "list_workflow_runs":
        status = arguments.get("workflow_runs_filter", {}).get("status", "")
        if status == "queued":
            return {
                "tool": tool,
                "arguments": arguments,
                "is_error": False,
                "text": '{"total_count": 0, "workflow_runs": []}',
                "structured_content": None,
                "content": [],
            }
        if status == "in_progress":
            return {
                "tool": tool,
                "arguments": arguments,
                "is_error": False,
                "text": """{
                    "total_count": 1,
                    "workflow_runs": [
                        {
                            "id": 102,
                            "name": "Test",
                            "display_title": "CI test suite",
                            "head_branch": "main",
                            "event": "push",
                            "status": "in_progress",
                            "conclusion": null,
                            "created_at": "2026-05-27T11:00:00Z",
                            "actor": {"login": "dev"},
                            "triggering_actor": {"login": "dev"},
                            "pull_requests": []
                        }
                    ]
                }""",
                "structured_content": None,
                "content": [],
            }
        return {
            "tool": tool,
            "arguments": arguments,
            "is_error": False,
            "text": """{
                "total_count": 1,
                "workflow_runs": [
                    {
                        "id": 101,
                        "name": "Deploy",
                        "display_title": "Deploy to production",
                        "head_branch": "main",
                        "event": "workflow_dispatch",
                        "status": "completed",
                        "conclusion": "failure",
                        "created_at": "2026-05-27T10:00:00Z",
                        "actor": {"login": "release-bot"},
                        "triggering_actor": {"login": "release-bot"},
                        "pull_requests": []
                    }
                ]
            }""",
            "structured_content": None,
            "content": [],
        }

    if tool == "actions_list" and method == "list_workflow_jobs":
        return {
            "tool": tool,
            "arguments": arguments,
            "is_error": False,
            "text": """{
                "jobs": {
                    "total_count": 1,
                    "jobs": [
                        {
                            "id": 9001,
                            "run_id": 101,
                            "name": "deploy",
                            "status": "completed",
                            "conclusion": "failure",
                            "steps": [
                                {
                                    "name": "Checkout",
                                    "status": "completed",
                                    "conclusion": "success",
                                    "number": 1
                                },
                                {
                                    "name": "Deploy",
                                    "status": "completed",
                                    "conclusion": "failure",
                                    "number": 2
                                }
                            ]
                        }
                    ]
                }
            }""",
            "structured_content": None,
            "content": [],
        }

    if tool == "actions_get" and method == "get_workflow_job":
        return {
            "tool": tool,
            "arguments": arguments,
            "is_error": False,
            "text": """{
                "id": 9001,
                "run_id": 101,
                "name": "deploy",
                "status": "completed",
                "conclusion": "failure",
                "steps": [
                    {
                        "name": "Checkout",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1
                    },
                    {
                        "name": "Deploy",
                        "status": "completed",
                        "conclusion": "failure",
                        "number": 2
                    }
                ]
            }""",
            "structured_content": None,
            "content": [],
        }

    if tool == "get_job_logs":
        return {
            "tool": tool,
            "arguments": arguments,
            "is_error": False,
            "text": """{
                "job_id": 9001,
                "logs_content": "##[group]Checkout\\nCloning repository\\n##[endgroup]\\n##[group]Deploy\\nkubectl apply -f manifests/\\nError: secret rotation broke production deploy\\n##[endgroup]",
                "message": "Job logs content retrieved successfully",
                "original_length": 2000
            }""",
            "structured_content": None,
            "content": [],
        }

    return {
        "tool": tool,
        "arguments": arguments,
        "is_error": True,
        "text": f"Unhandled tool: {tool}",
        "structured_content": None,
        "content": [],
    }


def test_is_available_requires_github_source_owner_and_repo() -> None:
    rt = _registered_tool(list_github_actions_workflow_runs)
    assert rt.is_available(
        {"github": {"connection_verified": True, "owner": "org", "repo": "repo"}}
    )
    assert rt.is_available({"github": {"connection_verified": True}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_github_repository_fields() -> None:
    rt = _registered_tool(list_github_actions_workflow_runs)
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["owner"] == "my-org"
    assert params["repo"] == "my-repo"
    assert params["github_url"] == "http://github.example.com/mcp"
    assert params["github_mode"] == "streamable-http"
    assert params["github_token"] == "ghp_test"


def test_list_workflow_runs_happy_path() -> None:
    workflow_tool = cast(Any, list_github_actions_workflow_runs)
    with (
        patch("integrations.github.tools.actions.resolve_github_mcp_config", return_value=object()),
        patch("integrations.github.tools.actions.call_github_mcp_tool", side_effect=_mcp_response),
    ):
        result = workflow_tool(owner="org", repo="repo", github_token="tok")
    assert result["available"] is True
    assert result["workflow_runs"][0]["id"] == 101


def test_list_workflow_runs_passes_head_sha_filter() -> None:
    workflow_tool = cast(Any, list_github_actions_workflow_runs)
    captured: dict[str, object] = {}

    def _capture(config: object, tool: str, arguments: dict[str, object]) -> object:
        captured["tool"] = tool
        captured["arguments"] = arguments
        return _mcp_response(config, tool, arguments)

    with (
        patch("integrations.github.tools.actions.resolve_github_mcp_config", return_value=object()),
        patch("integrations.github.tools.actions.call_github_mcp_tool", side_effect=_capture),
    ):
        result = workflow_tool(
            owner="org",
            repo="repo",
            head_sha="abc123def",
            github_token="tok",
        )
    assert result["head_sha"] == "abc123def"
    assert captured["arguments"]["workflow_runs_filter"] == {"head_sha": "abc123def"}


def test_list_active_runs_happy_path() -> None:
    active_tool = cast(Any, list_github_actions_active_runs)
    with (
        patch("integrations.github.tools.actions.resolve_github_mcp_config", return_value=object()),
        patch("integrations.github.tools.actions.call_github_mcp_tool", side_effect=_mcp_response),
    ):
        result = active_tool(owner="org", repo="repo", github_token="tok")
    assert result["available"] is True
    assert result["workflow_runs"][0]["status"] == "in_progress"


def test_list_run_jobs_happy_path() -> None:
    jobs_tool = cast(Any, list_github_actions_run_jobs)
    with (
        patch("integrations.github.tools.actions.resolve_github_mcp_config", return_value=object()),
        patch("integrations.github.tools.actions.call_github_mcp_tool", side_effect=_mcp_response),
    ):
        result = jobs_tool(owner="org", repo="repo", run_id=101, github_token="tok")
    assert result["available"] is True
    assert result["jobs"][0]["name"] == "deploy"


def test_get_step_log_happy_path() -> None:
    log_tool = cast(Any, get_github_actions_step_log)
    with (
        patch("integrations.github.tools.actions.resolve_github_mcp_config", return_value=object()),
        patch("integrations.github.tools.actions.call_github_mcp_tool", side_effect=_mcp_response),
    ):
        result = log_tool(
            owner="org",
            repo="repo",
            run_id=101,
            job_id=9001,
            github_token="tok",
        )
    assert result["available"] is True
    assert result["step_name"] == "Deploy"
    assert "kubectl apply" in result["log_text"]


def test_extract_step_log_prefers_step_name() -> None:
    result = extract_step_log(
        """##[group]Checkout
line 1
##[endgroup]
##[group]Deploy
"""
        + ("A" * 7000)
        + "\n##[endgroup]\n",
        step_name="Deploy",
    )
    assert result["match_strategy"] == "step_name"
    assert result["step_name"] == "Deploy"
    assert "A" * 1000 in result["log_text"]


def test_extract_step_log_includes_trailing_annotations() -> None:
    result = extract_step_log(
        """##[group]Checkout
line 1
##[endgroup]
##[group]Deploy
line 2
##[endgroup]
##[error]Process completed with exit code 1.
""",
        step_name="Deploy",
    )
    assert result["step_name"] == "Deploy"
    assert result["match_strategy"] == "step_name"
    assert "line 2" in result["log_text"]
    assert "Process completed with exit code 1." in result["log_text"]


def test_extract_step_log_preserves_ungrouped_lines_around_groups() -> None:
    result = extract_step_log(
        """runner setup before groups
##[group]Checkout
line 1
##[endgroup]
annotation between groups
##[group]Deploy
line 2
##[endgroup]
final runner summary
""",
        step_name="Checkout",
    )

    assert result["step_name"] == "Checkout"
    assert result["match_strategy"] == "step_name"

    assert "line 1" in result["log_text"]
    assert "annotation between groups" in result["log_text"]

    assert "runner setup before groups" not in result["log_text"]
    assert "line 2" not in result["log_text"]
    assert "final runner summary" not in result["log_text"]


def test_extract_step_log_prefers_step_number() -> None:
    result = extract_step_log(
        """runner setup before groups
##[group]Checkout
line 1
##[endgroup]
annotation between groups
##[group]Deploy
line 2
##[endgroup]
final runner summary
""",
        step_number=1,
    )
    assert result["step_name"] == "Checkout"
    assert result["match_strategy"] == "step_number"
    assert "line 1" in result["log_text"]
    assert "annotation between groups" in result["log_text"]
    assert "runner setup before groups" not in result["log_text"]


def test_extract_step_log_invalid_step_number() -> None:
    result = extract_step_log(
        """runner setup before groups
##[group]Checkout
line 1
##[endgroup]
annotation between groups
##[group]Deploy
line 2
##[endgroup]
final runner summary
""",
        step_number=3,
    )
    assert result["step_name"] == "full-log"
    assert result["match_strategy"] == "full-log"
    assert "runner setup before groups" in result["log_text"]
    assert "line 1" in result["log_text"]
    assert "annotation between groups" in result["log_text"]
    assert "line 2" in result["log_text"]
    assert "final runner summary" in result["log_text"]


def test_extract_step_log_ungrouped_only_step_number_reports_full_log() -> None:
    """A log with zero ##[group] markers has no real steps to pick by number.

    Regression for the bug where the single ungrouped-log fallback section was
    itself miscounted as step #1.
    """
    result = extract_step_log(
        "plain log line 1\nplain log line 2\n",
        step_number=1,
    )
    assert result["match_strategy"] == "full-log"
    assert result["step_name"] == "full-log"
    assert "plain log line 1" in result["log_text"]
    assert "plain log line 2" in result["log_text"]


def test_get_step_log_reports_truncated_when_original_lines_exceeds_returned() -> None:
    log_tool = cast(Any, get_github_actions_step_log)
    with (
        patch("integrations.github.tools.actions.resolve_github_mcp_config", return_value=object()),
        patch("integrations.github.tools.actions.call_github_mcp_tool", side_effect=_mcp_response),
    ):
        result = log_tool(owner="org", repo="repo", run_id=101, job_id=9001, github_token="tok")
    assert result["truncated"] is True
    assert result["original_lines"] == 2000
    assert result["returned_lines"] < result["original_lines"]
    assert result["retry_attempted"] is False
    assert result["retry_error"] is None


def test_get_step_log_no_retry_when_original_length_field_missing() -> None:
    """A get_job_logs response without original_length must degrade safely:
    no truncation signal, no retry attempt."""

    def mcp_response(config: object, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool != "get_job_logs":
            return _mcp_response(config, tool, arguments)
        return {
            "tool": tool,
            "arguments": arguments,
            "is_error": False,
            "text": json.dumps({"job_id": 9001, "logs_content": "no group markers here"}),
            "structured_content": None,
            "content": [],
        }

    log_tool = cast(Any, get_github_actions_step_log)
    with (
        patch("integrations.github.tools.actions.resolve_github_mcp_config", return_value=object()),
        patch("integrations.github.tools.actions.call_github_mcp_tool", side_effect=mcp_response),
    ):
        result = log_tool(owner="org", repo="repo", run_id=101, job_id=9001, github_token="tok")

    assert result["original_lines"] is None
    assert result["truncated"] is False
    assert result["retry_attempted"] is False


def test_get_step_log_retries_with_larger_tail_when_step_missing() -> None:
    """First fetch (tail_lines=500) truncates the log before the Deploy group;
    the tool should re-fetch a bigger tail instead of settling for full-log."""

    def mcp_response(config: object, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool != "get_job_logs":
            return _mcp_response(config, tool, arguments)
        if arguments.get("tail_lines") == 500:
            logs_content = "##[group]Checkout\nCloning repository\n##[endgroup]"
        else:
            logs_content = (
                "##[group]Checkout\nCloning repository\n##[endgroup]\n"
                "##[group]Deploy\nkubectl apply -f manifests/\n##[endgroup]"
            )
        return {
            "tool": tool,
            "arguments": arguments,
            "is_error": False,
            "text": json.dumps(
                {"job_id": 9001, "logs_content": logs_content, "original_length": 5000}
            ),
            "structured_content": None,
            "content": [],
        }

    log_tool = cast(Any, get_github_actions_step_log)
    with (
        patch("integrations.github.tools.actions.resolve_github_mcp_config", return_value=object()),
        patch("integrations.github.tools.actions.call_github_mcp_tool", side_effect=mcp_response),
    ):
        result = log_tool(
            owner="org",
            repo="repo",
            run_id=101,
            job_id=9001,
            step_name="Deploy",
            github_token="tok",
        )

    assert result["match_strategy"] == "step_name"
    assert "kubectl apply" in result["log_text"]
    assert result["retry_attempted"] is True
    assert result["retry_error"] is None


def test_get_step_log_reports_retry_error_when_retry_fetch_fails() -> None:
    """If the larger-tail retry itself fails, surface that instead of silently
    keeping the truncated first response with no explanation."""

    def mcp_response(config: object, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool != "get_job_logs":
            return _mcp_response(config, tool, arguments)
        if arguments.get("tail_lines") == 500:
            return {
                "tool": tool,
                "arguments": arguments,
                "is_error": False,
                "text": json.dumps(
                    {
                        "job_id": 9001,
                        "logs_content": "##[group]Checkout\nCloning repository\n##[endgroup]",
                        "original_length": 5000,
                    }
                ),
                "structured_content": None,
                "content": [],
            }
        return {
            "tool": tool,
            "arguments": arguments,
            "is_error": True,
            "text": "rate limited",
            "structured_content": None,
            "content": [],
        }

    log_tool = cast(Any, get_github_actions_step_log)
    with (
        patch("integrations.github.tools.actions.resolve_github_mcp_config", return_value=object()),
        patch("integrations.github.tools.actions.call_github_mcp_tool", side_effect=mcp_response),
    ):
        result = log_tool(
            owner="org",
            repo="repo",
            run_id=101,
            job_id=9001,
            step_name="Deploy",
            github_token="tok",
        )

    assert result["match_strategy"] == "full-log"
    assert result["retry_attempted"] is True
    assert result["retry_error"] == "rate limited"
    assert result["truncated"] is True


def _job_logs_result(arguments: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool": "get_job_logs",
        "arguments": arguments,
        "is_error": False,
        "text": json.dumps(payload),
        "structured_content": None,
        "content": [],
    }


def test_get_step_log_keeps_small_body_when_retry_also_misses_the_step() -> None:
    """A retry that still can't find the step must not swap the small first
    response for the multi-thousand-line retry body: that goes straight into the
    agent's context for no gain."""
    small_log = "##[group]Checkout\nCloning repository\n##[endgroup]"
    huge_log = "\n".join(f"unrelated log line {i}" for i in range(3000))

    def mcp_response(config: object, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool != "get_job_logs":
            return _mcp_response(config, tool, arguments)
        content = small_log if arguments.get("tail_lines") == 500 else huge_log
        return _job_logs_result(
            arguments, {"job_id": 9001, "logs_content": content, "original_length": 5000}
        )

    log_tool = cast(Any, get_github_actions_step_log)
    with (
        patch("integrations.github.tools.actions.resolve_github_mcp_config", return_value=object()),
        patch("integrations.github.tools.actions.call_github_mcp_tool", side_effect=mcp_response),
    ):
        result = log_tool(
            owner="org",
            repo="repo",
            run_id=101,
            job_id=9001,
            step_name="Deploy",
            github_token="tok",
        )

    assert result["retry_attempted"] is True
    assert result["retry_error"] is None
    assert result["match_strategy"] == "full-log"
    assert result["log_text"] == small_log
    assert result["returned_lines"] == len(small_log.splitlines())


def test_get_step_log_does_not_report_complete_log_with_blank_lines_as_truncated() -> None:
    """original_length counts blank lines too, so the returned text must be
    measured unstripped or a complete log looks truncated and burns a retry."""
    logs_content = "\nsetup\nrun tests\nteardown\n\n"

    def mcp_response(config: object, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool != "get_job_logs":
            return _mcp_response(config, tool, arguments)
        return _job_logs_result(
            arguments,
            {
                "job_id": 9001,
                "logs_content": logs_content,
                "original_length": len(logs_content.splitlines()),
            },
        )

    log_tool = cast(Any, get_github_actions_step_log)
    with (
        patch("integrations.github.tools.actions.resolve_github_mcp_config", return_value=object()),
        patch("integrations.github.tools.actions.call_github_mcp_tool", side_effect=mcp_response),
    ):
        result = log_tool(owner="org", repo="repo", run_id=101, job_id=9001, github_token="tok")

    assert result["returned_lines"] == 5
    assert result["truncated"] is False
    assert result["retry_attempted"] is False


def test_get_step_log_retry_without_original_length_keeps_known_line_count() -> None:
    """A retry response that omits original_length must not turn a known total
    into None and silently flip truncated back to False."""

    def mcp_response(config: object, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool != "get_job_logs":
            return _mcp_response(config, tool, arguments)
        if arguments.get("tail_lines") == 500:
            return _job_logs_result(
                arguments,
                {
                    "job_id": 9001,
                    "logs_content": "##[group]Checkout\nCloning repository\n##[endgroup]",
                    "original_length": 5000,
                },
            )
        return _job_logs_result(
            arguments,
            {
                "job_id": 9001,
                "logs_content": (
                    "##[group]Checkout\nCloning repository\n##[endgroup]\n"
                    "##[group]Deploy\nkubectl apply -f manifests/\n##[endgroup]"
                ),
            },
        )

    log_tool = cast(Any, get_github_actions_step_log)
    with (
        patch("integrations.github.tools.actions.resolve_github_mcp_config", return_value=object()),
        patch("integrations.github.tools.actions.call_github_mcp_tool", side_effect=mcp_response),
    ):
        result = log_tool(
            owner="org",
            repo="repo",
            run_id=101,
            job_id=9001,
            step_name="Deploy",
            github_token="tok",
        )

    assert result["match_strategy"] == "step_name"
    assert result["original_lines"] == 5000
    assert result["truncated"] is True


def test_get_step_log_unavailable_payload_carries_truncation_keys() -> None:
    """The error paths must expose the same keys as the success path so callers
    never have to key-check before reading them."""
    log_tool = cast(Any, get_github_actions_step_log)
    with patch("integrations.github.tools.actions.resolve_github_mcp_config", return_value=None):
        result = log_tool(owner="org", repo="repo", run_id=101, job_id=9001)

    assert result["available"] is False
    assert result["truncated"] is False
    assert result["returned_lines"] == 0
    assert result["original_lines"] is None
    assert result["retry_attempted"] is False
    assert result["retry_error"] is None
