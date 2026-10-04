"""Compact tool activity for the shell's hosted-gateway feed."""

from __future__ import annotations

import shlex

import pytest

import core.agent_harness.activity_display as activity_display
from config.constants.gateway import PROMPT_PROGRESS_KIND_PLAN, PROMPT_PROGRESS_KIND_TOOL
from core.agent_harness.activity_display import format_hosted_activity, generic_tool_activity
from core.agent_harness.task_plan.progress import task_plan_from_checklist


def test_github_activity_keeps_only_command_shape() -> None:
    activity = format_hosted_activity(
        "github_cli",
        {
            "args": [
                "api",
                "user",
                "--include",
                "-H",
                "Authorization: Bearer should-not-render",
                "--jq",
                ".[] | [.createdAt] | @tsv",
            ],
            "repo": "Tracer-Cloud/opensre",
        },
    )

    assert activity is not None
    assert activity.kind == PROMPT_PROGRESS_KIND_TOOL
    assert activity.text == "GitHub CLI · gh api request"
    assert "should-not-render" not in activity.text
    assert ".createdAt" not in activity.text


@pytest.mark.parametrize(
    ("args", "summary"),
    [
        (
            ["api", "repos/o/r", "-H", "Authorization: Bearer hunter2"],
            "gh api request",
        ),
        (["api", "repos/o/r", "--raw-field=credential=hunter2"], "gh api request"),
        (
            ["issue", "create", "--title", "private customer", "--body", "hunter2"],
            "gh issue create",
        ),
        (
            [
                "--repo",
                "Tracer-Cloud/opensre",
                "pr",
                "--hostname",
                "github.com",
                "comment",
                "7",
                "--body=Bearer hunter2",
            ],
            "gh pr comment",
        ),
        (["--jq", ".private", "pr", "view", "7"], "gh pr view"),
        (["pr", "--jq", ".private", "view", "7"], "gh pr view"),
        (["-t", "{{.private}}", "issue", "list"], "gh issue list"),
        (["--template", "{{.private}}", "run", "view", "8"], "gh run view"),
        (["search", "code", "hunter2"], "gh search code"),
        (["extension-name", "hunter2"], "gh command"),
    ],
)
def test_github_activity_keeps_safe_shape_without_free_form_values(
    args: list[str], summary: str
) -> None:
    activity = format_hosted_activity("github_cli", {"args": args})

    assert activity is not None
    assert activity.text == f"GitHub CLI · {summary}"
    assert "hunter2" not in activity.text
    assert "private customer" not in activity.text


def test_update_plan_is_a_checklist_not_a_dict() -> None:
    activity = format_hosted_activity(
        "update_plan",
        {
            "plan": [
                {"step": "Inspect authenticated user", "status": "completed"},
                {
                    "step": "Check repository creation permission",
                    "status": "in_progress",
                    "verifies": True,
                },
            ]
        },
    )

    assert activity is not None
    assert activity.kind == PROMPT_PROGRESS_KIND_PLAN
    assert "{'step'" not in activity.text
    assert "status" not in activity.text.splitlines()[1]
    assert "✓ Inspect authenticated user" in activity.text
    assert "● Check repository creation permission (verify)" in activity.text
    restored = task_plan_from_checklist(activity.text)
    assert restored is not None
    assert restored.steps[1].verifies is True
    assert restored.steps[1].step == "Check repository creation permission"


def test_ask_user_and_the_private_chooser_are_not_progress_rows() -> None:
    assert format_hosted_activity("ask_user_choice", {"title": "Which repo?"}) is None
    assert format_hosted_activity("slash_invoke", {"command": "/choose", "args": []}) is None


def test_local_generic_activity_keeps_a_bounded_argument_preview() -> None:
    label, content = generic_tool_activity(
        "custom_registry_tool",
        {
            "query": "x" * 400,
            "api_token": "secret-token",
            "timeout": 30,
            "payload": {"step": "Inspect authenticated user", "status": "completed"},
        },
    )

    assert label == "custom registry tool"
    assert content.endswith("…")
    assert "secret-token" not in content
    assert "timeout" not in content
    assert "{'step'" not in content
    assert "fields step, status" in content


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "summary"),
    [
        (
            "cli_exec",
            {"payload": "integrations setup --token hunter2"},
            "OpenSRE CLI · Run command",
        ),
        (
            "execute_python_code",
            {"code": "print('hunter2')", "inputs": ["customer", "hunter2"]},
            "Python · Run code",
        ),
        (
            "slash_invoke",
            {"command": "/model", "args": ["set", "hunter2"]},
            "OpenSRE · Run slash command",
        ),
        (
            "code_implement",
            {"task": "Put hunter2 in the customer config"},
            "Code · Implement changes",
        ),
        (
            "custom_registry_tool",
            {"query": "hunter2", "items": ["first", "hunter2"]},
            "custom registry tool · Run tool",
        ),
    ],
)
def test_hosted_generic_activity_never_includes_argument_values(
    tool_name: str, tool_input: dict[str, object], summary: str
) -> None:
    activity = format_hosted_activity(tool_name, tool_input)

    assert activity is not None
    assert activity.kind == PROMPT_PROGRESS_KIND_TOOL
    assert activity.text == summary
    assert "hunter2" not in activity.text
    assert "customer" not in activity.text


@pytest.mark.parametrize(
    ("command", "summary"),
    [
        (
            "curl -H 'Authorization: Bearer shell-secret' https://example.test",
            "Run network command",
        ),
        ("deploy --password shell-secret", "Run command"),
        ("uv run pytest tests/unit --token shell-secret", "Run tests"),
        ("uv run python -m pytest tests/unit --token shell-secret", "Run tests"),
        ('cd "/private/customer path" && git status', "Check Git status"),
        ('cd /d "C:/private/customer path" && pytest tests/unit', "Run tests"),
    ],
)
def test_shell_activity_uses_static_summaries_without_command_values(
    command: str, summary: str
) -> None:
    activity = format_hosted_activity("shell_run", {"command": command, "quiet": True})

    assert activity is not None
    assert activity.text == f"Shell · {summary}"
    assert "shell-secret" not in activity.text


def test_shell_activity_tokenizes_only_a_bounded_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed_lengths: list[int] = []
    real_shlex = shlex.shlex

    def recording_shlex(
        value: str,
        infile: str | None = None,
        posix: bool = False,
        punctuation_chars: bool | str = False,
    ) -> shlex.shlex:
        parsed_lengths.append(len(value))
        return real_shlex(
            value,
            infile=infile,
            posix=posix,
            punctuation_chars=punctuation_chars,
        )

    monkeypatch.setattr(activity_display.shlex, "shlex", recording_shlex)
    command = "git status '" + "x" * 10_000

    activity = format_hosted_activity("shell_run", {"command": command})

    assert activity is not None
    assert activity.text == "Shell · Check Git status"
    assert parsed_lengths == [activity_display._SHELL_SUMMARY_MAX_CHARS]
