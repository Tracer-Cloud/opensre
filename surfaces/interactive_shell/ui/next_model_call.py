"""``/context``: what the next model call will carry, part by part, and how big it is."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape

from config.constants.conversation_history import HISTORY_COMPACT_AFTER_TURNS
from infrastructure.terminal.theme import BOLD_BRAND, DIM, WARNING
from surfaces.shared.terminal.components.rendering import print_repl_table, repl_table

if TYPE_CHECKING:
    from core.agent_harness.spi.accounting import PromptSize

_CAPTION = (
    "Stable, context and volatile blocks are the cached system prompt; ephemeral "
    "blocks go with your next message, after the history. The total leaves out the "
    "message you type next. Tokens are estimates."
)


def _counted(count: int, noun: str) -> str:
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


def _history_line(size: PromptSize, *, replayed: bool) -> str:
    if not replayed:
        return "earlier turns ride in the recent-conversation block (structured history is off)"
    history = size.history
    if not history.messages:
        return "no earlier turns yet"
    parts = [
        f"{_counted(history.messages, 'message')} from {_counted(history.turns, 'turn')}, "
        f"{history.tool_turns:,} with tool results"
    ]
    if history.compacted_messages:
        parts.append(
            f"after compaction folds {_counted(history.compacted_messages, 'older message')} "
            "into a summary, written then and not counted here"
        )
    elif history.summarized:
        parts.append("opens with a session summary")
    return " · ".join(parts)


def _compaction_line(size: PromptSize, *, budget_tokens: int | None, compacts_next: bool) -> str:
    if compacts_next:
        return f"[{WARNING}]older turns are summarized before the next turn[/]"
    if budget_tokens is None:
        return "by characters of history (structured history is off)"
    history = size.history
    share = round(100 * history.tokens / budget_tokens)
    return (
        f"at {budget_tokens:,} tokens or {HISTORY_COMPACT_AFTER_TURNS} turns · "
        f"now ≈{history.tokens:,} tokens ({share}%), {_counted(history.turns, 'turn')}"
    )


def render_next_model_call(
    console: Console,
    size: PromptSize,
    *,
    history_budget_tokens: int | None,
    compacts_next: bool,
) -> None:
    """Print each prompt block, the replayed history, tools, the total and the compaction budget.

    ``history_budget_tokens`` is ``None`` when earlier turns are not replayed as
    messages (structured history off), so no token budget applies.
    ``compacts_next`` is compaction's own verdict: the next turn summarizes
    older turns before it builds the call shown here.
    """
    table = repl_table(
        title="Next model call (preview, nothing sent)\n",
        title_style=BOLD_BRAND,
        caption=_CAPTION,
        caption_justify="left",
        caption_style=DIM,
    )
    table.add_column("part", no_wrap=True)
    table.add_column("tier", style=DIM, no_wrap=True)
    table.add_column("chars", justify="right", no_wrap=True)
    table.add_column("≈tokens", justify="right", no_wrap=True)
    for block in size.blocks:
        table.add_row(escape(block.id), block.tier, f"{block.chars:,}", f"{block.tokens:,}")
    history = size.history
    label = "history after compaction" if history.compacted_messages else "history"
    table.add_row(label, "", f"{history.chars:,}", f"{history.tokens:,}")
    table.add_row("[bold]total[/]", "", f"[bold]{size.chars:,}[/]", f"[bold]{size.tokens:,}[/]")
    print_repl_table(console, table)

    notes = repl_table(show_header=False)
    notes.add_column("key", style="bold", no_wrap=True)
    notes.add_column("value")
    replayed = history_budget_tokens is not None
    notes.add_row("history", _history_line(size, replayed=replayed))
    if size.tool_schema_count is not None:
        notes.add_row(
            "tools",
            f"{_counted(size.tool_schema_count, 'tool schema')}, sent with every call "
            "(not in the total)",
        )
    if not size.carries_repository_context:
        notes.add_row(
            "repository",
            "none active yet; once a message names one, or this checkout is matched to "
            "your GitHub connection, the call also names it",
        )
    notes.add_row(
        "compaction",
        _compaction_line(size, budget_tokens=history_budget_tokens, compacts_next=compacts_next),
    )
    print_repl_table(console, notes)


__all__ = ["render_next_model_call"]
