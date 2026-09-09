from __future__ import annotations

import threading
import time
from typing import Any

from rich.console import Console, ConsoleOptions, RenderResult
from rich.text import Text

from infrastructure.observability.trace.redaction import format_json_preview
from infrastructure.terminal.theme import (
    BRAND,
    DIM,
    ERROR,
    GLYPH_ERROR,
    GLYPH_SUCCESS,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
)
from surfaces.shared.terminal.components.time_format import _elapsed_hms, _fmt_timing
from surfaces.shared.terminal.output.events import ProgressEvent
from surfaces.shared.terminal.output.labels import (
    BADGE_STYLES,
    _humanise_message,
    _node_event_type,
    _node_label,
    _node_phase_label,
)

_SPINNER_FRAMES = ("·  ", "·· ", "···", "·· ")
_FRAME_SECS = 0.10


class _LiveRenderable:
    """Rich renderable that rebuilds the active event-log on refresh."""

    def __init__(self, display: _EventLogDisplay) -> None:
        self._d = display

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        d = self._d
        now = time.monotonic()
        with d._lock:
            if d._tool_details_visible:
                yield from _render_tool_detail_view(d, options, now)
                return
            yield from _render_active_steps(d, options, now)


def _render_active_steps(
    display: _EventLogDisplay,
    options: ConsoleOptions,
    now: float,
) -> RenderResult:
    for node_name, info in display._active_steps.items():
        elapsed_step = now - info["t0"]
        elapsed_total = now - display._t0
        frame = _SPINNER_FRAMES[int(elapsed_step / _FRAME_SECS) % len(_SPINNER_FRAMES)]
        ev_type = _node_event_type(node_name)
        badge_label, badge_color = BADGE_STYLES.get(ev_type, BADGE_STYLES["DIAG"])
        subtext: str | None = info.get("subtext")
        if subtext and now > info.get("subtext_until", 0.0):
            subtext = None

        t = Text()
        t.append(f"{_elapsed_hms(elapsed_total)}  ", style=SECONDARY)
        t.append(frame, style=SECONDARY)
        t.append(badge_label, style=f"bold {badge_color}")
        t.append("  ·  ", style=DIM)
        t.append(_node_label(node_name), style=f"bold {TEXT}")
        if subtext:
            t.append(f"  ↳ {subtext}", style=BRAND)
        t.append(f"  {_fmt_timing(int(elapsed_step * 1000))}", style=SECONDARY)
        yield t

    yield Text("")
    yield Text("┄" * (options.max_width - 1), style=DIM)
    yield _footer(display, now - display._t0, "tab tool details  ")


def _render_tool_detail_view(
    display: _EventLogDisplay,
    options: ConsoleOptions,
    now: float,
) -> RenderResult:
    elapsed_total = now - display._t0
    heading = Text()
    heading.append(" Tool Details", style=f"bold {TEXT}")
    if display._tool_summary:
        heading.append(f"  {display._tool_summary}", style=BRAND)
    yield heading

    records = display._tool_detail_records[-6:]
    hidden_count = max(0, len(display._tool_detail_records) - len(records))
    if hidden_count:
        yield Text(f"  {hidden_count} older tool call(s) hidden", style=DIM)
    if not records:
        yield Text("  No tool calls have finished yet.", style=DIM)

    for record in records:
        yield from _tool_record_rows(record)

    yield Text("┄" * (options.max_width - 1), style=DIM)
    yield _footer(display, elapsed_total, "tab compact view  ", phase="TOOL DETAILS")


def _tool_record_rows(record: dict[str, Any]) -> RenderResult:
    elapsed = str(record.get("elapsed") or "")
    suffix = f"  {elapsed}" if elapsed else ""
    row = Text()
    row.append("  ● ", style=f"bold {HIGHLIGHT}")
    row.append(str(record.get("display") or "tool"), style=f"bold {TEXT}")
    row.append(suffix, style=SECONDARY)
    yield row

    if (tool_input := record.get("input")) not in ({}, None):
        yield Text("    Input:", style=SECONDARY)
        for line in format_json_preview(tool_input, max_chars=1200).splitlines():
            yield Text(f"      {line}", style=DIM)
    if (output := record.get("output")) not in ({}, None, ""):
        yield Text("    Output:", style=SECONDARY)
        for line in format_json_preview(output, max_chars=2200).splitlines():
            yield Text(f"      {line}", style=DIM)
    yield Text("")


def _footer(
    display: _EventLogDisplay,
    elapsed: float,
    hint: str,
    *,
    phase: str | None = None,
) -> Text:
    ft = Text()
    ft.append(" ● ", style=f"bold {HIGHLIGHT}")
    ft.append(f"{phase or display._current_phase}  ", style=f"bold {SECONDARY}")
    ft.append(f"{_elapsed_hms(elapsed)}  ", style=SECONDARY)
    if display._model:
        ft.append(f"{display._model}  ", style=SECONDARY)
    ft.append(f"{display._mode}  ", style=SECONDARY)
    ft.append(hint, style=DIM)
    ft.append("esc to cancel", style=DIM)
    return ft


class _EventLogDisplay:
    """Rich Live-backed animated event log. One instance per run."""

    def __init__(
        self,
        model: str = "",
        mode: str = "local",
        t0: float | None = None,
        console: Console | None = None,
    ) -> None:
        from rich.live import Live

        from surfaces.shared.terminal.output.console_state import (
            set_active_display,
            set_live_console,
        )

        self._model = model
        self._mode = mode
        self._t0 = t0 if t0 is not None else time.monotonic()
        self._active_steps: dict[str, dict[str, Any]] = {}
        self._current_phase = "LOAD"
        self._tool_details_visible = False
        self._tool_detail_records: list[dict[str, Any]] = []
        self._tool_summary = ""
        self._lock = threading.Lock()
        self._console = console if console is not None else Console(highlight=False)
        self._live = Live(
            _LiveRenderable(self),
            console=self._console,
            refresh_per_second=10,
            auto_refresh=True,
            transient=True,
            vertical_overflow="ellipsis",
        )
        self._live.start(refresh=True)
        set_live_console(self._console)
        set_active_display(self)

    def stop(self) -> None:
        from surfaces.shared.terminal.output.console_state import (
            _capture_footer_snapshot,
            clear_active_display,
            unregister_live_console,
        )

        _capture_footer_snapshot(self)
        if self._live.is_started:
            self._live.stop()
        unregister_live_console(self._console)
        clear_active_display(self)

    def step_start(self, node_name: str) -> None:
        with self._lock:
            self._active_steps[node_name] = {
                "t0": time.monotonic(),
                "subtext": None,
                "subtext_until": 0.0,
            }
            self._current_phase = _node_phase_label(node_name)

    def set_tool_details(
        self,
        *,
        visible: bool,
        records: list[dict[str, Any]],
        summary: str,
        clear: bool = False,
    ) -> None:
        with self._lock:
            self._tool_details_visible = visible
            self._tool_detail_records = list(records)
            self._tool_summary = summary
        if self._live.is_started:
            if clear:
                self._live.console.clear()
            self._live.refresh()

    def step_complete(self, node_name: str, event: ProgressEvent) -> None:
        elapsed_total = time.monotonic() - self._t0
        with self._lock:
            self._active_steps.pop(node_name, None)
            ev_type = _node_event_type(node_name)
            badge_label, badge_color = BADGE_STYLES.get(ev_type, BADGE_STYLES["DIAG"])
            err = event.status == "error"
            t = Text()
            t.append(f"{_elapsed_hms(elapsed_total)}  ", style=SECONDARY)
            t.append(f"{GLYPH_ERROR}  " if err else f"{GLYPH_SUCCESS}  ", style=f"bold {ERROR if err else HIGHLIGHT}")
            t.append(badge_label, style=f"bold {badge_color}")
            t.append("  ·  ", style=DIM)
            t.append(_node_label(node_name), style=f"bold {TEXT}")
            if msg := _humanise_message(event.message or ""):
                t.append(f"  {msg}", style=BRAND)
            t.append(f"  {_fmt_timing(event.elapsed_ms)}", style=SECONDARY)
        if self._live.is_started:
            self._live.console.print(t)

    def step_subtext(self, node_name: str, text: str, duration: float = 4.0) -> None:
        with self._lock:
            if node_name in self._active_steps:
                self._active_steps[node_name]["subtext"] = text
                self._active_steps[node_name]["subtext_until"] = time.monotonic() + duration

    def print_above(self, text: str) -> None:
        if not text.strip():
            return
        from infrastructure.terminal.markdown import ReplyMarkdown

        from infrastructure.terminal.theme import MARKDOWN_THEME

        with self._live.console.use_theme(MARKDOWN_THEME):
            self._live.console.print(ReplyMarkdown(text, code_theme="ansi_dark"))

    def print_above_renderable(self, renderable: Any) -> None:
        self._live.console.print(renderable)
