"""prompt-toolkit control that shows the transcript tail at the current width."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import GetLinePrefixCallable, UIContent, UIControl
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType

from surfaces.interactive_shell.ui.transcript_view.store import (
    Row,
    TranscriptMark,
    TranscriptStore,
)

if TYPE_CHECKING:
    from prompt_toolkit.key_binding.key_bindings import NotImplementedOrNone

# Rows moved per wheel notch.
_WHEEL_ROWS = 3


class TranscriptControl(UIControl):
    """Transcript viewport: as tall as its rows until they fill the screen.

    Below a screenful the control asks for exactly the rows it has, so the
    composer sits directly under the last line and walks down as output
    arrives. Once the rows overflow, the view anchors to the newest line and
    older rows scroll off the top.

    Only the rows needed for the visible window are rendered, so a resize costs
    one screen of rendering however long the session is. While scrolled back,
    rows that arrive below are added to the offset so the passage holds still.
    """

    def __init__(self, store: TranscriptStore) -> None:
        self._store = store
        self._offset = 0
        self._page = 1
        self._mark: TranscriptMark | None = None
        self._lock = threading.Lock()

    @property
    def scrolled_back(self) -> bool:
        """True while older rows are shown instead of the newest output."""
        return self._offset > 0

    def is_focusable(self) -> bool:
        return False

    def create_content(self, width: int, height: int) -> UIContent:
        height = max(0, height)
        with self._lock:
            offset, anchor = self._offset, self._mark
            self._page = max(1, height - 1)
        window = self._store.window(max(1, width), height, offset, anchor)
        with self._lock:
            # A scroll that landed during the render applies on top.
            self._offset = max(0, self._offset - offset + window.offset)
            self._mark = window.mark
        visible: list[Row] = window.rows

        def get_line(index: int) -> StyleAndTextTuples:
            return list(visible[index])

        return UIContent(get_line=get_line, line_count=len(visible), show_cursor=False)

    def preferred_height(
        self,
        width: int,
        max_available_height: int,
        _wrap_lines: bool,
        _get_line_prefix: GetLinePrefixCallable | None,
    ) -> int:
        """Rows this transcript wants, capped at what the screen can give.

        Deliberately read-only: ``create_content`` owns the scroll
        reconciliation, so computing this through it would apply a concurrent
        scroll twice in one paint.
        """
        with self._lock:
            offset, anchor = self._offset, self._mark
        window = self._store.window(max(1, width), max(0, max_available_height), offset, anchor)
        return len(window.rows)

    def scroll(self, rows: int) -> None:
        """Move toward older (positive) or newer (negative) rows."""
        with self._lock:
            self._offset = max(0, self._offset + rows)

    def page_up(self) -> None:
        self.scroll(self._page)

    def page_down(self) -> None:
        self.scroll(-self._page)

    def scroll_to_bottom(self) -> None:
        with self._lock:
            self._offset = 0

    def mouse_handler(self, mouse_event: MouseEvent) -> NotImplementedOrNone:
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.scroll(_WHEEL_ROWS)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.scroll(-_WHEEL_ROWS)
            return None
        return NotImplemented


__all__ = ["TranscriptControl"]
