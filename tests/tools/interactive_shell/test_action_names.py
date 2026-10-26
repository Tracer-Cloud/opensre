"""Pin ToolKind members and their TOOL_KIND_TO_NAME mapping."""

from __future__ import annotations

from tools.interactive_shell.action_names import TOOL_KIND_TO_NAME, ActionToolName, ToolKind


def test_tool_kind_members_are_stable() -> None:
    assert [kind.value for kind in ToolKind] == [
        "slash",
        "shell",
        "task_cancel",
        "cli_command",
        "implementation",
        "llm_provider",
        "session_goal",
    ]


def test_tool_kind_round_trips_from_string() -> None:
    """Scenario YAML/harness fixtures reference kinds by plain string."""
    for kind in ToolKind:
        assert ToolKind(kind.value) is kind


def test_every_tool_kind_has_a_name_mapping() -> None:
    assert set(TOOL_KIND_TO_NAME) == set(ToolKind)


def test_tool_kind_to_name_mapping_values() -> None:
    assert TOOL_KIND_TO_NAME == {
        ToolKind.SLASH: "slash_invoke",
        ToolKind.SHELL: "shell_run",
        ToolKind.TASK_CANCEL: "task_cancel",
        ToolKind.CLI_COMMAND: "cli_exec",
        ToolKind.IMPLEMENTATION: "code_implement",
        ToolKind.LLM_PROVIDER: "llm_set_provider",
        ToolKind.SESSION_GOAL: "session_goal_set",
    }


def test_tool_kind_to_name_lookup_by_plain_string_key() -> None:
    """A dict keyed by ToolKind members must still be found via a plain string,
    since StrEnum members hash and compare equal to their value."""
    assert TOOL_KIND_TO_NAME["slash"] == "slash_invoke"
    assert TOOL_KIND_TO_NAME.get("session_goal") == "session_goal_set"


def test_action_tool_name_members_are_stable() -> None:
    assert [name.value for name in ActionToolName] == [
        "ask_user_choice",
        "cli_exec",
        "code_implement",
        "fix_sentry_issue_start",
        "llm_set_provider",
        "propose_scheduled_delivery",
        "session_goal_complete",
        "session_goal_set",
        "shell_run",
        "skill_view",
        "slash_invoke",
        "task_cancel",
        "update_plan",
    ]


def test_tool_kind_to_name_maps_onto_action_tool_name() -> None:
    assert set(TOOL_KIND_TO_NAME.values()) <= set(ActionToolName)
