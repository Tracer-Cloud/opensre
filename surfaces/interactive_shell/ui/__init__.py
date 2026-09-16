from __future__ import annotations

from typing import TYPE_CHECKING, Any

from infrastructure.terminal.theme import (
    ANSI_DIM,
    ANSI_RESET,
    BG,
    BOLD_BRAND,
    DEVICE_CODE,
    DEVICE_CODE_ANSI,
    DIM,
    DIM_COUNTER_ANSI,
    ERROR,
    HIGHLIGHT,
    MARKDOWN_THEME,
    PROMPT_ACCENT_ANSI,
    PROMPT_FRAME_ANSI,
    SECONDARY,
    TEXT,
    WARNING,
)
from surfaces.interactive_shell.ui.poster import refresh_welcome_poster, repl_render_launch_poster
from surfaces.shared.terminal.banner import render_launch_banner
from surfaces.shared.terminal.components import (
    print_valid_choice_list,
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)
from surfaces.shared.terminal.components.rendering import (
    print_repl_json,
    print_repl_table,
    repl_print,
    repl_table,
)

if TYPE_CHECKING:
    from surfaces.interactive_shell.ui.streaming import (
        STREAM_LABEL_ANSWER,
        STREAM_LABEL_ASSISTANT,
        stream_to_console,
    )
    from surfaces.shared.terminal.agents.agents_view import (
        _build_agents_table,
        render_agents_table,
    )
    from surfaces.shared.terminal.tables import (
        COMMAND_OUTPUT_GUTTER_WIDTH,
        MCP_INTEGRATION_SERVICES,
        ColumnDef,
        print_command_output,
        render_integrations_table,
        render_mcp_table,
        render_models_table,
        render_table,
        render_tools_table,
        resolve_provider_models,
    )

# Heavy re-exports resolved lazily so importing ``ui`` (done on every REPL boot
# via the prompt/completion path) does not force the table + streaming stack.
_LAZY_SUBMODULE_EXPORTS: dict[str, str] = {
    "_build_agents_table": "surfaces.shared.terminal.agents",
    "render_agents_table": "surfaces.shared.terminal.agents",
    "STREAM_LABEL_ANSWER": "surfaces.interactive_shell.ui.streaming",
    "STREAM_LABEL_ASSISTANT": "surfaces.interactive_shell.ui.streaming",
    "stream_to_console": "surfaces.interactive_shell.ui.streaming",
    "COMMAND_OUTPUT_GUTTER_WIDTH": "surfaces.shared.terminal.tables",
    "MCP_INTEGRATION_SERVICES": "surfaces.shared.terminal.tables",
    "ColumnDef": "surfaces.shared.terminal.tables",
    "print_command_output": "surfaces.shared.terminal.tables",
    "render_integrations_table": "surfaces.shared.terminal.tables",
    "render_mcp_table": "surfaces.shared.terminal.tables",
    "render_models_table": "surfaces.shared.terminal.tables",
    "render_table": "surfaces.shared.terminal.tables",
    "render_tools_table": "surfaces.shared.terminal.tables",
    "resolve_provider_models": "surfaces.shared.terminal.tables",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY_SUBMODULE_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_path), name)


__all__ = [
    "ANSI_DIM",
    "ANSI_RESET",
    "BG",
    "BOLD_BRAND",
    "COMMAND_OUTPUT_GUTTER_WIDTH",
    "ColumnDef",
    "DEVICE_CODE",
    "DEVICE_CODE_ANSI",
    "DIM",
    "DIM_COUNTER_ANSI",
    "ERROR",
    "HIGHLIGHT",
    "MCP_INTEGRATION_SERVICES",
    "MARKDOWN_THEME",
    "PROMPT_ACCENT_ANSI",
    "PROMPT_FRAME_ANSI",
    "SECONDARY",
    "STREAM_LABEL_ANSWER",
    "STREAM_LABEL_ASSISTANT",
    "TEXT",
    "WARNING",
    "_build_agents_table",
    "print_valid_choice_list",
    "print_command_output",
    "print_repl_json",
    "print_repl_table",
    "render_agents_table",
    "refresh_welcome_poster",
    "repl_render_launch_poster",
    "render_launch_banner",
    "render_integrations_table",
    "render_mcp_table",
    "render_models_table",
    "render_table",
    "render_tools_table",
    "repl_choose_one",
    "repl_print",
    "repl_section_break",
    "repl_table",
    "repl_tty_interactive",
    "resolve_provider_models",
    "stream_to_console",
]
