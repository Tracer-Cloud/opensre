"""Domain-specific table renderers for the interactive shell.

Concrete renderers for integrations, models, tools, and planned-actions output.
All rendering is delegated to the REPL TTY helpers in :mod:`rendering`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from infrastructure.terminal.peek import cap_output_for_display
from infrastructure.terminal.theme import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
)
from surfaces.shared.terminal.components.rendering import (
    _prepare_tty_for_rich,
    print_repl_table,
    repl_print,
    repl_print_continue,
    repl_table,
)
from surfaces.shared.terminal.tables.provider import resolve_provider_models

if TYPE_CHECKING:
    from surfaces.shared.terminal.tables.tool_catalog import ToolCatalogEntry

# MCP-type services are also rendered under `/mcp list` for focused MCP actions.
MCP_INTEGRATION_SERVICES = frozenset({"github"})


def status_style(status: str) -> str:
    return {
        "ok": HIGHLIGHT,
        "configured": HIGHLIGHT,
        "passed": HIGHLIGHT,
        "missing": WARNING,
        "failed": ERROR,
        "error": ERROR,
    }.get(status, DIM)


# ---------------------------------------------------------------------------
# Generic table abstraction
# ---------------------------------------------------------------------------


@dataclass
class ColumnDef:
    """Declarative column spec for ``render_table``."""

    header: str
    style: str = ""
    no_wrap: bool = False
    overflow: str = "fold"
    justify: str = "left"
    flex: bool = False  # auto-sizes to fill remaining terminal width


def render_table(
    console: Console,
    title: str,
    columns: list[ColumnDef],
    rows: list[tuple[str | Text, ...]],
    *,
    title_style: str = BOLD_BRAND,
    show_lines: bool = False,
) -> None:
    """TTY-safe generic table renderer.

    Handles: TTY prep, repl_table creation, column wiring, auto-escaping
    string cells, and print_repl_table. Flex columns share remaining width
    after fixed columns claim their budget.
    """
    width = _prepare_tty_for_rich(console)
    flex_count = sum(1 for c in columns if c.flex)
    flex_width = 20
    if flex_count:
        fixed_budget = sum(14 for c in columns if not c.flex)
        flex_width = max(20, (width - fixed_budget) // flex_count)

    table = repl_table(title=f"{title}\n", title_style=title_style, show_lines=show_lines)
    for col in columns:
        col_kwargs: dict[str, Any] = {
            "no_wrap": col.no_wrap,
            "overflow": col.overflow,
            "justify": col.justify,
        }
        if col.style:
            col_kwargs["style"] = col.style
        if col.flex:
            col_kwargs["max_width"] = flex_width
        table.add_column(col.header, **col_kwargs)
    for row in rows:
        table.add_row(*(escape(v) if isinstance(v, str) else v for v in row))
    print_repl_table(console, table, width=width)


# ---------------------------------------------------------------------------
# Concrete table renderers
# ---------------------------------------------------------------------------

_INTEGRATION_COLS: list[ColumnDef] = [
    ColumnDef("service", style="bold", no_wrap=True),
    ColumnDef("source", style=DIM, no_wrap=True),
    ColumnDef("status", no_wrap=True),
    ColumnDef("detail", style=DIM, flex=True),
]

_MODEL_COLS: list[ColumnDef] = [
    ColumnDef("provider", style="bold", no_wrap=True),
    ColumnDef("reasoning model"),
    ColumnDef("toolcall model"),
]

_TOOL_COLS: list[ColumnDef] = [
    ColumnDef("tool", style="bold", no_wrap=True),
    ColumnDef("surfaces", style=DIM, no_wrap=True),
    ColumnDef("params", style=DIM),
    ColumnDef("description", flex=True),
]


def _integration_row(r: dict[str, str]) -> tuple[str | Text, ...]:
    st = r.get("status", "?")
    return (
        r.get("service", "?"),
        r.get("source", "?"),
        Text(st, style=status_style(st)),
        r.get("detail", ""),
    )


_CONNECTED_STATUSES = frozenset({"ok", "configured", "passed"})


def render_integrations_table(console: Console, results: list[dict[str, str]]) -> None:
    # Connected integrations first (so the few a user has actually set up
    # aren't buried among 50+ "missing" rows), alphabetical within each group.
    rows = sorted(
        results,
        key=lambda r: (
            r.get("status") not in _CONNECTED_STATUSES,
            r.get("service", ""),
        ),
    )
    if not rows:
        repl_print(
            console,
            f"[{DIM}]no integrations configured.  try `opensre integrations setup` to add one.[/]",
        )
        return
    render_table(console, "Integrations", _INTEGRATION_COLS, [_integration_row(r) for r in rows])


def render_mcp_table(console: Console, results: list[dict[str, str]]) -> None:
    rows = sorted(
        (r for r in results if r.get("service") in MCP_INTEGRATION_SERVICES),
        key=lambda r: r.get("service", ""),
    )
    if not rows:
        repl_print(console, f"[{DIM}]no MCP servers configured.[/]")
        return
    render_table(console, "MCP servers", _INTEGRATION_COLS, [_integration_row(r) for r in rows])


def render_models_table(
    console: Console,
    settings: Any,
    source: str = "local configuration",
) -> None:
    if settings is None:
        repl_print(console, f"[{ERROR}]LLM settings unavailable[/] — check provider env vars.")
        return
    provider = str(getattr(settings, "provider", "unknown"))
    reasoning_model, toolcall_model = resolve_provider_models(settings, provider)
    render_table(
        console,
        f"LLM connection · {source}",
        _MODEL_COLS,
        [(provider, reasoning_model, toolcall_model)],
    )


def render_tools_table(console: Console, entries: list[ToolCatalogEntry]) -> None:
    if not entries:
        repl_print(console, f"[{DIM}]no tools registered.[/]")
        return
    render_table(
        console,
        "Tools",
        _TOOL_COLS,
        [
            (
                entry.name,
                ", ".join(entry.surfaces),
                entry.input_schema_summary,
                entry.description or "-",
            )
            for entry in entries
        ],
        show_lines=True,
    )


_COMMAND_OUTPUT_INDENT = "    "  # aligns wrapped lines under the ``  ↳ `` marker

_TRACEBACK_HEADER = "Traceback (most recent call last):"
_TRACEBACK_FRAME_RE = re.compile(r'^\s*File "(?P<file>.+)", line (?P<line>\d+), in (?P<fn>.+)$')


def _collapse_traceback(text: str) -> str | None:
    """Collapse a Python traceback to ``ExcType: message`` + its innermost frame.

    Returns ``None`` when *text* is not a traceback, so ordinary output is left
    untouched. A full trace buries the two lines that matter — what failed and
    where it raised — so keep those and fold the intermediate frames.
    """
    if _TRACEBACK_HEADER not in text:
        return None
    lines = text.rstrip().split("\n")
    frames = [m for line in lines if (m := _TRACEBACK_FRAME_RE.match(line))]
    exception = next((line.strip() for line in reversed(lines) if line.strip()), "")
    if not frames or not exception:
        return None
    innermost = frames[-1]
    hidden = len(frames) - 1
    rendered = [exception, f"  at {innermost['file']}:{innermost['line']} in {innermost['fn']}"]
    if hidden:
        rendered.append(f"  ({hidden} more frame{'s' if hidden != 1 else ''} hidden)")
    return "\n".join(rendered)


def print_command_output(
    console: Console,
    output: str,
    *,
    style: str | None = None,
    on_collapse: Callable[[str], None] | None = None,
) -> None:
    if not output:
        return
    text = _collapse_traceback(output.rstrip()) or output.rstrip()
    preview, folded = cap_output_for_display(text)
    if folded is not None and on_collapse is not None:
        on_collapse(folded)
    lines = preview.split("\n")
    # Frame every result under its `$ command` header with a ``↳`` gutter so the
    # output reads as the command's child (parent → child) and stays grouped and
    # set off from the reply prose above — wide output included. A single wide
    # line wraps within the block instead of flushing the whole block (and its
    # narrow siblings) to the left margin. Long bodies collapse to a Droid-style
    # peek plus ``Ctrl+O to view``; the caller stashes *folded* for paging.
    framed = [f"  ↳ {lines[0]}", *(f"{_COMMAND_OUTPUT_INDENT}{line}" for line in lines[1:])]
    text = "\n".join(framed)
    # Parse any ANSI the captured child emitted so its Rich styling (bold, colour)
    # survives being re-printed here instead of showing as raw escape codes.
    rendered = Text.from_ansi(text) if style is None else Text.from_ansi(text, style=style)
    # Continue the current block: output must sit directly under its `$ command`
    # header, not detached from it by a blank line (which makes it read as if it
    # belonged to the next command).
    repl_print_continue(console, rendered)


__all__ = [
    "ColumnDef",
    "MCP_INTEGRATION_SERVICES",
    "print_command_output",
    "render_integrations_table",
    "render_mcp_table",
    "render_models_table",
    "render_table",
    "render_tools_table",
    "status_style",
]
