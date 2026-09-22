"""Slash-command, alias, and file-path completion for the REPL prompt."""

from __future__ import annotations

from collections.abc import Iterable

from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from surfaces.interactive_shell.command_registry import SLASH_COMMANDS
from surfaces.interactive_shell.command_registry.help import QUICK_ACCESS_COMMANDS
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.ui.input_prompt.layout import (
    _short_meta,
    clip_prompt_text,
    prompt_line_width,
)
from surfaces.shared.terminal.components.choice_menu import repl_tty_interactive
from surfaces.shared.terminal.prompt_layout import (
    DEFAULT_TERMINAL_COLUMNS,
    terminal_columns,
)

_COMPLETION_PREVIEW_SEP = " — "


def _slash_command_name(completion: Completion) -> str | None:
    for candidate in (completion.text, completion.display_text or ""):
        if candidate.startswith("/"):
            return candidate
    return None


def _resolve_completion_preview(
    completion: Completion,
    *,
    buffer_text: str,
) -> tuple[str, str] | None:
    cmd_name = _slash_command_name(completion)
    if cmd_name is not None:
        entry = SLASH_COMMANDS.get(cmd_name)
        if entry is not None:
            return cmd_name, entry.description

    meta = completion.display_meta_text
    if not meta:
        return None

    display = completion.display_text or completion.text
    if cmd_name is not None:
        label = display
    else:
        parts = buffer_text.split()
        label = f"{parts[0]} {display}" if parts and parts[0].startswith("/") else display
    return label, meta


def completion_preview_text(*, include_label: bool = True) -> str:
    """Full description for the highlighted completion menu item."""
    app = get_app_or_none()
    if app is None:
        return ""
    buffer = app.current_buffer
    complete_state = buffer.complete_state
    if complete_state is None or not complete_state.completions:
        return ""

    completion = complete_state.current_completion or complete_state.completions[0]
    preview = _resolve_completion_preview(completion, buffer_text=buffer.text)
    if preview is None:
        return ""

    label, description = preview
    try:
        cols = app.output.get_size().columns
    except Exception:
        cols = DEFAULT_TERMINAL_COLUMNS
    # Leave the last column empty so this context line cannot soft-wrap on
    # shrink-resize and orphan stale prompt frames (same budget as the rule).
    line = clip_prompt_text(
        f"{label}{_COMPLETION_PREVIEW_SEP}{description}" if include_label else description,
        prompt_line_width(cols),
    )
    return line


# Precomputed at import time so bare-`/` completions never rebuild it per keystroke.
_QUICK_ACCESS_SET: frozenset[str] = frozenset(QUICK_ACCESS_COMMANDS)


def _slash_completion(cmd: SlashCommand, start_position: int, *, cols: int) -> Completion:
    return Completion(
        cmd.name,
        start_position=start_position,
        display=cmd.name,
        display_meta=_short_meta(cmd.description, command_name=cmd.name, cols=cols),
    )


class ShellCompleter(Completer):
    """Tab-completion for slash commands, subcommands, and file paths."""

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,  # noqa: ARG002 — prompt_toolkit override signature
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text:
            return

        if not text.startswith("/"):
            return

        parts = text.split()
        trailing_space = text != text.rstrip(" ")
        if len(parts) == 1 and not trailing_space:
            needle = parts[0].lower()
            cols = terminal_columns()
            if needle == "/":
                # Bare `/`: show most important commands first, then the rest.
                for name in QUICK_ACCESS_COMMANDS:
                    cmd = SLASH_COMMANDS.get(name)
                    if cmd is not None:
                        yield _slash_completion(cmd, -1, cols=cols)
                for cmd in SLASH_COMMANDS.values():
                    if cmd.name not in _QUICK_ACCESS_SET:
                        yield _slash_completion(cmd, -1, cols=cols)
            else:
                for cmd in SLASH_COMMANDS.values():
                    if cmd.name.lower().startswith(needle):
                        yield _slash_completion(cmd, -len(parts[0]), cols=cols)
            return

        if len(parts) <= 2:
            cmd_name = parts[0].lower()
            raw_arg = "" if trailing_space or len(parts) < 2 else parts[1]

            if _suppress_empty_arg_completions_for_inline_picker(cmd_name, raw_arg):
                return

            entry = SLASH_COMMANDS.get(cmd_name)
            hints = entry.first_arg_completions if entry is not None else ()
            sub_prefix = raw_arg.lower()
            for sub, meta in hints:
                if sub.startswith(sub_prefix):
                    yield Completion(
                        sub,
                        start_position=-len(raw_arg),
                        display=sub,
                        display_meta=meta,
                    )


# Commands where bare invocation opens an inline picker in TTY mode.
_INLINE_PICKER_COMMANDS: frozenset[str] = frozenset(
    {
        "/history",
        "/integrations",
        "/mcp",
        "/model",
        "/trust",
        "/verbose",
    }
)


def _suppress_empty_arg_completions_for_inline_picker(cmd_name: str, raw_arg: str) -> bool:
    """Hide first-arg autocomplete when bare slash command opens inline picker."""
    return repl_tty_interactive() and not raw_arg and cmd_name in _INLINE_PICKER_COMMANDS
