from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, TypeGuard

from rich.text import Text

from surfaces.shared.terminal.output.environment import _safe_print

if TYPE_CHECKING:
    from surfaces.shared.terminal.output.events import DisplayProtocol
    from surfaces.shared.terminal.output.repl_display import _ReplEventLogDisplay
from surfaces.shared.terminal.components.time_format import _elapsed_hms, _fmt_timing
from surfaces.shared.terminal.output.tool_details import (
    build_tool_call_line,
    build_tool_detail_text,
    make_tool_detail_record,
    tool_detail_body,
)
from surfaces.shared.terminal.output.tool_details import (
    format_tool_summary as _format_tool_summary,
)
from surfaces.shared.terminal.output.tool_details import (
    record_tool_summary as _record_tool_summary,
)
from tools.registry import resolve_tool_display_name


def _is_repl_display(display: object) -> TypeGuard[_ReplEventLogDisplay]:
    from surfaces.shared.terminal.output.repl_display import _ReplEventLogDisplay

    return isinstance(display, _ReplEventLogDisplay)


class ToolTrackingMixin:
    _silent: bool
    _rich: bool
    _t0: float
    _display: DisplayProtocol | None
    _tool_start_times: dict[str, float]
    _tool_inputs: dict[str, Any]
    _tool_details_visible: bool
    _tool_detail_records: list[dict[str, Any]]
    _printed_tool_detail_ids: set[int]
    _tool_summary_counts: dict[str, dict[str, int]]
    _tool_summary_order: list[tuple[str, str]]

    def update_subtext(self, node_name: str, text: str, duration: float = 4.0) -> None:
        raise NotImplementedError

    def print_above_renderable(self, renderable: Any) -> None:
        raise NotImplementedError

    def record_tool_start(
        self,
        tool_name: str,
        tool_input: Any = None,
        *,
        event_key: str | None = None,
    ) -> None:
        key = event_key or tool_name
        self._tool_start_times[key] = time.monotonic()
        self._tool_inputs[key] = tool_input
        from surfaces.shared.terminal.output.console_state import get_turn_spinner

        # Match Droid: while tools run, the line under the plan reads
        # "Invoking tools…" (not a stale stage label).
        spinner = get_turn_spinner()
        if spinner is not None:
            set_phase = getattr(spinner, "set_phase", None)
            if callable(set_phase):
                set_phase("Invoking tools…")

        if self._silent:
            return
        _record_tool_summary(tool_name, self._tool_summary_counts, self._tool_summary_order)
        self._sync_tool_detail_view()

    def record_tool_end(
        self,
        tool_name: str,
        output: Any = None,
        *,
        event_key: str | None = None,
        tool_input: Any = None,
    ) -> None:
        key = event_key or tool_name
        start = self._tool_start_times.pop(key, None)
        elapsed_ms = int((time.monotonic() - start) * 1000) if start is not None else None
        stored_input = self._tool_inputs.pop(key, None)
        # UI tool tracking only; tool spans are emitted in core.execution.
        if self._silent:
            return
        self._update_tool_summary_subtext()
        self._record_tool_detail(
            resolve_tool_display_name(tool_name),
            tool_input if tool_input is not None else stored_input,
            output,
            elapsed=_fmt_timing(elapsed_ms) if elapsed_ms is not None else "",
        )
        if elapsed_ms is not None and not _is_repl_display(self._display):
            # REPL turns show an aggregate lap summary; one line per tool
            # call floods scrollback during multi-lap ReAct loops.
            self.print_above_renderable(
                build_tool_call_line(tool_name, elapsed_ms, time.monotonic() - self._t0)
            )

    def print_status_hint(self, text: str) -> None:
        if self._silent:
            return
        if _is_repl_display(self._display):
            self._display.animate_hint(text)
            return
        t = Text()
        t.append(f"{_elapsed_hms(time.monotonic() - self._t0)}  ", style="dim")
        t.append("      ↳  ", style="dim")
        t.append(text, style="dim")
        self.print_above_renderable(t)

    def toggle_tool_details(self) -> None:
        if self._silent:
            return
        self._tool_details_visible = not self._tool_details_visible
        if self._rich and self._display is not None and not _is_repl_display(self._display):
            self._sync_tool_detail_view(clear=True)
            return
        label = "shown" if self._tool_details_visible else "hidden"
        _safe_print(f"  Tool details {label} (tab)")
        if self._tool_details_visible:
            self._flush_tool_details()

    def _sync_tool_detail_view(self, *, clear: bool = False) -> None:
        if self._rich and self._display is not None and not _is_repl_display(self._display):
            self._display.set_tool_details(
                visible=self._tool_details_visible,
                records=self._tool_detail_records,
                summary=self.format_tool_summary(),
                clear=clear,
            )

    def _update_tool_summary_subtext(self) -> None:
        if summary := self.format_tool_summary():
            self.update_subtext("agent", summary, duration=30.0)

    def format_tool_summary(self) -> str:
        return _format_tool_summary(self._tool_summary_counts, self._tool_summary_order)

    def _record_tool_detail(
        self,
        display: str,
        tool_input: Any,
        output: Any,
        *,
        elapsed: str = "",
    ) -> None:
        record = make_tool_detail_record(display, tool_input, output, elapsed=elapsed)
        if record is None:
            return
        self._tool_detail_records.append(record)
        if not self._tool_details_visible:
            return
        if self._rich and self._display is not None and not _is_repl_display(self._display):
            self._sync_tool_detail_view()
        else:
            self._print_tool_detail(record)

    def _flush_tool_details(self) -> None:
        for record in self._tool_detail_records:
            if id(record) not in self._printed_tool_detail_ids:
                self._print_tool_detail(record)

    def _print_tool_detail(self, record: dict[str, Any]) -> None:
        if self._rich:
            self.print_above_renderable(build_tool_detail_text(record))
        else:
            display = str(record.get("display") or "tool")
            elapsed = str(record.get("elapsed") or "")
            suffix = f"  {elapsed}" if elapsed else ""
            _safe_print(f"  Tool details: {display}{suffix}")
            for line in tool_detail_body(record).splitlines():
                _safe_print(f"      {line}")
        self._printed_tool_detail_ids.add(id(record))
