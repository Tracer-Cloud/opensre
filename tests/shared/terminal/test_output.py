from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from surfaces.shared.terminal import output
from surfaces.shared.terminal.output import (
    ProgressEvent,
    ProgressTracker,
    _fmt_timing,
    get_output_format,
    get_tracker,
    reset_tracker,
    suppress_stdin_watchers,
    toggle_active_tool_details,
)
from surfaces.shared.terminal.output import console_state as output_repl_console_state
from surfaces.shared.terminal.output import environment as output_environment
from surfaces.shared.terminal.output import repl_display as output_repl
from surfaces.shared.terminal.output import toggles as output_toggles
from surfaces.shared.terminal.output import tracker as output_tracker
from surfaces.shared.terminal.output.labels import _humanise_message

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


@pytest.fixture(autouse=True)
def _isolate_output_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a clean slate for output-format env and the tracker singleton."""
    for name in ("TRACER_OUTPUT_FORMAT", "NO_COLOR", "SLACK_WEBHOOK_URL", "TRACER_VERBOSE"):
        monkeypatch.delenv(name, raising=False)
    # The module-level ``_tracker`` is a session-scoped singleton; without resetting it
    # a tracker created in an earlier test would leak its ``_rich`` flag into later ones.
    monkeypatch.setattr(output_tracker, "_tracker", None)
    monkeypatch.setattr(output_toggles, "_stdin_watcher_suppression_depth", 0)
    monkeypatch.setattr(output_toggles, "_tool_detail_toggle_callbacks", [])


@pytest.fixture
def force_text_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the tracker to pick the plain-text rendering path."""
    monkeypatch.setenv("TRACER_OUTPUT_FORMAT", "text")


# ─────────────────────────────────────────────────────────────────────────────
# get_output_format
# ─────────────────────────────────────────────────────────────────────────────


def test_get_output_format_honours_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACER_OUTPUT_FORMAT", "json")
    assert get_output_format() == "json"


def test_get_output_format_returns_text_when_no_color_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert get_output_format() == "text"


def test_get_output_format_returns_text_when_no_color_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # NO_COLOR semantics: presence of the variable is the signal, not its value.
    monkeypatch.setenv("NO_COLOR", "")
    assert get_output_format() == "text"


def test_get_output_format_returns_text_when_slack_webhook_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/abc")
    assert get_output_format() == "text"


def test_get_output_format_returns_rich_for_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(output_environment.sys.stdout, "isatty", lambda: True, raising=False)
    assert get_output_format() == "rich"


def test_get_output_format_returns_text_when_not_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(output_environment.sys.stdout, "isatty", lambda: False, raising=False)
    assert get_output_format() == "text"


def test_get_output_format_override_wins_over_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TRACER_OUTPUT_FORMAT", "rich")
    assert get_output_format() == "rich"


# ─────────────────────────────────────────────────────────────────────────────
# _humanise_message
# ─────────────────────────────────────────────────────────────────────────────


def test_humanise_message_returns_empty_for_empty_input() -> None:
    assert _humanise_message("") == ""


def test_humanise_message_uses_registered_tool_display_names() -> None:
    message = "Planned actions: ['query_datadog_logs', 'get_sre_guidance']"

    assert _humanise_message(message) == "Datadog logs, SRE runbook"


def test_humanise_message_falls_back_for_unknown_tool_names() -> None:
    message = "Planned actions: ['my_custom_tool']"

    assert _humanise_message(message) == "my custom tool"


def test_humanise_message_drops_no_new_actions() -> None:
    assert _humanise_message("No new actions to plan") == ""


def test_humanise_message_extracts_resolved_integrations_list() -> None:
    msg = "Resolved integrations: ['datadog', 'grafana', 'pagerduty']"
    assert _humanise_message(msg) == "datadog, grafana, pagerduty"


def test_humanise_message_extracts_integrations_when_keyword_present() -> None:
    msg = "Loaded integrations from store: ['github']"
    assert _humanise_message(msg) == "github"


def test_humanise_message_returns_input_when_resolved_has_no_list() -> None:
    # Falls through to the trailing return when no '[...]' segment is present.
    msg = "resolved without list"
    assert _humanise_message(msg) == msg


def test_humanise_message_formats_validity_as_confidence() -> None:
    assert _humanise_message("validity:87%") == "confidence 87%"


def test_humanise_message_strips_datadog_prefix() -> None:
    assert _humanise_message("datadog:fetched 5 logs") == "fetched 5 logs"


def test_humanise_message_passes_through_unrecognised_messages() -> None:
    assert _humanise_message("ready") == "ready"


# ─────────────────────────────────────────────────────────────────────────────
# _fmt_timing
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("elapsed_ms", "expected"),
    [
        (0, "0ms"),
        (1, "1ms"),
        (250, "250ms"),
        (999, "999ms"),
        (1000, "1.0s"),
        (1500, "1.5s"),
        (12345, "12.3s"),
    ],
)
def test_fmt_timing(elapsed_ms: int, expected: str) -> None:
    assert _fmt_timing(elapsed_ms) == expected


# ─────────────────────────────────────────────────────────────────────────────
# ProgressTracker — text mode
# ─────────────────────────────────────────────────────────────────────────────


def test_tracker_uses_repl_append_display_when_prompt_app_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(output_tracker, "get_output_format", lambda: "rich")
    monkeypatch.setattr(output_tracker, "_repl_progress_active", lambda: True)
    tracker = ProgressTracker()
    assert isinstance(tracker._display, output_repl._ReplEventLogDisplay)
    assert tracker._toggle_watcher is None


def test_tracker_uses_repl_append_display_under_repl_safe_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(output_tracker, "get_output_format", lambda: "rich")
    from surfaces.shared.terminal.output.repl_progress import repl_safe_progress_scope

    with repl_safe_progress_scope():
        tracker = ProgressTracker()
    assert isinstance(tracker._display, output_repl._ReplEventLogDisplay)
    assert tracker._toggle_watcher is None


def test_repl_display_buffers_subtext_until_step_complete(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(output_tracker, "get_output_format", lambda: "rich")
    monkeypatch.setattr(output_tracker, "_repl_progress_active", lambda: True)
    display = output_repl._ReplEventLogDisplay()
    display.step_start("plan_actions")
    out_after_start = _strip_ansi(capsys.readouterr().out)
    assert "Planning" in out_after_start
    assert "↳" not in out_after_start

    display.step_subtext("plan_actions", "Planner: log poll")
    display.step_subtext("plan_actions", "Planner: log poll x2")
    assert "↳" not in _strip_ansi(capsys.readouterr().out)

    display.step_complete(
        "plan_actions",
        output.ProgressEvent(node_name="plan_actions", elapsed_ms=1200, status="completed"),
    )
    out = _strip_ansi(capsys.readouterr().out)
    assert out.count("↳") == 1
    assert "Planner: log poll x2" in out
    assert "Planner: log poll\n" not in out


@pytest.mark.asyncio
async def test_repl_safe_progress_scope_propagates_to_asyncio_thread() -> None:
    from surfaces.shared.terminal.output.repl_progress import repl_safe_progress_scope

    with repl_safe_progress_scope():
        assert await asyncio.to_thread(output_environment._repl_progress_active) is True


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_start_prints_node_label_in_text_mode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    tracker = ProgressTracker()
    tracker.start("resolve_integrations")

    out = _strip_ansi(capsys.readouterr().out)
    assert "Loading integrations" in out
    assert "…" in out


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_start_records_event_and_uses_fallback_label(
    capsys: pytest.CaptureFixture[str],
) -> None:
    tracker = ProgressTracker()
    tracker.start("custom_node", message="loading")

    out = _strip_ansi(capsys.readouterr().out)
    # Unknown node names are humanised via title-case.
    assert "Custom Node" in out
    assert tracker.events[-1].node_name == "custom_node"
    assert tracker.events[-1].status == "started"
    assert tracker.events[-1].message == "loading"


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_complete_emits_dot_label_and_timing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tracker = ProgressTracker()

    # ``_finish`` calls ``time.monotonic`` twice: once for the elapsed delta, and
    # once via ``dict.pop(node, time.monotonic())`` whose default is always evaluated
    # before ``pop`` runs — even when ``node`` is present. So we yield three values.
    clock = iter([100.0, 100.5, 100.5])
    monkeypatch.setattr(output_tracker.time, "monotonic", lambda: next(clock))

    tracker.start("plan_actions")
    tracker.complete("plan_actions", message="No new actions to plan")

    out = _strip_ansi(capsys.readouterr().out)
    lines = [line for line in out.splitlines() if line.strip()]

    assert lines[-1].strip().startswith("●")
    assert "Planning" in lines[-1]
    assert "500ms" in lines[-1]
    assert "No new actions" not in lines[-1]


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_complete_appends_humanised_message_when_present(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tracker = ProgressTracker()

    clock = iter([0.0, 1.25, 1.25])
    monkeypatch.setattr(output_tracker.time, "monotonic", lambda: next(clock))

    tracker.start("review_findings")
    tracker.complete("review_findings", message="validity:75%")

    out = _strip_ansi(capsys.readouterr().out)
    last = [line for line in out.splitlines() if line.strip()][-1]

    assert "Review Findings" in last
    assert "1.2s" in last
    assert "confidence 75%" in last


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_complete_records_event_with_status_and_fields() -> None:
    tracker = ProgressTracker()
    tracker.start("investigate")
    tracker.complete("investigate", fields_updated=["evidence"], message="datadog:ok")

    completed = [e for e in tracker.events if e.status == "completed"]
    assert len(completed) == 1
    event = completed[0]
    assert event.node_name == "investigate"
    assert event.fields_updated == ["evidence"]
    assert event.message == "datadog:ok"
    assert event.elapsed_ms >= 0


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_error_path_uses_x_marker(capsys: pytest.CaptureFixture[str]) -> None:
    tracker = ProgressTracker()
    tracker.start("plan_actions")
    tracker.error("plan_actions", "boom")

    last = [line for line in _strip_ansi(capsys.readouterr().out).splitlines() if line.strip()][-1]
    assert "✗" in last
    assert "Planning" in last
    assert tracker.events[-1].status == "error"
    assert tracker.events[-1].message == "boom"


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_update_subtext_is_a_noop_in_text_mode() -> None:
    tracker = ProgressTracker()
    tracker.start("investigate")
    # No spinner is registered in text mode, so this must not raise.
    tracker.update_subtext("investigate", "querying logs")
    tracker.complete("investigate")


@pytest.mark.usefixtures("force_text_mode")
def test_tracker_tool_details_are_hidden_until_toggled(
    capsys: pytest.CaptureFixture[str],
) -> None:
    tracker = ProgressTracker()

    tracker.record_tool_start(
        "query_grafana_logs",
        {"service_name": "checkout-api", "grafana_api_key": "[redacted]"},
        event_key="call-1",
    )
    tracker.record_tool_end(
        "query_grafana_logs",
        {"available": True, "logs": [{"message": "boom"}]},
        event_key="call-1",
    )

    out = capsys.readouterr().out
    assert "Input:" not in out
    assert "Output:" not in out
    assert tracker.format_tool_summary() == "Grafana: Loki"

    tracker.toggle_tool_details()

    out = capsys.readouterr().out
    assert "Tool details shown" in out
    assert "Input:" in out
    assert "Output:" in out
    assert "checkout-api" in out
    assert "boom" in out
    assert "grafana_api_key" in out
    assert "secret" not in out


@pytest.mark.usefixtures("force_text_mode")
def test_rich_tracker_tool_details_toggle_replaces_live_view(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _FakeDisplay:
        def __init__(self) -> None:
            self.detail_calls: list[dict[str, Any]] = []

        def step_subtext(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def print_above_renderable(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def set_tool_details(
            self,
            *,
            visible: bool,
            records: list[dict[str, Any]],
            summary: str,
            clear: bool = False,
        ) -> None:
            self.detail_calls.append(
                {
                    "visible": visible,
                    "records": records,
                    "summary": summary,
                    "clear": clear,
                }
            )

    tracker = ProgressTracker()
    display = _FakeDisplay()
    tracker._rich = True
    tracker._display = display  # type: ignore[assignment]

    tracker.record_tool_start(
        "query_grafana_logs",
        {"service_name": "checkout-api", "grafana_api_key": "[redacted]"},
        event_key="call-1",
    )
    tracker.record_tool_end(
        "query_grafana_logs",
        {"available": True, "logs": [{"message": "boom"}]},
        event_key="call-1",
    )
    assert "Input:" not in capsys.readouterr().out

    tracker.toggle_tool_details()
    shown = display.detail_calls[-1]
    assert shown["visible"] is True
    assert shown["clear"] is True
    assert shown["summary"] == "Grafana: Loki"
    assert shown["records"][0]["output"]["logs"][0]["message"] == "boom"
    assert "Input:" not in capsys.readouterr().out

    tracker.toggle_tool_details()
    hidden = display.detail_calls[-1]
    assert hidden["visible"] is False
    assert hidden["clear"] is True


def test_tool_detail_watcher_disables_terminal_output_discard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TTY:
        def isatty(self) -> bool:
            return True

        def fileno(self) -> int:
            return 99

    class _Select:
        @staticmethod
        def select(*_args: Any, **_kwargs: Any) -> tuple[list[int], list[int], list[int]]:
            return [], [], []

    class _Termios:
        ICANON = 0x0002
        ECHO = 0x0008
        IEXTEN = 0x0400
        VMIN = 6
        VTIME = 5
        VDISCARD = 13
        TCSADRAIN = 1
        saved_attrs: list[list[Any]] = []

        @classmethod
        def tcgetattr(cls, _fd: int) -> list[Any]:
            return [0, 0, 0, cls.ICANON | cls.ECHO | cls.IEXTEN, 0, 0, [b"\x00"] * 20]

        @classmethod
        def tcsetattr(cls, _fd: int, _when: int, attrs: list[Any]) -> None:
            cls.saved_attrs.append(attrs)

    monkeypatch.setattr(output_toggles.sys, "stdin", _TTY())
    monkeypatch.setattr(output_toggles.sys, "stdout", _TTY())
    monkeypatch.setattr(output_toggles, "select", _Select)
    monkeypatch.setattr(output_toggles, "termios", _Termios)
    monkeypatch.setattr(output_toggles.os, "fpathconf", lambda _fd, _name: 0)

    watcher = output.ToolDetailToggleWatcher(lambda: None)
    watcher.start()
    watcher.stop()

    attrs = _Termios.saved_attrs[0]
    assert attrs[3] & _Termios.ICANON == 0
    assert attrs[3] & _Termios.ECHO == 0
    assert attrs[3] & _Termios.IEXTEN == 0
    assert attrs[6][_Termios.VDISCARD] == b"\x00"


def test_tool_detail_watcher_stop_restores_canonical_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TTY:
        def isatty(self) -> bool:
            return True

        def fileno(self) -> int:
            return 0

    class _Termios:
        ICANON = 0x0002
        ECHO = 0x0008
        IEXTEN = 0x0400
        VMIN = 6
        VTIME = 5
        VDISCARD = 13
        TCSADRAIN = 1
        TCIFLUSH = 0
        final_attrs: list[int] | None = None

        @classmethod
        def tcgetattr(cls, _fd: int) -> list[Any]:
            lflags = cls.ICANON | cls.ECHO | cls.IEXTEN
            if cls.final_attrs is not None:
                lflags = cls.final_attrs[3]
            return [0, 0, 0, lflags, 0, 0, [0] * 20]

        @classmethod
        def tcsetattr(cls, _fd: int, _when: int, attrs: list[Any]) -> None:
            cls.final_attrs = attrs[3]

        @classmethod
        def tcflush(cls, _fd: int, _queue: int) -> None:
            return None

    class _Select:
        @staticmethod
        def select(*_args: Any, **_kwargs: Any) -> tuple[list[int], list[int], list[int]]:
            return [], [], []

    from surfaces.shared.terminal.components import key_reader

    monkeypatch.setattr(output_toggles.sys, "stdin", _TTY())
    monkeypatch.setattr(output_toggles.sys, "stdout", _TTY())
    monkeypatch.setattr(output_toggles, "select", _Select)
    monkeypatch.setattr(output_toggles, "termios", _Termios)
    monkeypatch.setattr(output_toggles.os, "fpathconf", lambda _fd, _name: 0)
    monkeypatch.setattr(key_reader, "termios", _Termios, raising=False)
    monkeypatch.setattr(key_reader.sys, "stdin", _TTY())

    watcher = output.ToolDetailToggleWatcher(lambda: None)
    watcher.start()
    watcher.stop()

    assert _Termios.final_attrs is not None
    assert _Termios.final_attrs & _Termios.ICANON
    assert _Termios.final_attrs & _Termios.ECHO


def test_suppressed_stdin_watchers_do_not_touch_terminal_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TTY:
        def isatty(self) -> bool:
            return True

        def fileno(self) -> int:
            return 99

    class _Termios:
        @staticmethod
        def tcgetattr(_fd: int) -> list[Any]:
            raise AssertionError("suppressed watcher should not query terminal state")

    monkeypatch.setattr(output_toggles.sys, "stdin", _TTY())
    monkeypatch.setattr(output_toggles.sys, "stdout", _TTY())
    monkeypatch.setattr(output_toggles, "termios", _Termios)

    watcher = output.ToolDetailToggleWatcher(lambda: None)
    with suppress_stdin_watchers():
        watcher.start()

    assert watcher._thread is None
    assert watcher._fd is None


def test_repl_tracker_skips_per_tool_call_lines(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(output_tracker, "get_output_format", lambda: "rich")
    monkeypatch.setattr(output_tracker, "_repl_progress_active", lambda: True)
    tracker = ProgressTracker()
    tracker.start("investigation_agent")
    tracker.record_tool_start("query_datadog_logs", event_key="call-1")
    tracker.record_tool_end(
        "query_datadog_logs",
        {"logs": []},
        event_key="call-1",
    )
    out = _strip_ansi(capsys.readouterr().out)
    assert "Datadog" not in out or "Investigation" in out
    assert out.count("↳") == 0
    tracker.complete("investigation_agent")


def test_record_tool_start_sets_invoking_tools_on_turn_spinner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """While tools run, the line under the plan is Droid's 'Invoking tools…'."""
    from surfaces.interactive_shell.runtime.core.state import SpinnerState
    from surfaces.shared.terminal.output.console_state import set_turn_spinner

    monkeypatch.setattr(output_tracker, "get_output_format", lambda: "rich")
    monkeypatch.setattr(output_tracker, "_repl_progress_active", lambda: True)
    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase("Investigation")
    set_turn_spinner(spinner)
    try:
        tracker = ProgressTracker()
        tracker.start("investigation_agent")
        tracker.record_tool_start("query_datadog_logs", event_key="call-1")
        assert spinner.phase == SpinnerState.INVOKING_TOOLS_PHASE
    finally:
        set_turn_spinner(None)


class TestReplHintAnimation:
    """Tests for _ReplEventLogDisplay lap hints and prompt-spinner phase drive."""

    def test_fit_hint_prefix_truncates_for_narrow_terminals(self) -> None:
        long_prefix = (
            "Reviewing evidence (lap 2/20) after Datadog logs, "
            "Datadog events, Datadog monitors, search sentry issues"
        )
        fitted = output_repl._fit_hint_prefix(long_prefix, cols=80)
        assert fitted.endswith("...")
        assert len(fitted) <= 80 - output_repl._HINT_LINE_OVERHEAD

    def test_fit_hint_prefix_keeps_short_text(self) -> None:
        short = "Planning investigation (lap 1/20)"
        assert output_repl._fit_hint_prefix(short, cols=80) == short

    def test_animate_hint_dedupes_consecutive_identical_lines(self) -> None:
        """Lap hints print once; a repeated hint is a no-op (append-only history)."""
        display = self._make_display()
        emitted: list[str] = []
        display._emit = lambda line: emitted.append(line.plain)  # type: ignore[method-assign]
        display.animate_hint("Reviewing evidence (lap 2/20) after Datadog logs")
        display.animate_hint("Reviewing evidence (lap 2/20) after Datadog logs ·")
        assert len(emitted) == 1

    def test_step_start_drives_prompt_spinner_phase(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The display animates the prompt spinner with the active stage label."""
        from surfaces.interactive_shell.runtime.core.state import SpinnerState

        spinner = SpinnerState()
        monkeypatch.setattr(output_repl_console_state, "_turn_spinner", spinner)
        display = self._make_display()
        display._emit = lambda _line: None  # type: ignore[method-assign]

        display.step_start("plan_actions")
        assert spinner.streaming
        assert spinner.phase == "Planning"

        display.stop()
        assert not spinner.streaming
        assert spinner.phase == ""

    def _make_display(self) -> output_repl._ReplEventLogDisplay:
        return output_repl._ReplEventLogDisplay(t0=0.0)


def test_active_tool_detail_toggle_uses_newest_registered_callback() -> None:
    calls: list[str] = []
    unregister_base = output.register_tool_detail_toggle(lambda: calls.append("base"))
    unregister_top = output.register_tool_detail_toggle(lambda: calls.append("top"))

    try:
        assert toggle_active_tool_details() is True
        unregister_top()
        assert toggle_active_tool_details() is True
        unregister_base()
        assert toggle_active_tool_details() is False
    finally:
        unregister_top()
        unregister_base()

    assert calls == ["top", "base"]


# ─────────────────────────────────────────────────────────────────────────────
# Singleton tracker
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("force_text_mode")
def test_get_tracker_returns_singleton() -> None:
    a = get_tracker(reset=True)
    b = get_tracker()
    assert a is b


@pytest.mark.usefixtures("force_text_mode")
def test_reset_tracker_creates_a_fresh_instance() -> None:
    first = reset_tracker()
    second = reset_tracker()
    assert first is not second


# ─────────────────────────────────────────────────────────────────────────────
# ProgressEvent dataclass
# ─────────────────────────────────────────────────────────────────────────────


def test_progress_event_defaults() -> None:
    event = ProgressEvent(node_name="investigate", elapsed_ms=10)
    assert event.fields_updated == []
    assert event.status == "completed"
    assert event.message is None


def test_progress_event_independent_default_lists() -> None:
    a: Any = ProgressEvent(node_name="a", elapsed_ms=0)
    b: Any = ProgressEvent(node_name="b", elapsed_ms=0)
    a.fields_updated.append("x")
    assert b.fields_updated == []


# ─────────────────────────────────────────────────────────────────────────────
# _safe_print
# ─────────────────────────────────────────────────────────────────────────────


def test_safe_print_passes_utf8_strings_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    from surfaces.shared.terminal.output import _safe_print

    _safe_print("hello world")
    assert capsys.readouterr().out.strip() == "hello world"


def test_safe_print_survives_encode_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate Windows cp1252 stdout that can't encode ● (U+25CF)."""
    from io import StringIO

    from surfaces.shared.terminal.output import _safe_print

    class _NarrowWriter(StringIO):
        encoding = "ascii"

        def write(self, s: str) -> int:
            s.encode("ascii")  # raises UnicodeEncodeError for non-ASCII
            return super().write(s)

    buf = _NarrowWriter()
    monkeypatch.setattr("sys.stdout", buf)
    _safe_print("  ● investigate")  # must not raise
    assert "?" in buf.getvalue() or buf.getvalue()  # fallback ran without exception


def test_finish_text_mode_survives_non_ascii_mark(
    monkeypatch: pytest.MonkeyPatch,
    force_text_mode: None,
) -> None:
    """Regression: _finish in text mode must not raise UnicodeEncodeError for ●."""
    from io import StringIO

    from surfaces.shared.terminal.output import _safe_print

    # Verify _safe_print itself is robust; _finish delegates to it.
    class _AsciiWriter(StringIO):
        encoding = "ascii"

        def write(self, s: str) -> int:
            s.encode("ascii")
            return super().write(s)

    buf = _AsciiWriter()
    monkeypatch.setattr("sys.stdout", buf)
    _safe_print("  ● diagnose_root_cause  1.2s")  # matches the _finish output format
    assert buf.getvalue()  # something was written, no exception raised
