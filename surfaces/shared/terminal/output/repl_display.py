from __future__ import annotations

import shutil
import threading
import time
from typing import Any

from rich.console import Console
from rich.text import Text

from infrastructure.terminal.theme import BRAND, DIM, SECONDARY
from surfaces.shared.terminal.components.time_format import _elapsed_hms
from surfaces.shared.terminal.output.events import ProgressEvent
from surfaces.shared.terminal.output.labels import (
    _node_label,
    _node_phase_label,
    build_progress_step_text,
)

# Timestamp + indent; keep append-only hint lines on one physical row.
_HINT_LINE_OVERHEAD = 20


def _terminal_columns() -> int:
    try:
        cols = shutil.get_terminal_size(fallback=(80, 24)).columns
    except OSError:
        cols = 80
    return max(40, cols - 1)


def _fit_hint_prefix(prefix: str, *, cols: int | None = None) -> str:
    """Truncate hint text so an append-only lap line stays on a single row."""
    budget = max(16, (cols if cols is not None else _terminal_columns()) - _HINT_LINE_OVERHEAD)
    if len(prefix) <= budget:
        return prefix
    if budget <= 3:
        return prefix[:budget]
    return f"{prefix[: budget - 3]}..."


class _ReplEventLogDisplay:
    """Append-only progress for the interactive REPL.

    Live animation is delegated to the prompt spinner (``SpinnerState``): raw
    cursor-up frames cannot rewrite a row under ``patch_stdout(raw=True)``, so
    the display prints step/lap history append-only and drives the spinner with
    per-stage phase labels via the ``console_state`` registration.
    """

    def __init__(
        self,
        model: str = "",
        mode: str = "local",
        t0: float | None = None,
        console: Console | None = None,
    ) -> None:
        self._model = model
        self._mode = mode
        self._t0 = t0 if t0 is not None else time.monotonic()
        self._active_steps: dict[str, dict[str, Any]] = {}
        self._current_phase = "LOAD"
        self._lock = threading.Lock()
        self._console = console if console is not None else Console(highlight=False)
        self._last_emitted_hint: str | None = None

    def stop(self) -> None:
        from surfaces.shared.terminal.output.console_state import (
            _capture_footer_snapshot,
            get_turn_spinner,
        )

        spinner = get_turn_spinner()
        if spinner is not None:
            spinner.stop()
        _capture_footer_snapshot(self)

    def _emit(self, line: Text | Any) -> None:
        from surfaces.shared.terminal.components.choice_menu import prepare_repl_output_line

        prepare_repl_output_line()
        self._console.print(line)

    def animate_hint(self, text: str) -> None:
        """Print one compact append-only lap-status line.

        The live "still working" cue is the prompt spinner (driven from
        ``step_start``); this only records lap hints as scrollback history and
        dedupes consecutive identical lines.
        """
        prefix = _fit_hint_prefix(text.rstrip("· \t"))
        if prefix == self._last_emitted_hint:
            return
        self._last_emitted_hint = prefix
        elapsed_total = time.monotonic() - self._t0
        t = Text()
        t.append(f"{_elapsed_hms(elapsed_total)}  ", style=SECONDARY)
        t.append("      ↳  ", style=DIM)
        t.append(prefix, style=SECONDARY)
        self._emit(t)

    def step_start(self, node_name: str) -> None:
        from surfaces.shared.terminal.output.console_state import get_turn_spinner

        spinner = get_turn_spinner()
        if spinner is not None:
            spinner.set_phase(_node_label(node_name))
        with self._lock:
            self._active_steps[node_name] = {
                "t0": time.monotonic(),
                "subtext": None,
                "subtext_until": 0.0,
            }
            self._current_phase = _node_phase_label(node_name)
        self._last_emitted_hint = None
        self._emit(
            build_progress_step_text(
                node_name=node_name,
                elapsed_total=time.monotonic() - self._t0,
                status="active",
            )
        )

    def set_tool_details(
        self,
        *,
        visible: bool,
        records: list[dict[str, Any]],
        summary: str,
        clear: bool = False,
    ) -> None:
        pass

    def step_complete(self, node_name: str, event: ProgressEvent) -> None:
        self._last_emitted_hint = None
        with self._lock:
            info = self._active_steps.pop(node_name, {})
            subtext = info.get("subtext")
        line = build_progress_step_text(
            node_name=node_name,
            elapsed_total=time.monotonic() - self._t0,
            elapsed_step_ms=event.elapsed_ms,
            status=event.status,
            message=event.message,
        )
        if subtext:
            line.append(f"  ↳ {subtext}", style=BRAND)
        self._emit(line)

    def step_subtext(self, node_name: str, text: str, duration: float = 4.0) -> None:
        if not text.strip():
            return
        with self._lock:
            if node_name in self._active_steps:
                self._active_steps[node_name]["subtext"] = text
                self._active_steps[node_name]["subtext_until"] = time.monotonic() + duration

    def print_above(self, text: str) -> None:
        if not text.strip():
            return
        from surfaces.shared.terminal.components.markdown import ReplyMarkdown

        from infrastructure.terminal.theme import MARKDOWN_THEME

        with self._console.use_theme(MARKDOWN_THEME):
            self._emit(ReplyMarkdown(text, code_theme="ansi_dark"))

    def print_above_renderable(self, renderable: Any) -> None:
        self._emit(renderable)
