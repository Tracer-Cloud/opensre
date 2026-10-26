"""Reusable terminal UI primitives (menus, TTY rendering, formatting)."""

from surfaces.shared.terminal.components.choice_menu import (
    print_valid_choice_list,
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)
from surfaces.shared.terminal.components.loaders import DEFAULT_LOADER_LABEL, llm_loader
from surfaces.shared.terminal.components.rendering import (
    print_repl_json,
    print_repl_table,
    print_repl_text,
    repl_print,
    repl_table,
)
from surfaces.shared.terminal.components.time_format import (
    format_repl_duration,
    format_repl_timestamp,
)
from surfaces.shared.terminal.components.token_format import (
    _CHARS_PER_TOKEN,
    format_token_count_short,
)

__all__ = [
    "DEFAULT_LOADER_LABEL",
    "_CHARS_PER_TOKEN",
    "format_repl_duration",
    "format_repl_timestamp",
    "format_token_count_short",
    "llm_loader",
    "print_repl_json",
    "print_repl_table",
    "print_repl_text",
    "print_valid_choice_list",
    "repl_choose_one",
    "repl_print",
    "repl_section_break",
    "repl_table",
    "repl_tty_interactive",
]
