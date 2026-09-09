"""Tests for prompt placeholder and prefill behavior."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pytest
from prompt_toolkit.completion import Completion
from rich.console import Console

from core.agent_harness.spi.session_goal import SessionGoal, attach_session_goal
from infrastructure.scheduling.task_types import TaskKind
from surfaces.interactive_shell.runtime.core import state as loop_state
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.input_prompt import completion as prompt_completion
from surfaces.interactive_shell.ui.input_prompt import rendering as prompt_rendering
from surfaces.interactive_shell.ui.input_prompt.completion import completion_preview_hint_ansi
from surfaces.interactive_shell.ui.input_prompt.layout import prompt_line_width
from surfaces.interactive_shell.ui.input_prompt.refresh import wire_prompt_refresh
from surfaces.interactive_shell.ui.input_prompt.rendering import (
    DEFAULT_PLACEHOLDER_TEXT,
    _prompt_counter_text,
    _prompt_message,
    _prompt_turn_number,
    composer_footer_ansi,
    render_submitted_prompt,
    resolve_idle_hint_ansi,
    resolve_prompt_placeholder,
    resolve_prompt_prefix_ansi,
)


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _placeholder_text(session: Session) -> str:
    return "".join(fragment for _style, fragment in resolve_prompt_placeholder(session))


class _RefreshFakeBuffer:
    def __init__(self) -> None:
        self.text = ""
        self.submitted = False

    def validate_and_handle(self) -> None:
        self.submitted = True


class _RefreshFakeApp:
    is_running = True

    def __init__(self) -> None:
        self.current_buffer = _RefreshFakeBuffer()

    def invalidate(self) -> None:
        pass


class _RefreshFakeLoop:
    def call_soon_threadsafe(self, fn, *args) -> None:  # type: ignore[no-untyped-def]
        fn(*args)


class TestPromptRefreshAutoSubmit:
    def test_queue_auto_command_fills_and_submits_prompt(self) -> None:
        """An agent-queued interactive command should be both prefilled and
        auto-submitted so it dispatches through the exclusive-stdin path."""
        session = Session()
        app = _RefreshFakeApp()
        wire_prompt_refresh(session, app, _RefreshFakeLoop())
        session.terminal.set_auto_command("/integrations setup sentry")
        assert app.current_buffer.text == "/integrations setup sentry"
        assert app.current_buffer.submitted is True

    def test_auto_command_defers_while_dispatch_active(self) -> None:
        """``/goal set`` must not nest validate_and_handle inside the slash turn."""
        session = Session()
        app = _RefreshFakeApp()
        wire_prompt_refresh(session, app, _RefreshFakeLoop())
        session.terminal.dispatch_active = True
        session.terminal.set_auto_command("How many Windows users in the last 7 days?")
        assert app.current_buffer.submitted is False
        assert session.terminal.pending_prompt_autosubmit is True
        assert (
            session.terminal.pending_prompt_default == "How many Windows users in the last 7 days?"
        )

    def test_plain_prefill_does_not_auto_submit(self) -> None:
        """A prefill without the auto-submit flag must wait for the user (Enter)."""
        session = Session()
        app = _RefreshFakeApp()
        wire_prompt_refresh(session, app, _RefreshFakeLoop())
        session.terminal.pending_prompt_default = "why did it fail?"
        session.terminal.notify_prompt_changed()
        assert app.current_buffer.text == "why did it fail?"
        assert app.current_buffer.submitted is False


def _render_console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False)


class TestPromptTurnCounter:
    def test_first_turn_is_numbered_one(self) -> None:
        session = Session()
        assert _prompt_turn_number(session) == 1
        assert _prompt_counter_text(session) == "[1] "

    def test_counter_advances_per_submitted_prompt(self) -> None:
        session = Session()
        console = _render_console()
        render_submitted_prompt(console, session, "hello")
        assert _prompt_turn_number(session) == 2
        assert _prompt_counter_text(session) == "[2] "
        render_submitted_prompt(console, session, "and again")
        assert _prompt_turn_number(session) == 3

    def test_user_prompt_row_has_warm_accent_on_full_width_surface(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Droid-style: orange ``▌`` lead-in and INPUT_SURFACE across the full row."""
        from infrastructure.terminal.theme import get_active_theme, reply_marker_hex

        monkeypatch.setattr(prompt_rendering, "terminal_columns", lambda: 40)
        session = Session()
        buf = io.StringIO()
        console = Console(
            file=buf,
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
            legacy_windows=False,
            no_color=False,
        )
        render_submitted_prompt(console, session, "why does it show that?")
        raw = buf.getvalue()
        # A blank row precedes the echo (between-turns gap); the plate itself is
        # the row after it.
        assert re.sub(r"\x1b\[[0-9;]*m", "", raw).startswith("\n")
        visible = re.sub(r"\x1b\[[0-9;]*m", "", raw).strip("\n")
        assert "▌" in visible
        assert "❯" not in visible
        assert "why does it show that?" in visible
        # Plate spans the live prompt width (spaces pad out the row).
        assert len(visible) == 40, repr(visible)
        assert visible.startswith("▌")
        accent = reply_marker_hex().lstrip("#")
        ar, ag, ab = (int(accent[i : i + 2], 16) for i in (0, 2, 4))
        assert f"{ar};{ag};{ab}" in raw
        surface = get_active_theme().INPUT_SURFACE.lstrip("#")
        sr, sg, sb = (int(surface[i : i + 2], 16) for i in (0, 2, 4))
        assert f"{sr};{sg};{sb}" in raw
        text = get_active_theme().TEXT.lstrip("#")
        tr, tg, tb = (int(text[i : i + 2], 16) for i in (0, 2, 4))
        assert f"{tr};{tg};{tb}" in raw

    def test_autosubmitted_goal_condition_gets_work_turn_marker(self) -> None:
        """``/goal set`` autosubmit must not look like part of the slash turn."""
        session = Session()
        console = _render_console()
        attach_session_goal(session, SessionGoal(condition="How many Windows users?"))
        session.terminal.last_input_autosubmitted = True
        render_submitted_prompt(console, session, "How many Windows users in the last 7 days?")
        out = console.file.getvalue()  # type: ignore[union-attr]
        assert "↗ /goal — work turn" in out
        assert "[1]" in out
        assert "How many Windows users" in out
        assert session.terminal.last_input_autosubmitted is False

    def test_autosubmit_without_a_goal_gets_no_work_turn_marker(self) -> None:
        """A queued picker or demo prompt is autosubmitted too; it is not /goal work."""
        # Arrange
        session = Session()
        console = _render_console()
        session.terminal.last_input_autosubmitted = True

        # Act
        render_submitted_prompt(console, session, "/demo")

        # Assert
        out = console.file.getvalue()  # type: ignore[union-attr]
        assert "/goal — work turn" not in out
        assert "/demo" in out

    def test_history_rows_do_not_advance_counter(self) -> None:
        """One request that runs many tools adds many history rows but one number.

        Regression: the counter previously derived from ``len(session.history)``,
        so a single request that executed seven shell commands jumped the next
        prompt from ``[1]`` to ``[10]``.
        """
        session = Session()
        render_submitted_prompt(_render_console(), session, "onboard me on the CI/CD fix")
        for _ in range(7):
            session.record("shell", "gh auth status")
        session.record("chat", "loaded the skill")
        session.record("cli_agent", "onboard me on the CI/CD fix")
        assert _prompt_turn_number(session) == 2
        assert _prompt_counter_text(session) == "[2] "

    def test_clear_resets_counter(self) -> None:
        """``/new`` and ``/resume`` go through ``Session.clear`` and restart at [1]."""
        session = Session()
        render_submitted_prompt(_render_console(), session, "hello")
        session.clear()
        assert _prompt_turn_number(session) == 1


class TestResolveIdleHint:
    def test_idle_hint_is_empty_no_recurring_ready_line(self) -> None:
        # The prompt shows no per-turn "Ready · …" line (it also stacked into
        # copies on terminal resize).
        session = Session()
        session.configured_integrations_known = True
        session.configured_integrations = ("datadog", "github", "grafana")
        assert resolve_idle_hint_ansi(session) == ""


class TestComposerFooter:
    def test_footer_row_is_empty_box_is_the_job_prompt(self) -> None:
        assert composer_footer_ansi() == ""
        session = Session()
        text = _placeholder_text(session)
        assert text == DEFAULT_PLACEHOLDER_TEXT
        assert "Enter send" not in text
        assert "? help" not in text


class TestPromptMessage:
    def test_uses_minimal_greater_than_prompt(self) -> None:
        assert _strip_ansi(_prompt_message(Session()).value) == " > "


class TestResolvePromptPlaceholder:
    def test_default_when_no_session_context(self) -> None:
        session = Session()
        text = _placeholder_text(session)
        assert text == "Ask about an alert"
        assert "Enter send" not in text

    def test_placeholder_prompts_to_continue_an_unfinished_plan(self) -> None:
        from core.agent_harness.task_plan.plan import parse_task_plan

        session = Session()
        plan, error = parse_task_plan(
            {
                "plan": [
                    {"step": "Discover source", "status": "completed"},
                    {"step": "Query latency", "status": "in_progress"},
                    {"step": "Verify", "status": "pending"},
                ]
            }
        )
        assert error is None and plan is not None
        session.task_plan = plan
        text = _strip_ansi(_placeholder_text(session))
        assert "continue the plan" in text
        assert DEFAULT_PLACEHOLDER_TEXT not in text

    def test_shows_trust_mode(self) -> None:
        session = Session()
        session.terminal.trust_mode = True
        text = _placeholder_text(session)
        assert "trust on" in text
        assert DEFAULT_PLACEHOLDER_TEXT not in text

    def test_shows_running_task_count(self) -> None:
        session = Session()
        task = session.task_registry.create(TaskKind.CLI_COMMAND)
        task.mark_running()
        assert "1 task running" in _placeholder_text(session)

        second = session.task_registry.create(TaskKind.CODE_AGENT)
        second.mark_running()
        assert "2 tasks running" in _placeholder_text(session)

    def test_shows_resumed_session_name(self) -> None:
        session = Session()
        session.resumed_from_name = "redis-incident"
        text = _placeholder_text(session)
        assert "resumed: redis-incident" in text

    def test_combines_multiple_state_segments(self) -> None:
        session = Session()
        session.terminal.trust_mode = True
        session.resumed_from_name = "redis-incident"
        task = session.task_registry.create(TaskKind.CLI_COMMAND)
        task.mark_running()
        text = _placeholder_text(session)
        assert "trust on" in text
        assert "1 task running" in text
        assert "resumed: redis-incident" in text
        assert " · " in text


@dataclass
class _FakeCompleteState:
    completions: list[Completion]
    current_completion: Completion | None = None


@dataclass
class _FakeBuffer:
    text: str
    complete_state: _FakeCompleteState | None = None


@dataclass
class _FakeOutput:
    columns: int = 120

    def get_size(self) -> _FakeOutput:
        return self


@dataclass
class _FakeApp:
    current_buffer: _FakeBuffer
    output: _FakeOutput


class TestCompletionPreviewHint:
    def test_returns_empty_when_no_app(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(prompt_completion, "get_app_or_none", lambda: None)
        assert completion_preview_hint_ansi() == ""

    def test_shows_full_slash_command_description(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completion = Completion(
            "/gateway",
            start_position=-1,
            display="/gateway",
            display_meta="Control the background OpenSRE gateway daemon: start…",
        )
        app = _FakeApp(
            current_buffer=_FakeBuffer(
                text="/",
                complete_state=_FakeCompleteState(
                    completions=[completion],
                    current_completion=completion,
                ),
            ),
            output=_FakeOutput(),
        )
        monkeypatch.setattr(prompt_completion, "get_app_or_none", lambda: app)

        rendered = _strip_ansi(completion_preview_hint_ansi())
        assert rendered.startswith("/gateway — ")
        assert len(rendered) > len("/gateway — " + completion.display_meta_text)
        assert "…" not in rendered

    def test_unregistered_slash_completion_uses_display_label(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        completion = Completion(
            "/plugin-cmd",
            start_position=-1,
            display="/plugin-cmd",
            display_meta="Plugin-provided slash command.",
        )
        app = _FakeApp(
            current_buffer=_FakeBuffer(
                text="/",
                complete_state=_FakeCompleteState(
                    completions=[completion],
                    current_completion=completion,
                ),
            ),
            output=_FakeOutput(),
        )
        monkeypatch.setattr(prompt_completion, "get_app_or_none", lambda: app)

        rendered = _strip_ansi(completion_preview_hint_ansi())
        assert rendered == "/plugin-cmd — Plugin-provided slash command."

    def test_shows_subcommand_label_with_parent_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        completion = Completion(
            "high",
            start_position=-1,
            display="high",
            display_meta="favor more thorough reasoning",
        )
        app = _FakeApp(
            current_buffer=_FakeBuffer(
                text="/effort ",
                complete_state=_FakeCompleteState(
                    completions=[completion],
                    current_completion=completion,
                ),
            ),
            output=_FakeOutput(),
        )
        monkeypatch.setattr(prompt_completion, "get_app_or_none", lambda: app)

        rendered = _strip_ansi(completion_preview_hint_ansi())
        assert rendered == "/effort high — favor more thorough reasoning"

    def test_falls_back_to_first_completion_when_none_selected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = Completion(
            "/plugin-cmd",
            start_position=-1,
            display="/plugin-cmd",
            display_meta="Plugin-provided slash command.",
        )
        app = _FakeApp(
            current_buffer=_FakeBuffer(
                text="/",
                complete_state=_FakeCompleteState(
                    completions=[first],
                    current_completion=None,
                ),
            ),
            output=_FakeOutput(),
        )
        monkeypatch.setattr(prompt_completion, "get_app_or_none", lambda: app)

        rendered = _strip_ansi(completion_preview_hint_ansi())
        assert rendered == "/plugin-cmd — Plugin-provided slash command."

    def test_clips_preview_to_terminal_width(self, monkeypatch: pytest.MonkeyPatch) -> None:
        long_meta = (
            "Plugin-provided slash command with a deliberately long description "
            "that must be clipped to the terminal width."
        )
        completion = Completion(
            "/plugin-cmd",
            start_position=-1,
            display="/plugin-cmd",
            display_meta=long_meta,
        )
        app = _FakeApp(
            current_buffer=_FakeBuffer(
                text="/",
                complete_state=_FakeCompleteState(
                    completions=[completion],
                    current_completion=completion,
                ),
            ),
            output=_FakeOutput(columns=40),
        )
        monkeypatch.setattr(prompt_completion, "get_app_or_none", lambda: app)

        rendered = _strip_ansi(completion_preview_hint_ansi())
        assert rendered.endswith("…")
        # One column short of the terminal width (pending-wrap guard).
        assert len(rendered) <= prompt_line_width(40)
        assert rendered.startswith("/plugin-cmd — ")


class TestResolvePromptPrefix:
    def test_prefers_inline_spinner_over_completion_preview(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            prompt_rendering,
            "completion_preview_hint_ansi",
            lambda: "preview line",
        )
        spinner = loop_state.SpinnerState()
        spinner.start()
        prefix = resolve_prompt_prefix_ansi(
            inline_spinner=spinner.inline_spinner_ansi(),
            idle_hint=spinner.idle_hint_ansi(),
        )
        assert "preview line" not in prefix
        assert "Press ESC to stop" in _strip_ansi(prefix)

    def test_prefers_completion_preview_over_idle_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            prompt_rendering,
            "completion_preview_hint_ansi",
            lambda: "preview line",
        )
        spinner = loop_state.SpinnerState()
        prefix = resolve_prompt_prefix_ansi(
            inline_spinner=spinner.inline_spinner_ansi(),
            idle_hint=spinner.idle_hint_ansi(),
        )
        assert prefix == "preview line"
        assert "/ for commands" not in prefix

    def test_idle_prompt_prefix_is_empty_when_no_preview(self) -> None:
        spinner = loop_state.SpinnerState()
        prefix = resolve_prompt_prefix_ansi(
            inline_spinner=spinner.inline_spinner_ansi(),
            idle_hint=resolve_idle_hint_ansi(Session()),
        )
        # Nothing streaming or previewing → no idle chrome above the composer.
        assert _strip_ansi(prefix) == ""


@pytest.mark.asyncio
async def test_composer_frame_preferred_height_does_not_crash() -> None:
    """dont_extend_height must be a Filter — a raw bool crashes on first redraw.

    Measures under a running app: the composer height is content-driven, so
    prompt-toolkit loads the buffer's history to size it, which needs a live
    app loop — the only context a real redraw ever has.
    """
    import asyncio

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from surfaces.interactive_shell.ui.input_prompt import build_prompt_session

    with (
        create_pipe_input() as pipe_input,
        create_app_session(input=pipe_input, output=DummyOutput()),
    ):
        prompt = build_prompt_session()
        task = asyncio.create_task(prompt.prompt_async(""))
        await asyncio.sleep(0)
        dim = prompt.layout.container.preferred_height(80, 40)
        pipe_input.send_text("\r")
        await asyncio.wait_for(task, timeout=5.0)

    assert dim.preferred >= 1


def test_composer_frame_uses_subtle_rounded_corners() -> None:
    from prompt_toolkit.layout.containers import VSplit, Window

    from surfaces.interactive_shell.ui.input_prompt import rounded_composer_frame

    frame = rounded_composer_frame(Window())
    top, _middle, bottom = frame.children

    assert isinstance(top, VSplit)
    assert isinstance(bottom, VSplit)
    assert [corner.char for corner in (top.children[0], top.children[-1])] == ["╭", "╮"]
    assert [corner.char for corner in (bottom.children[0], bottom.children[-1])] == [
        "╰",
        "╯",
    ]
