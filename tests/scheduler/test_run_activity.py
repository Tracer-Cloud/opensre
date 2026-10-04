"""A scheduled run attempt keeps the tool calls that changed something, bounded and redacted."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from core.agent_harness import AgentSession
from core.domain.types.tools import ToolRole
from core.llm.types import ToolCall
from core.tool import RegisteredTool, SideEffectLevel, ToolExecutionHooks
from core.tool.execution import execute_tool_calls
from infrastructure.scheduling.scheduler.outcomes import WorkOutcome, WorkStatus
from infrastructure.scheduling.scheduler.run_activity import (
    ACTION_MAX_CHARS,
    ACTIONS_KEPT,
    RunActivity,
    collect_run_activity,
)
from infrastructure.scheduling.scheduler.tool_actions import bound_action_hook

_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def _tool(
    name: str,
    run: Callable[..., Any],
    level: SideEffectLevel | None,
    *,
    role: ToolRole = ToolRole.ACTION,
) -> RegisteredTool:
    return RegisteredTool(
        name=name,
        description=name,
        input_schema={"type": "object", "properties": {}},
        source="github",
        run=run,
        side_effect_level=level,
        role=role,
    )


def _github_cli(args: list[str], **_kwargs: Any) -> dict[str, Any]:
    url = "https://github.com/o/r/pull/6555#issuecomment-99"
    return {"ok": args[0] != "fail", "stdout": url, "summary": f"Commented on PR #6555: {url}"}


def _repair(**_kwargs: Any) -> dict[str, Any]:
    outcome = WorkOutcome(
        status=WorkStatus.BLOCKED,
        error_kind="pr_not_open",
        operation="ci:o/r:pr:42",
        evidence={"pr_url": "https://github.com/o/r/pull/42"},
    )
    return {"success": False, "work_outcome": outcome.model_dump(mode="json")}


def _raise(**_kwargs: Any) -> dict[str, Any]:
    raise RuntimeError("network down")


def _ok(**_kwargs: Any) -> dict[str, Any]:
    return {"ok": True, "ts": "1700000000.1"}


_TOOLS = [
    _tool("github_cli", _github_cli, SideEffectLevel.MUTATING),
    _tool("fix_github_pr_ci", _repair, SideEffectLevel.MUTATING),
    _tool("slack_send_message", _ok, SideEffectLevel.EXTERNAL),
    _tool("list_github_prs", _ok, SideEffectLevel.READ_ONLY),
    _tool("query_datadog_logs", _ok, None),
    _tool("memory_remember", _ok, SideEffectLevel.MUTATING, role=ToolRole.BOOKKEEPING),
    _tool("shell_run", _raise, SideEffectLevel.MUTATING),
]


def _run(calls: list[ToolCall], hooks: ToolExecutionHooks | None) -> None:
    for call in calls:
        execute_tool_calls([call], _TOOLS, {}, hooks=hooks)


def test_only_calls_that_changed_something_are_recorded_with_what_they_acted_on() -> None:
    calls = [
        ToolCall(id="1", name="list_github_prs", input={}),
        ToolCall(id="2", name="query_datadog_logs", input={}),
        ToolCall(id="3", name="memory_remember", input={}),
        ToolCall(id="4", name="shell_run", input={"command": "git push origin fix/x"}),
        ToolCall(id="5", name="github_cli", input={"args": ["fail"], "repo": "o/r"}),
        ToolCall(
            id="6",
            name="github_cli",
            input={"args": ["pr", "comment", "6555", "--body", "Needs a decision."], "repo": "o/r"},
        ),
        ToolCall(
            id="7", name="fix_github_pr_ci", input={"owner": "o", "repo": "r", "pr_number": 42}
        ),
        ToolCall(
            id="8",
            name="slack_send_message",
            input={
                "channel_id": "C123",
                "message": "private body",
                "webhook_url": "https://hooks.slack.com/services/T/B/secret",
            },
        ),
    ]

    with collect_run_activity() as activity:
        _run(calls, ToolExecutionHooks(after_tool_call=bound_action_hook()))

    snapshot = activity.snapshot()
    assert snapshot.actions == (
        "github_cli pr comment 6555 … repo=o/r → Commented on PR #6555: "
        "https://github.com/o/r/pull/6555#issuecomment-99",
        "fix_github_pr_ci owner=o repo=r pr_number=42 → ci:o/r:pr:42 blocked (pr_not_open) "
        "pr_url=https://github.com/o/r/pull/42",
        "slack_send_message channel_id=C123",
    )
    assert snapshot.action_count == 3


def _shell_ok(command: str, **_kwargs: Any) -> dict[str, Any]:
    if command.startswith("git status"):
        return {"ok": True, "side_effect_level": SideEffectLevel.READ_ONLY.value}
    return {"ok": True}


@pytest.mark.parametrize(
    "command, action",
    [
        # Option values and quoted text never reach the record: only the
        # leading words, after a ``cd`` prefix and environment assignments.
        ('cd "/srv/my repo" && git commit -m "rotate key s3cr3t"', "shell_run git commit …"),
        ("GH_TOKEN=s3cr3t gh pr merge 12 --squash", "shell_run gh pr merge 12 …"),
        ('curl -H "Authorization: Bearer s3cr3t" https://api.example.com', "shell_run curl …"),
        ("git push origin fix/x", "shell_run git push origin fix/x"),
        # A call its tool reports as a read is not an action.
        ("git status --short", None),
    ],
)
def test_a_command_is_recorded_by_its_leading_words_and_reads_are_left_out(
    command: str, action: str | None
) -> None:
    tools = [_tool("shell_run", _shell_ok, SideEffectLevel.MUTATING)]

    with collect_run_activity() as activity:
        execute_tool_calls(
            [ToolCall(id="1", name="shell_run", input={"command": command})],
            tools,
            {},
            hooks=ToolExecutionHooks(after_tool_call=bound_action_hook()),
        )

    assert activity.snapshot().actions == ((action,) if action else ())


def test_two_calls_that_read_the_same_are_both_counted() -> None:
    tools = [_tool("shell_run", _shell_ok, SideEffectLevel.MUTATING)]
    calls = [
        ToolCall(id=str(index), name="shell_run", input={"command": f'git commit -m "fix {name}"'})
        for index, name in enumerate(("A", "B"))
    ]

    with collect_run_activity() as activity:
        execute_tool_calls(
            calls, tools, {}, hooks=ToolExecutionHooks(after_tool_call=bound_action_hook())
        )

    snapshot = activity.snapshot()
    assert snapshot.actions == ("shell_run git commit … (×2)",)
    assert snapshot.action_count == 1


def test_an_attempt_keeps_its_newest_distinct_actions_and_counts_the_rest() -> None:
    activity = RunActivity()

    for index in range(ACTIONS_KEPT + 5):
        activity.add_action(f"shell_run git push origin branch-{index} " + "x" * 300)
        activity.add_action(f"shell_run git push origin branch-{index} " + "x" * 300)

    snapshot = activity.snapshot()
    assert snapshot.action_count == ACTIONS_KEPT + 5
    assert len(snapshot.actions) == ACTIONS_KEPT
    assert snapshot.actions[0].startswith("shell_run git push origin branch-5 ")
    assert snapshot.actions[-1].startswith(f"shell_run git push origin branch-{ACTIONS_KEPT + 4} ")
    assert all(len(action) <= ACTION_MAX_CHARS for action in snapshot.actions)


def test_a_credential_never_reaches_an_action_or_note_even_when_cut() -> None:
    # The line is cut 12 characters into the token: only redacting first hides it.
    command = "deploy " + "--flag " * 23 + f"--token {_TOKEN}"

    with collect_run_activity() as activity:
        hook = bound_action_hook()
        tools = [_tool("shell_run", _ok, SideEffectLevel.MUTATING)]
        for value in (f"git push https://{_TOKEN}@github.com/o/r", command):
            execute_tool_calls(
                [ToolCall(id="1", name="shell_run", input={"command": value})],
                tools,
                {},
                hooks=ToolExecutionHooks(after_tool_call=hook),
            )

    activity.keep_note("x" * 290 + f" {_TOKEN}")

    snapshot = activity.snapshot()
    assert len(snapshot.actions) == 2
    assert all("ghp_" not in action for action in snapshot.actions)
    assert "ghp_" not in snapshot.carry_note


class _Turn:
    def chat(self, message: str) -> str:
        return message


def test_headless_turns_record_actions_only_inside_a_run_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound: list[ToolExecutionHooks | None] = []

    def start(*_args: object, tool_hooks: ToolExecutionHooks | None = None, **_kw: object) -> _Turn:
        bound.append(tool_hooks)
        return _Turn()

    monkeypatch.setattr(AgentSession, "start", start)
    caller_hooks = ToolExecutionHooks()

    AgentSession.run_headless_turn("no attempt is bound", tool_hooks=caller_hooks)
    with collect_run_activity() as activity:
        AgentSession.run_headless_turn("scheduled", tool_hooks=caller_hooks)

    outside, inside = bound
    assert outside is caller_hooks
    assert inside is not None and inside is not caller_hooks
    _run(
        [ToolCall(id="1", name="slack_send_message", input={"channel_id": "C9"})],
        inside,
    )
    assert activity.snapshot().actions == ("slack_send_message channel_id=C9",)
