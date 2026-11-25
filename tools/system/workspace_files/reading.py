"""Read part of a text file as numbered lines, bounded by the tool-output budget."""

from __future__ import annotations

from dataclasses import dataclass

#: Lines returned when the caller names no limit; the byte budget may cut sooner.
DEFAULT_LINE_LIMIT = 2000
#: One line longer than this is cut: minified bundles and data rows, not code.
MAX_LINE_CHARS = 2000
_CUT_LINE_MARK = " …[line cut]"


@dataclass(frozen=True)
class FileExcerpt:
    """Consecutive lines of one file, numbered as they are in the file."""

    text: str
    first_line: int
    last_line: int
    total_lines: int

    @property
    def remaining_lines(self) -> int:
        """Lines after the excerpt; ``0`` when it reaches the end of the file."""
        return max(self.total_lines - self.last_line, 0)


def text_lines(text: str) -> list[str]:
    """The lines of a file as an editor, ``grep -n`` and a traceback number them.

    Only a line feed ends a line. ``str.splitlines`` also breaks at form feeds
    and Unicode separators, which shifts every later number.
    """
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


def numbered_excerpt(text: str, *, offset: int, limit: int, max_bytes: int) -> FileExcerpt:
    """Lines ``offset`` (1-based) onward: at most ``limit`` of them and ``max_bytes`` of output.

    Each line is rendered ``<number>\\t<text>``. At least one line is returned
    when the file has any at ``offset``, so a caller paging forward always
    advances.
    """
    lines = text_lines(text)
    first = max(offset, 1)
    wanted = lines[first - 1 : first - 1 + max(limit, 1)]
    rendered: list[str] = []
    used = 0
    for number, line in enumerate(wanted, first):
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + _CUT_LINE_MARK
        entry = f"{number:>6}\t{line}"
        size = len(entry.encode("utf-8")) + 1
        if rendered and used + size > max_bytes:
            break
        rendered.append(entry)
        used += size
    return FileExcerpt(
        text="\n".join(rendered),
        first_line=first,
        last_line=first + len(rendered) - 1,
        total_lines=len(lines),
    )


__all__ = [
    "DEFAULT_LINE_LIMIT",
    "MAX_LINE_CHARS",
    "FileExcerpt",
    "numbered_excerpt",
    "text_lines",
]
