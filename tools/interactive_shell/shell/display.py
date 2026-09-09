"""Terminal-friendly shell command display helpers."""

from __future__ import annotations

import re

_HEREDOC_DELIMITER_RE = re.compile(r"<<(-)?\s*(?:'([^'\n]+)'|\"([^\"\n]+)\"|([^\s\\|;&<>]+))")


def _delimiter_from_match(match: re.Match[str]) -> tuple[str, bool]:
    strip_tabs = match.group(1) == "-"
    delimiter = match.group(2) or match.group(3) or match.group(4) or ""
    return delimiter, strip_tabs


def _closing_delimiter_line(line: str, *, delimiter: str, strip_tabs: bool) -> bool:
    normalized = line.rstrip("\r\n")
    if strip_tabs:
        normalized = normalized.lstrip("\t")
    return normalized == delimiter


def summarize_shell_command(command: str, *, width: int = 96) -> str:
    """One-line command entry for quiet runs: whitespace collapsed, cut with an ellipsis."""
    one_line = " ".join(format_shell_command_for_display(command).split())
    if len(one_line) <= width:
        return one_line
    return one_line[: width - 1].rstrip() + "…"


def format_shell_command_for_display(command: str) -> str:
    """Return a compact, user-facing command string for the REPL prompt area.

    Heredoc bodies (for example the Python script in ``python3 - <<'PY'``) are
    collapsed to a single summary line so incidental agent-generated scripts do
    not flood the terminal. The full ``command`` is still executed unchanged.
    """
    lines = command.splitlines()
    if len(lines) <= 1:
        return command

    display_lines: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = _HEREDOC_DELIMITER_RE.search(line)
        if match is None:
            display_lines.append(line)
            index += 1
            continue

        delimiter, strip_tabs = _delimiter_from_match(match)
        body_start = index + 1
        close_index: int | None = None
        for candidate_index in range(body_start, len(lines)):
            if _closing_delimiter_line(
                lines[candidate_index],
                delimiter=delimiter,
                strip_tabs=strip_tabs,
            ):
                close_index = candidate_index
                break

        body_line_count = 0 if close_index is None else close_index - body_start
        if body_line_count <= 0:
            summary = f"{line} …"
        else:
            noun = "line" if body_line_count == 1 else "lines"
            summary = f"{line} … ({body_line_count} {noun})"
        display_lines.append(summary)
        index = len(lines) if close_index is None else close_index + 1

    return "\n".join(display_lines)


__all__ = ["format_shell_command_for_display"]
