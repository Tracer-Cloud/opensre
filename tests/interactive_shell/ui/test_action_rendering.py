"""Tests for interactive-shell action rendering."""

from __future__ import annotations

import io
import re
from unittest.mock import Mock

import pytest
from rich.console import Console
from rich.text import Text

import surfaces.interactive_shell.runtime.slash_adapter as slash_adapter
from core.agent_harness.turns.turn_results import ToolCallingTurnResult
from infrastructure.terminal.theme import BOLD_SKILL, TEXT
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.action_rendering import (
    ActionRenderObserver,
    tool_call_display,
)
from surfaces.interactive_shell.ui.input_prompt.rendering import (
    _prompt_turn_number,
    render_submitted_prompt,
)
from tests.core.agent.orchestration.action_execution_test_harness import (
    ActionExecutionHarness,
    FakeActionLLM,
    no_tool_response,
)
from tests.shared.harness_turn_driver import run_harness_turn


def test_slash_invoke_tool_start_does_not_record_cli_agent() -> None:
    session = Session()
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False)
    observer = ActionRenderObserver(session=session, console=console, message="/model show")

    observer(
        "tool_start",
        {"name": "slash_invoke", "input": {"command": "/model", "args": ["show"]}},
    )

    # A user slash command is echoed as the ``[N]`` row, so it is not added to
    # the tool action log (that would duplicate it, and strand a line at exit).
    assert session.history == []
    assert observer.planned_count == 1
    assert session.terminal.action_log_entries == []


def test_internal_choose_slash_has_no_tool_preview() -> None:
    session = Session()
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False)
    observer = ActionRenderObserver(session=session, console=console, message="/choose")

    observer(
        "tool_start",
        {"name": "slash_invoke", "input": {"command": "/choose", "args": []}},
    )

    assert observer.planned_count == 1
    assert buffer.getvalue() == ""


def test_ask_user_choice_has_no_generic_tool_preview() -> None:
    observer, buffer = _observer_with_buffer("ask me to choose")

    observer(
        "tool_start",
        {
            "name": "ask_user_choice",
            "input": {"title": "Deploy how?", "options": ["Canary", "Rolling"]},
        },
    )

    assert observer.planned_count == 1
    assert buffer.getvalue() == ""


def test_shell_run_tool_start_does_not_record_cli_agent() -> None:
    session = Session()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    observer = ActionRenderObserver(session=session, console=console, message="!true")

    observer("tool_start", {"name": "shell_run", "input": {"command": "true"}})

    assert session.history == []
    assert observer.planned_count == 1


def _observer_with_buffer(message: str = "onboard me") -> tuple[ActionRenderObserver, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=100)
    return ActionRenderObserver(session=Session(), console=console, message=message), buffer


def test_message_update_before_tool_calls_renders_live() -> None:
    """Phase headers preceding a tool group appear in the terminal as Markdown."""
    observer, buffer = _observer_with_buffer()

    observer(
        "message_update",
        {
            "content": "### [1/8] Prerequisite checks\nRunning GitHub CLI checks…",
            "has_tool_calls": True,
        },
    )

    output = buffer.getvalue()
    assert "[1/8] Prerequisite checks" in output
    assert "Running GitHub CLI checks" in output


def test_message_update_final_answer_is_not_rendered() -> None:
    """The closing no-tool-call answer is streamed by the turn driver, not here."""
    observer, buffer = _observer_with_buffer()

    observer("message_update", {"content": "All done.", "has_tool_calls": False})

    assert buffer.getvalue() == ""


def test_message_update_with_blank_content_prints_nothing() -> None:
    observer, buffer = _observer_with_buffer()

    observer("message_update", {"content": "   ", "has_tool_calls": True})

    assert buffer.getvalue() == ""


def _skill_observer() -> tuple[ActionRenderObserver, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, highlight=False, width=100)
    observer = ActionRenderObserver(session=Session(), console=console, message="run code review")
    return observer, buffer


def test_skill_view_renders_activation_event() -> None:
    """Loading a skill shows the two-line activation tree, nothing else."""
    observer, buffer = _skill_observer()

    observer(
        "tool_start",
        {"id": "t1", "name": "skill_view", "input": {"name": "install_code_review"}},
    )
    observer(
        "tool_end",
        {
            "id": "t1",
            "name": "skill_view",
            "input": {"name": "install_code_review"},
            "output": {"ok": True, "name": "install-code-review", "content": "<CDATA body>"},
        },
    )

    assert buffer.getvalue() == "\nSkill install-code-review\n  ↳ Skill activated\n"


def test_two_skills_in_one_batch_name_the_skill_on_each_activation_line() -> None:
    """Headers print first, results after; the first skill's line must say which skill."""
    observer, buffer = _skill_observer()
    for call_id, name in (("t1", "github_ci_health"), ("t2", "github_ci_fix_onboarding")):
        observer("tool_start", {"id": call_id, "name": "skill_view", "input": {"name": name}})
    for call_id, name in (("t1", "github-ci-health"), ("t2", "github-ci-fix-onboarding")):
        observer(
            "tool_end",
            {
                "id": call_id,
                "name": "skill_view",
                "input": {"name": name},
                "output": {"ok": True, "name": name, "content": "<body>"},
            },
        )

    assert buffer.getvalue() == (
        "\nSkill github-ci-health\n\nSkill github-ci-fix-onboarding\n"
        "  ↳ github-ci-health activated\n  ↳ Skill activated\n"
    )


def test_skill_view_renders_bold_green_skill_label() -> None:
    console = Mock(spec=Console)
    observer = ActionRenderObserver(session=Session(), console=console, message="run code review")

    observer(
        "tool_start",
        {"id": "t1", "name": "skill_view", "input": {"name": "install_code_review"}},
    )

    heading = console.print.call_args_list[1].args[0]
    assert isinstance(heading, Text)
    assert heading.plain == "Skill install-code-review"
    assert len(heading.spans) == 2
    assert str(heading.spans[0].style) == BOLD_SKILL
    assert str(heading.spans[1].style) == str(TEXT)


def test_skill_view_strips_terminal_controls_from_model_name() -> None:
    # Arrange: a model-supplied skill name carrying an ANSI escape + BEL
    console = Mock(spec=Console)
    observer = ActionRenderObserver(session=Session(), console=console, message="run code review")

    # Act
    observer(
        "tool_start",
        {"id": "t1", "name": "skill_view", "input": {"name": "code\x1b[2Kreview\x07"}},
    )

    # Assert: the rendered skill heading carries no C0/C1/DEL controls
    heading = console.print.call_args_list[1].args[0]
    assert isinstance(heading, Text)
    assert "\x1b" not in heading.plain
    assert "\x07" not in heading.plain


def test_tool_call_display_strips_terminal_controls_from_model_args() -> None:
    # Arrange + Act: a model-supplied tool arg with an ANSI escape + BEL
    label, content = tool_call_display("shell_run", {"command": "ls\x1b[2Krm\x07"})

    # Assert: no control characters survive into the raw Rich line
    assert "\x1b" not in content
    assert "\x07" not in content
    assert content == "ls[2Krm"


def test_github_cli_tool_call_display_uses_sdk_arguments_without_runtime_details() -> None:
    label, content = tool_call_display(
        "github_cli",
        {
            "args": [
                "search",
                "prs",
                "-H",
                "Authorization: Bearer should-not-render",
                "--repo",
                "react/react",
                "--merged",
                "--merged-at",
                "2026-08-01..2026-08-26",
                "--jq",
                ".[] | [.createdAt, .closedAt] | @tsv",
            ],
            "repo": "facebook/react",
            "timeout": 120,
        },
    )

    assert label == "GitHub CLI"
    assert content.startswith("gh -R facebook/react search prs")
    assert "-H …" in content
    assert "should-not-render" not in content
    assert "--repo react/react" in content
    assert "--merged-at 2026-08-01..2026-08-26" in content
    assert "--jq …" in content
    assert ".createdAt" not in content
    assert "timeout" not in content
    assert "120" not in content


def test_python_tool_call_display_summarizes_execution_with_safe_input_values() -> None:
    label, content = tool_call_display(
        "execute_python_code",
        {
            "allow_network": True,
            "code": "print(inputs['github_token'])",
            "inputs": {
                "owner": "react",
                "repo": "react",
                "week_start_local": "2026-08-24T00:00:00+01:00",
            },
            "timeout": 60,
        },
    )

    assert label == "Python"
    assert content == (
        "run analysis · network enabled · inputs: owner=react, repo=react, "
        "week_start_local=2026-08-24T00:00:00+01:00"
    )
    assert "print" not in content
    assert "timeout" not in content
    assert "60" not in content


def test_python_tool_call_display_derives_high_level_details_from_source() -> None:
    label, content = tool_call_display(
        "execute_python_code",
        {
            "allow_network": True,
            "code": """
owner = inputs["owner"]
url = f"https://api.github.com/repos/{owner}/react/stargazers?per_page=100"
print({"stars_gained": 3, "pages_scanned": 2})
""",
            "timeout": 60,
        },
    )

    assert label == "Python"
    assert "target: api.github.com/repos/{owner}/react/stargazers" in content
    assert "inputs: owner" in content
    assert "outputs: stars_gained, pages_scanned" in content
    assert "network enabled" in content
    assert "per_page" not in content
    assert "print" not in content
    assert "timeout" not in content


def test_generic_tool_call_display_is_bounded_and_omits_execution_controls() -> None:
    label, content = tool_call_display(
        "custom_registry_tool",
        {
            "query": "x" * 400,
            "limit": 25,
            "timeout": 120,
            "api_token": "secret-token",
        },
    )

    assert label == "custom registry tool"
    assert len(content) <= 180
    assert content.endswith("…")
    assert not content.startswith("{")
    assert "limit: 25" in content
    assert "query:" in content
    assert "timeout" not in content
    assert "api_token" not in content
    assert "secret-token" not in content


def test_intermediate_message_strips_terminal_controls_before_markdown() -> None:
    # Arrange: a real terminal, where Rich would otherwise pass the model's
    # control bytes straight through (a non-terminal console strips them anyway,
    # so this must force a terminal to exercise the sanitizer).
    session = Session()
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, highlight=False, width=80)
    observer = ActionRenderObserver(session=session, console=console, message="x")

    # Act: model narration carrying a clear-screen escape and a BEL
    observer(
        "message_update",
        {
            "has_tool_calls": True,
            "content": "### [1/2] Scope\x1b[2J\nLooking at p99\x07",
        },
    )

    # Assert: the dangerous payloads are gone (Rich's own styling escapes are
    # ``ESC[…m``, never ``ESC[2J``, so this stays a clean security assertion),
    # while the newline-separated prose survives.
    output = buffer.getvalue()
    assert "\x1b[2J" not in output
    assert "\x07" not in output
    assert "Scope" in output
    assert "Looking at p99" in output


def test_skill_view_failure_renders_failure_child() -> None:
    observer, buffer = _skill_observer()

    observer(
        "tool_start",
        {"id": "t1", "name": "skill_view", "input": {"name": "no-such-skill"}},
    )
    observer(
        "tool_end",
        {
            "id": "t1",
            "name": "skill_view",
            "input": {"name": "no-such-skill"},
            "output": {"error": "unknown skill 'no-such-skill'"},
        },
    )

    assert buffer.getvalue() == "\nSkill no-such-skill\n  ↳ Skill failed to load\n"


def test_skill_view_tool_end_without_start_prints_nothing() -> None:
    observer, buffer = _skill_observer()

    observer(
        "tool_end",
        {
            "id": "t9",
            "name": "skill_view",
            "input": {"name": "morning-report"},
            "output": {"ok": True},
        },
    )

    assert buffer.getvalue() == ""


def test_llm_start_sets_thinking_phase_without_verb_rotation() -> None:
    """``llm_start`` labels the status row Thinking…; phase labels are the UX."""
    from surfaces.interactive_shell.runtime.core.state import SpinnerState
    from surfaces.shared.terminal.output.console_state import set_turn_spinner

    observer, _buffer = _observer_with_buffer()
    spinner = SpinnerState()
    spinner.start()
    set_turn_spinner(spinner)
    try:
        observer("llm_start", {"iteration": 0})
        assert spinner.phase == SpinnerState.THINKING_PHASE
        observer("llm_start", {"iteration": 2})
        assert spinner.phase == SpinnerState.THINKING_PHASE
        assert "Thinking…" in re.sub(r"\x1b\[[0-9;]*m", "", spinner.inline_spinner_ansi())
    finally:
        set_turn_spinner(None)


def test_llm_start_without_registered_spinner_is_noop() -> None:
    observer, buffer = _observer_with_buffer()

    observer("llm_start", {"iteration": 3})

    assert buffer.getvalue() == ""
    assert observer.planned_count == 0


def test_message_update_does_not_record_history_or_count_as_planned() -> None:
    observer, _buffer = _observer_with_buffer()

    observer("message_update", {"content": "### [1/8] Checks", "has_tool_calls": True})

    assert observer.session.history == []
    assert observer.planned_count == 0


def test_non_skill_tool_end_prints_nothing() -> None:
    observer, buffer = _skill_observer()

    observer("tool_start", {"id": "t1", "name": "shell_run", "input": {"command": "true"}})
    after_start = buffer.getvalue()
    observer(
        "tool_end",
        {"id": "t1", "name": "shell_run", "input": {"command": "true"}, "output": {"ok": True}},
    )

    # Self-rendering tools (shell_run) already printed ``$ cmd`` + output;
    # a generic tool_end child would duplicate that block.
    assert buffer.getvalue() == after_start


def test_generic_tool_end_nests_the_result_under_the_call() -> None:
    """Droid / Claude Code / Cursor attach the result to the call as a ``↳`` child."""
    observer, buffer = _observer_with_buffer()

    observer(
        "tool_start",
        {"id": "t1", "name": "github_cli", "input": {"args": ["api", "user"]}},
    )
    observer(
        "tool_end",
        {
            "id": "t1",
            "name": "github_cli",
            "output": {"ok": True, "summary": "GitHub API call succeeded."},
        },
    )

    # The call is buffered — nothing prints live until the log flushes.
    assert buffer.getvalue() == ""
    entries = observer.session.terminal.action_log_entries
    assert len(entries) == 1
    assert entries[0].kind == "GitHub CLI"
    assert "↳ GitHub API call succeeded" in entries[0].detail
    assert observer.session.terminal.inline_tool_results is True

    observer("agent_end", {})
    out = buffer.getvalue()
    assert "GitHub CLI" in out
    assert "↳ GitHub API call succeeded" in out


def test_generic_tool_end_hides_a_json_blob() -> None:
    """A ``gh api`` payload is for the model — nest nothing, leave the reply to summarize."""
    observer, buffer = _observer_with_buffer()

    observer(
        "tool_start",
        {"id": "t1", "name": "github_cli", "input": {"args": ["api", "user"]}},
    )
    observer(
        "tool_end",
        {
            "id": "t1",
            "name": "github_cli",
            "output": {"ok": True, "stdout": '{"login":"Tracer-Cloud"}'},
        },
    )

    # The call is buffered; the JSON blob result is not folded under it — the
    # reply summarizes model-only data.
    assert buffer.getvalue() == ""
    entries = observer.session.terminal.action_log_entries
    assert len(entries) == 1
    assert "gh api user" in entries[0].detail
    assert "login" not in entries[0].detail
    assert observer.session.terminal.inline_tool_results is False

    observer("agent_end", {})
    out = buffer.getvalue()
    assert "gh api user" in out
    assert "login" not in out


def test_skill_block_renders_live_not_buffered() -> None:
    """Skill blocks print live with one gap; tool calls are buffered, not shown yet."""
    observer, buffer = _observer_with_buffer()

    observer(
        "tool_start",
        {"id": "s1", "name": "skill_view", "input": {"name": "install_code_review"}},
    )
    observer(
        "tool_end",
        {
            "id": "s1",
            "name": "skill_view",
            "output": {"ok": True, "name": "install-code-review"},
        },
    )
    observer(
        "tool_start",
        {"id": "t1", "name": "github_cli", "input": {"args": ["api", "user"]}},
    )

    out = buffer.getvalue()
    assert "\nSkill install-code-review\n  ↳ Skill activated\n" in out
    assert "\n\n\n" not in out
    # The github call is buffered for the grouped log, not printed inline yet.
    assert any(e.kind == "GitHub CLI" for e in observer.session.terminal.action_log_entries)


def test_literal_slash_command_records_single_history_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatched: list[str] = []

    def _fake_dispatch(
        command: str,
        session: Session,
        console: Console,
        **_kwargs: object,
    ) -> bool:
        dispatched.append(command)
        session.record("slash", command, ok=True)
        return True

    monkeypatch.setattr(slash_adapter, "dispatch_slash", _fake_dispatch)
    session = Session()
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response()]))
    render_submitted_prompt(harness.console, session, "/model show")

    result = run_action_tool_turn(
        "/model show",
        session,
        harness.console,
        llm_factory=harness.llm_factory,
    )

    assert result.handled is True
    assert dispatched == ["/model show"]
    assert session.history == [{"type": "slash", "text": "/model show", "ok": True}]
    # The turn's history recording must not advance the prompt number; only the
    # submission itself does.
    assert _prompt_turn_number(session) == 2


def test_chat_turn_records_single_cli_agent_history_entry() -> None:
    session = Session()
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    render_submitted_prompt(console, session, "what broke in prod?")

    def _no_actions(
        _text: str,
        _session: Session,
        _console: Console,
        **kwargs: object,
    ) -> ToolCallingTurnResult:
        return ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
            response_text="hello back",
        )

    run_harness_turn(
        "what broke in prod?",
        session,
        console,
        recorder=None,
        execute_actions=_no_actions,
    )

    assert session.history == [{"type": "cli_agent", "text": "what broke in prod?", "ok": True}]
    # The turn's history recording must not advance the prompt number; only the
    # submission itself does.
    assert _prompt_turn_number(session) == 2


def test_set_spinner_phase_does_not_activate_a_suppressed_spinner() -> None:
    """A suppressed (never-started) spinner must not be activated by the observer.

    Literal slash turns skip spinner start()/stop(); activating it on
    llm_start / tool_start would leave the spinner on screen after the command.
    """
    from surfaces.interactive_shell.runtime.core.state import SpinnerState
    from surfaces.shared.terminal.output.console_state import set_turn_spinner

    observer, _buffer = _observer_with_buffer()
    spinner = SpinnerState()  # not started -> streaming False (suppressed)
    set_turn_spinner(spinner)
    try:
        observer("llm_start", {"iteration": 0})
        observer("tool_start", {"name": "slash_invoke", "input": {"command": "/model"}})
        assert spinner.streaming is False
    finally:
        set_turn_spinner(None)


_UPDATE_PLAN = [
    {"step": "Measure wait vs work", "status": "pending"},
    {"step": "Verify p99 recovery", "status": "pending"},
]


def test_update_plan_tool_start_does_not_commit_session_state() -> None:
    """Rejected update_plan must not leave a plan that never succeeded."""
    from core.agent_harness.task_plan.plan import parse_task_plan

    observer, buffer = _observer_with_buffer("plan the fix")
    plan, error = parse_task_plan({"plan": _UPDATE_PLAN, "explanation": "### Facts\n- p99 up"})
    assert error is None and plan is not None

    observer(
        "tool_start",
        {
            "id": "p1",
            "name": "update_plan",
            "input": {
                "plan": _UPDATE_PLAN,
                "explanation": "### Facts\n- p99 up",
                "plan_only": True,
            },
        },
    )
    assert observer.session.task_plan is None
    assert observer.session.plan_only_until_authorized is False
    assert "Plan" not in buffer.getvalue()

    # Simulate a failed tool (schema/hook rejection) — still no session write.
    observer(
        "tool_end", {"id": "p1", "name": "update_plan", "output": {"ok": False, "error": "nope"}}
    )
    assert observer.session.task_plan is None
    assert observer.session.plan_only_until_authorized is False
    assert "Plan" not in buffer.getvalue()


def test_update_plan_is_not_dumped_into_the_transcript() -> None:
    # The plan renders only in the pinned bottom overlay; a successful
    # update_plan must not print the checklist or the diagnosis into scrollback.
    from core.agent_harness.task_plan.plan import parse_task_plan
    from core.agent_harness.task_plan.update_plan_policy import apply_update_plan_session

    observer, buffer = _observer_with_buffer("plan the fix")
    plan, error = parse_task_plan({"plan": _UPDATE_PLAN, "explanation": "### Facts\n- p99 up"})
    assert error is None and plan is not None
    apply_update_plan_session(observer.session, plan, plan_only=True)

    observer("tool_end", {"id": "p1", "name": "update_plan", "output": {"ok": True, "total": 2}})

    output = buffer.getvalue()
    assert "Plan ready" not in output
    assert "Measure wait vs work" not in output
    assert "p99 up" not in output


def test_observer_drives_load_state_phases_by_turn_stage() -> None:
    """The spinner label tracks the stage: llm_start → Thinking, tool_start →
    Invoking tools, tool_end → Executing. Never a stale label, never blank."""
    from surfaces.interactive_shell.runtime.core.state import SpinnerState
    from surfaces.shared.terminal.output.console_state import set_turn_spinner

    spinner = SpinnerState()
    spinner.start()  # initial dispatch shows Executing
    assert spinner.phase == SpinnerState.EXECUTING_PHASE
    set_turn_spinner(spinner)
    try:
        console = Console(file=io.StringIO(), force_terminal=False)
        observer = ActionRenderObserver(session=Session(), console=console, message="do it")

        observer("llm_start", {})
        assert spinner.phase == SpinnerState.THINKING_PHASE

        observer("tool_start", {"name": "shell_run", "input": {"command": "true"}})
        assert spinner.phase == SpinnerState.INVOKING_TOOLS_PHASE
        # The running action shows as a shimmering live line; for shell_run the
        # shimmer names the action only — the ``$ <cmd>`` line shows the command.
        assert spinner.active_action == "Execute"

        observer("tool_end", {"name": "shell_run"})
        assert spinner.phase == SpinnerState.EXECUTING_PHASE
        assert spinner.active_action == ""  # cleared; scrollback keeps the solid copy
    finally:
        set_turn_spinner(None)


def test_batched_tool_starts_keep_the_first_action_until_it_ends() -> None:
    """The loop emits every tool_start before any tool_end.

    The live row must keep the first still-running tool (not the last start)
    and must stay on Invoking tools until the last in-flight call ends.
    """
    from surfaces.interactive_shell.runtime.core.state import SpinnerState
    from surfaces.shared.terminal.output.console_state import set_turn_spinner

    spinner = SpinnerState()
    spinner.start()
    set_turn_spinner(spinner)
    try:
        observer, _buffer = _observer_with_buffer("do both")
        observer(
            "tool_start",
            {
                "id": "a",
                "name": "github_cli",
                "input": {"repo": "acme/app", "args": ["pr", "list"]},
            },
        )
        observer("tool_start", {"id": "b", "name": "shell_run", "input": {"command": "true"}})
        assert "GitHub CLI" in spinner.active_action
        assert "true" not in spinner.active_action
        assert spinner.phase == SpinnerState.INVOKING_TOOLS_PHASE

        observer("tool_end", {"id": "a", "name": "github_cli"})
        assert spinner.active_action == "Execute"  # shell_run shimmer names the action only
        assert spinner.phase == SpinnerState.INVOKING_TOOLS_PHASE

        observer("tool_end", {"id": "b", "name": "shell_run"})
        assert spinner.active_action == ""
        assert spinner.phase == SpinnerState.EXECUTING_PHASE
    finally:
        set_turn_spinner(None)


def test_untracked_tool_end_does_not_clear_a_running_action() -> None:
    """update_plan never owns the live row; its end must not wipe another tool."""
    from surfaces.interactive_shell.runtime.core.state import SpinnerState
    from surfaces.shared.terminal.output.console_state import set_turn_spinner

    spinner = SpinnerState()
    spinner.start()
    set_turn_spinner(spinner)
    try:
        observer, _buffer = _observer_with_buffer("plan while running")
        observer("tool_start", {"id": "a", "name": "shell_run", "input": {"command": "true"}})
        observer(
            "tool_end",
            {"id": "p1", "name": "update_plan", "output": {"ok": True, "total": 1}},
        )
        assert spinner.active_action == "Execute"  # shell_run still running, not wiped
        assert spinner.phase == SpinnerState.INVOKING_TOOLS_PHASE
    finally:
        set_turn_spinner(None)


def test_command_tools_suppress_the_static_action_header() -> None:
    """shell_run / cli_exec stream their own ``$ <cmd>`` + output; the observer
    must not also print an Execute/opensre header (the running action shows as
    the live shimmer, and scrollback keeps the ``$`` line)."""
    for name, key, cmd in (
        ("shell_run", "command", "echo hi"),
        ("cli_exec", "payload", "integrations list"),
    ):
        buf = io.StringIO()
        observer = ActionRenderObserver(
            session=Session(),
            console=Console(file=buf, force_terminal=False, highlight=False),
            message="do it",
        )
        observer("tool_start", {"name": name, "input": {key: cmd}})
        out = buf.getvalue()
        assert "Execute" not in out
        assert "opensre" not in out
        assert cmd not in out  # header suppressed; the $cmd line comes from the presenter
