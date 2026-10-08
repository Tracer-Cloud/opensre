"""Width-independent transcript the full-screen shell redraws on every resize.

The shell used to print its transcript straight into terminal scrollback. Once
printed, the terminal owned those rows and re-wrapped them by its own rules on a
width change, so every resize repair was a guess about what the terminal did.
This store keeps the transcript as entries that render themselves at any width:
Rich renderables re-lay-out (wrapping, tables, panels), and captured ANSI text is
re-wrapped by Rich with its old-width padding removed.
"""

from __future__ import annotations

import functools
import io
import re
import threading
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from rich.console import Console, RenderableType
from rich.style import Style
from rich.text import Text

Fragment = tuple[str, str]
Row = tuple[Fragment, ...]

# Entries kept once they are in scrollback; unflushed entries are never dropped.
_MAX_ENTRIES = 20_000
# A drag visits many widths; remember rows for the latest few per entry.
_WIDTH_CACHE_SIZE = 3
# Rich ignores a width override on TERM=dumb/unknown unless height is set too.
_RENDER_HEIGHT = 25

_CLEAR_SCREEN = re.compile(r"\x1b\[[23]J")
# OSC strings, CSI sequences, then any other ESC-led byte (or a lone ESC).
_ESCAPE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[0-~]?")


def _keep_sgr(match: re.Match[str]) -> str:
    sequence = match.group(0)
    return sequence if sequence.startswith("\x1b[") and sequence.endswith("m") else ""


def sanitize_terminal_text(text: str) -> str:
    """Keep printable text and SGR styling; drop cursor, mode and OSC sequences."""
    text = _ESCAPE.sub(_keep_sgr, text.replace("\r\n", "\n"))
    return "".join(
        char for char in text if char in "\n\r\t\x1b" or (ord(char) >= 0x20 and char != "\x7f")
    )


def _resolve_carriage_returns(line: str) -> str:
    """Model a bare CR as an overwrite of the line so far, like a terminal row."""
    return line.rsplit("\r", 1)[-1] if "\r" in line else line


def _has_background(line: Text, offset: int) -> bool:
    style = line.style if isinstance(line.style, Style) else Style.parse(line.style or "none")
    if style.bgcolor is not None:
        return True
    for span in line.spans:
        if span.start <= offset < span.end:
            span_style = span.style if isinstance(span.style, Style) else Style.parse(span.style)
            if span_style.bgcolor is not None:
                return True
    return False


def trim_row_padding(line: Text) -> Text:
    """Drop trailing spaces a renderer added to fill its old width.

    Spaces painted with a background colour are content (a code block's bar),
    so they stay.
    """
    plain = line.plain
    content_end = len(plain.rstrip(" "))
    keep = content_end
    for offset in range(len(plain) - 1, content_end - 1, -1):
        if _has_background(line, offset):
            keep = offset + 1
            break
    return line if keep == len(plain) else line[:keep]


def ansi_renderable(text: str) -> Text:
    """Turn captured terminal text into a Rich text that re-wraps at any width."""
    lines = Text.from_ansi(text).split("\n", allow_blank=True)
    return Text("\n").join(trim_row_padding(line) for line in lines)


def render_rows(renderable: RenderableType, width: int) -> tuple[Row, ...]:
    """Render ``renderable`` at ``width`` into prompt-toolkit fragment rows."""
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        width=max(1, width),
        height=_RENDER_HEIGHT,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        legacy_windows=False,
        soft_wrap=False,
    )
    console.print(renderable, overflow="fold")
    rendered = buffer.getvalue()
    if rendered.endswith("\n"):
        rendered = rendered[:-1]
    rows: list[Row] = []
    for line in rendered.split("\n"):
        rows.append(tuple((style, chunk) for style, chunk, *_ in to_formatted_text(ANSI(line))))
    return tuple(rows)


@dataclass(frozen=True)
class TranscriptMark:
    """Where the transcript ended at one paint; anchors a scrolled-back view."""

    epoch: int
    seq: int
    pending: str


@dataclass(frozen=True)
class TranscriptWindow:
    """Rows for one paint: ``rows`` ends ``offset`` rows above the newest row."""

    rows: list[Row]
    offset: int
    mark: TranscriptMark


@dataclass(eq=False)
class TranscriptEntry:
    """One printed block; ``on_normal_screen`` once it is in terminal scrollback."""

    renderable: RenderableType
    on_normal_screen: bool = False
    seq: int = 0
    _rows: OrderedDict[int, tuple[Row, ...]] = field(default_factory=OrderedDict, repr=False)

    def rows(self, width: int) -> tuple[Row, ...]:
        cached = self._rows.get(width)
        if cached is not None:
            self._rows.move_to_end(width)
            return cached
        rows = render_rows(self.renderable, width)
        self._rows[width] = rows
        if len(self._rows) > _WIDTH_CACHE_SIZE:
            self._rows.popitem(last=False)
        return rows


class TranscriptStore:
    """Thread-safe transcript: writers append, the full-screen view reads the tail."""

    def __init__(self, *, max_entries: int = _MAX_ENTRIES) -> None:
        self._entries: deque[TranscriptEntry] = deque()
        self._max_entries = max(1, max_entries)
        self._pending = ""
        self._pending_on_normal_screen = False
        self._seq = 0
        self._epoch = 0
        self._lock = threading.RLock()
        self.on_change: Callable[[], None] | None = None

    def append_renderable(
        self, renderable: RenderableType, *, on_normal_screen: bool = False
    ) -> None:
        """Add a block that is re-rendered at the current width on every paint."""
        with self._lock:
            self._close_pending_line()
            self._append_entry(renderable, on_normal_screen=on_normal_screen)
            self._changed()

    def append_text(self, text: str, *, on_normal_screen: bool = False) -> None:
        """Add terminal output; a clear-screen sequence empties the transcript first."""
        clear_at = max((match.end() for match in _CLEAR_SCREEN.finditer(text)), default=None)
        with self._lock:
            if clear_at is not None:
                self._clear_locked()
                text = text[clear_at:]
            text = sanitize_terminal_text(text)
            if not text:
                if clear_at is not None:
                    self._changed()
                return
            combined = self._pending + text
            complete, newline, rest = combined.rpartition("\n")
            if newline:
                lines = [_resolve_carriage_returns(line) for line in complete.split("\n")]
                # A line started off-screen is only in scrollback if all of it is.
                written = on_normal_screen and (self._pending_on_normal_screen or not self._pending)
                self._append_entry(ansi_renderable("\n".join(lines)), on_normal_screen=written)
            self._pending = rest
            self._pending_on_normal_screen = on_normal_screen
            self._changed()

    def clear(self) -> None:
        """Forget every entry (``/clear``, ``/new``)."""
        with self._lock:
            self._clear_locked()
            self._changed()

    def tail_rows(self, width: int, count: int) -> tuple[list[Row], bool]:
        """Return the newest ``count`` rows at ``width`` and whether older rows exist."""
        entries, pending, _epoch, _seq = self._snapshot()
        return _collect_tail(entries, _pending_rows(pending, width), width, count)

    def window(
        self, width: int, height: int, offset: int, anchor: TranscriptMark | None
    ) -> TranscriptWindow:
        """Return rows for a ``height``-row view scrolled ``offset`` rows back.

        With an ``anchor`` from the previous paint, rows that arrived since are
        added to ``offset`` so a scrolled-back view stays on the same passage.
        A cleared transcript returns the view to the newest row.
        """
        entries, pending, epoch, seq = self._snapshot()
        pending_rows = _pending_rows(pending, width)
        mark = TranscriptMark(epoch, seq, pending)
        if offset > 0 and anchor is not None:
            if anchor.epoch != epoch:
                offset = 0
            else:
                offset = max(0, offset + _rows_since(entries, pending_rows, anchor, width))
        rows, older = _collect_tail(entries, pending_rows, width, height + offset)
        if not older:
            offset = min(offset, max(0, len(rows) - height))
        end = len(rows) - offset
        return TranscriptWindow(rows[max(0, end - height) : end], offset, mark)

    def _snapshot(self) -> tuple[list[TranscriptEntry], str, int, int]:
        with self._lock:
            return (
                list(self._entries),
                _resolve_carriage_returns(self._pending),
                self._epoch,
                self._seq,
            )

    def take_unflushed(self) -> list[TranscriptEntry]:
        """Return entries not yet in terminal scrollback, marking them as written."""
        with self._lock:
            self._close_pending_line()
            unflushed = [entry for entry in self._entries if not entry.on_normal_screen]
            for entry in unflushed:
                entry.on_normal_screen = True
            self._trim_locked()
            return unflushed

    def _close_pending_line(self) -> None:
        if self._pending:
            line = _resolve_carriage_returns(self._pending)
            self._pending = ""
            self._append_entry(
                ansi_renderable(line), on_normal_screen=self._pending_on_normal_screen
            )

    def _append_entry(self, renderable: RenderableType, *, on_normal_screen: bool) -> None:
        self._seq += 1
        self._entries.append(
            TranscriptEntry(renderable, on_normal_screen=on_normal_screen, seq=self._seq)
        )
        self._trim_locked()

    def _trim_locked(self) -> None:
        # Only drop what scrollback already holds, so a flush never loses output.
        while len(self._entries) > self._max_entries and self._entries[0].on_normal_screen:
            self._entries.popleft()

    def _clear_locked(self) -> None:
        self._entries.clear()
        self._pending = ""
        self._epoch += 1

    def _changed(self) -> None:
        callback = self.on_change
        if callback is not None:
            callback()


def _collect_tail(
    entries: list[TranscriptEntry], pending_rows: tuple[Row, ...], width: int, count: int
) -> tuple[list[Row], bool]:
    rows = list(pending_rows)
    for entry in reversed(entries):
        if len(rows) >= count:
            return rows[-count:], True
        rows[:0] = entry.rows(width)
    return rows[-count:] if len(rows) > count else rows, len(rows) > count


def _rows_since(
    entries: list[TranscriptEntry],
    pending_rows: tuple[Row, ...],
    anchor: TranscriptMark,
    width: int,
) -> int:
    """Rows added below ``anchor`` at ``width``.

    The anchor's partial line is re-rendered at ``width`` and subtracted, so it
    is counted once whether it is still pending or has since become an entry,
    and a resize alone adds nothing.
    """
    added = 0
    for entry in reversed(entries):
        if entry.seq <= anchor.seq:
            break
        added += len(entry.rows(width))
    return added + len(pending_rows) - len(_pending_rows(anchor.pending, width))


def _pending_rows(pending: str, width: int) -> tuple[Row, ...]:
    return _render_pending(pending, width) if pending else ()


# A paint needs at most two partial lines: the current one and the anchor's.
# Keeping no more bounds memory while a long line without a newline grows.
@functools.lru_cache(maxsize=2)
def _render_pending(pending: str, width: int) -> tuple[Row, ...]:
    return render_rows(ansi_renderable(pending), width)


def render_for_scrollback(entries: Iterable[TranscriptEntry], width: int) -> str:
    """Render entries as terminal text for the normal screen (CRLF line ends)."""
    return _render_terminal_text([entry.renderable for entry in entries], width)


def render_text(renderable: RenderableType, width: int) -> str:
    """Render one renderable as terminal text for the normal screen (CRLF line ends)."""
    return _render_terminal_text([renderable], width)


def _render_terminal_text(renderables: list[RenderableType], width: int) -> str:
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        width=max(1, width),
        height=_RENDER_HEIGHT,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        legacy_windows=False,
    )
    for renderable in renderables:
        console.print(renderable, overflow="fold")
    return buffer.getvalue().replace("\r\n", "\n").replace("\n", "\r\n")


__all__ = [
    "Row",
    "TranscriptEntry",
    "TranscriptMark",
    "TranscriptStore",
    "TranscriptWindow",
    "ansi_renderable",
    "render_for_scrollback",
    "render_rows",
    "render_text",
    "sanitize_terminal_text",
    "trim_row_padding",
]
