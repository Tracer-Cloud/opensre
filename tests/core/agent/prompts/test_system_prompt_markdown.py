"""The action system prompt is loaded from bundled markdown."""

from __future__ import annotations

from pathlib import Path

from core.agent_harness.prompts import system_prompt as prompt_mod
from core.agent_harness.prompts.action.text import _SYSTEM_PROMPT_BASE
from core.agent_harness.prompts.system_prompt import _PROMPT_FILENAME


def test_system_prompt_base_comes_from_markdown_file() -> None:
    path = Path(prompt_mod.__file__).with_name(_PROMPT_FILENAME)
    assert path.is_file()
    assert path.name == "opensre_system_prompt.md"
    assert path.read_text(encoding="utf-8") == _SYSTEM_PROMPT_BASE


def test_system_prompt_completion_is_the_user_request_not_a_tool_call() -> None:
    collapsed = " ".join(_SYSTEM_PROMPT_BASE.split())
    assert "The user's request is the finish line, not that a tool ran" in collapsed
    assert "Listing tools, schemas, or a drafted query is not completion" in collapsed
    assert "Propose done with the evidence" in collapsed
    assert "If you cannot complete the request, say what blocked you and stop" in collapsed


def test_system_prompt_runs_explicit_commands_without_repository_probe() -> None:
    assert "execute it directly with the matching tool" in _SYSTEM_PROMPT_BASE
    assert "call `cli_exec` with the leading `opensre` prefix removed" in _SYSTEM_PROMPT_BASE
    assert "do not route it through `shell_run`" in _SYSTEM_PROMPT_BASE
    assert "Do not search for AGENTS.md files or inspect the repository first" in (
        _SYSTEM_PROMPT_BASE
    )


def test_actionable_results_are_bullets_not_a_paragraph() -> None:
    """A schedule card read as one dense block; nobody reached the commands in it."""
    collapsed = " ".join(_SYSTEM_PROMPT_BASE.split())
    assert "Three or more things the user can act on" in collapsed
    assert "are one bullet each, never a run of sentences" in collapsed
    assert "Repeat a card or list a tool already rendered line for line" in collapsed


def test_finite_material_ambiguity_requires_selectable_clarification() -> None:
    collapsed = " ".join(_SYSTEM_PROMPT_BASE.split())
    assert "Clarification is blocking whenever an underspecified request" in collapsed
    assert "materially different intents, goals, or execution paths" in collapsed
    assert "When TURN INTERACTION reports the menu is available" in collapsed
    assert "you MUST call `ask_user_choice`" in collapsed
    assert 'write a numbered "reply with 1, 2, or 3" list' in collapsed


def test_optional_choice_protections_follow_turn_interaction_facts() -> None:
    """Optional next-step menus follow TURN INTERACTION facts, not surface guessing."""
    text = _SYSTEM_PROMPT_BASE
    collapsed = " ".join(text.split())
    assert "TURN INTERACTION says the ask_user_choice menu is available" in collapsed
    assert "When the menu is unavailable or a session_goal is attached" in collapsed
    assert "finish, or one sentence of instructions" in collapsed
    assert "Always leave the user a selectable next step" not in text
    assert "headless, scheduled, or gateway" not in collapsed


def test_proactive_messages_are_new_actionable_and_time_sensitive() -> None:
    collapsed = " ".join(_SYSTEM_PROMPT_BASE.split())
    assert "standing policy for unsolicited messages" in collapsed
    assert "verified information not previously shared" in collapsed
    assert "names a clear owner and next action" in collapsed
    assert "timing that can materially affect the outcome" in collapsed
    assert "Use a direct message for a blocker owned by a specific person or team" in collapsed
    assert "Broadcast only decisions, anomalies, or milestones" in collapsed
    assert "when the underlying state has not changed" in collapsed
    assert "Do not ask whether to adopt this policy" in collapsed


def test_failed_commands_are_rerun_not_estimated() -> None:
    # Arrange / Act: the shell guidelines carry the rule as one bullet.
    shell_section = _SYSTEM_PROMPT_BASE.split("## Shell commands", 1)[1]

    # Assert
    assert "read-only measurement" in shell_section
    assert "fix it and run it again before answering" in shell_section
    assert "Do not rerun a command that may already have written files" in shell_section
    assert "Never replace a failed measurement" in shell_section
    assert "the command line still shows dimmed" in shell_section


def test_counts_come_from_the_whole_file() -> None:
    # Arrange / Act: the shell guidelines carry the rule as one bullet.
    shell_section = _SYSTEM_PROMPT_BASE.split("## Shell commands", 1)[1]

    # Assert: a range read undercounts silently, which is a wrong answer.
    assert "Counting or measuring from a file means reading all of it" in shell_section
    assert "wrong rather than approximate" in shell_section


def test_counts_come_from_a_parser_not_a_pattern() -> None:
    # Arrange / Act
    shell_section = _SYSTEM_PROMPT_BASE.split("## Shell commands", 1)[1]

    # Assert: a pattern over indentation answers a different question, and a
    # column must not be filled in when a neighbouring one is admitted unknown.
    assert "Count by parsing, not by pattern" in shell_section
    assert "answers a different question" in shell_section
    assert "say which field you could not read" in shell_section


def test_a_requested_plan_is_written_even_when_its_marks_are_declined() -> None:
    """Asked to tick undone steps, the model refused in prose and no checklist appeared."""
    collapsed = " ".join(_SYSTEM_PROMPT_BASE.split())
    assert "write it with `update_plan` even when you must decline the marks" in collapsed
    assert "The checklist with its statuses is the answer" in collapsed


def test_a_blocked_step_is_resolved_with_the_user_not_skipped() -> None:
    collapsed = " ".join(_SYSTEM_PROMPT_BASE.split())
    assert "A blocked step is resolved with the user, not skipped" in collapsed
