"""Slash command handlers for the REPL."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from app.cli.repl.banner import render_banner
from app.cli.repl.session import ReplSession


@dataclass(frozen=True)
class SlashCommand:
    name: str
    help_text: str
    handler: Callable[[ReplSession, Console, list[str]], bool]


def _cmd_help(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    table = Table(title="Slash commands", title_style="bold cyan", show_header=False)
    table.add_column("name", style="bold")
    table.add_column("description", style="dim")
    for cmd in SLASH_COMMANDS.values():
        table.add_row(cmd.name, cmd.help_text)
    console.print(table)
    return True


def _cmd_exit(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    console.print("[dim]goodbye.[/dim]")
    return False


def _cmd_clear(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    console.clear()
    render_banner(console)
    return True


def _cmd_reset(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    session.clear()
    console.print("[dim]session state cleared.[/dim]")
    return True


def _cmd_trust(session: ReplSession, console: Console, args: list[str]) -> bool:
    if args and args[0].lower() in ("off", "false", "disable"):
        session.trust_mode = False
        console.print("[dim]trust mode off[/dim]")
    else:
        session.trust_mode = True
        console.print("[yellow]trust mode on[/yellow] — future approval prompts will be skipped")
    return True


def _cmd_status(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    table = Table(title="Session status", title_style="bold cyan", show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("interactions", str(len(session.history)))
    table.add_row("last investigation", "yes" if session.last_state else "none")
    table.add_row("trust mode", "on" if session.trust_mode else "off")
    table.add_row("provider", os.getenv("LLM_PROVIDER", "anthropic"))
    acc = session.accumulated_context
    if acc:
        table.add_row("accumulated context", ", ".join(sorted(acc.keys())))
    console.print(table)
    return True


# MCP-type services are rendered separately under `/list mcp` so the default
# `/list integrations` view stays focused on alert-source / data integrations.
_MCP_SERVICES = frozenset({"github", "openclaw"})


def _load_verified_integrations() -> list[dict[str, str]]:
    """Import lazily so an unconfigured store doesn't slow down every REPL turn."""
    from app.integrations.verify import verify_integrations

    return verify_integrations()


def _load_llm_settings() -> Any | None:
    """Best-effort LLM settings load; returns None if env is misconfigured."""
    try:
        from app.config import LLMSettings

        return LLMSettings.from_env()
    except Exception:  # noqa: BLE001 — env/config errors are expected here
        return None


def _status_style(status: str) -> str:
    return {
        "ok": "green",
        "configured": "green",
        "missing": "yellow",
        "failed": "red",
    }.get(status, "dim")


def _render_integrations_table(console: Console, results: list[dict[str, str]]) -> None:
    rows = [r for r in results if r.get("service") not in _MCP_SERVICES]
    if not rows:
        console.print("[dim]no integrations configured.  try `opensre onboard` to add one.[/dim]")
        return
    table = Table(title="Integrations", title_style="bold cyan")
    table.add_column("service", style="bold")
    table.add_column("source", style="dim")
    table.add_column("status")
    table.add_column("detail", style="dim", overflow="fold")
    for row in rows:
        status = row.get("status", "unknown")
        table.add_row(
            row.get("service", "?"),
            row.get("source", "?"),
            f"[{_status_style(status)}]{status}[/{_status_style(status)}]",
            row.get("detail", ""),
        )
    console.print(table)


def _render_mcp_table(console: Console, results: list[dict[str, str]]) -> None:
    rows = [r for r in results if r.get("service") in _MCP_SERVICES]
    if not rows:
        console.print("[dim]no MCP servers configured.[/dim]")
        return
    table = Table(title="MCP servers", title_style="bold cyan")
    table.add_column("server", style="bold")
    table.add_column("source", style="dim")
    table.add_column("status")
    table.add_column("detail", style="dim", overflow="fold")
    for row in rows:
        status = row.get("status", "unknown")
        table.add_row(
            row.get("service", "?"),
            row.get("source", "?"),
            f"[{_status_style(status)}]{status}[/{_status_style(status)}]",
            row.get("detail", ""),
        )
    console.print(table)


def _render_models_table(console: Console) -> None:
    settings = _load_llm_settings()
    if settings is None:
        console.print("[red]LLM settings unavailable[/red] — check provider env vars.")
        return
    provider = str(getattr(settings, "provider", "unknown"))
    reasoning_attr = f"{provider}_reasoning_model"
    toolcall_attr = f"{provider}_toolcall_model"
    table = Table(title="LLM connection", title_style="bold cyan", show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("provider", provider)
    table.add_row("reasoning model", str(getattr(settings, reasoning_attr, "—")))
    table.add_row("toolcall model", str(getattr(settings, toolcall_attr, "—")))
    console.print(table)


def _cmd_list(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    sub = (args[0].lower() if args else "").strip()

    if sub in ("integrations", "integration", "int"):
        _render_integrations_table(console, _load_verified_integrations())
        return True

    if sub in ("mcp", "mcps"):
        _render_mcp_table(console, _load_verified_integrations())
        return True

    if sub in ("models", "model", "llm", "llms"):
        _render_models_table(console)
        return True

    if sub and sub not in ("", "all"):
        console.print(
            f"[red]unknown list target:[/red] {escape(sub)}  "
            "(try [bold]/list integrations[/bold], [bold]/list models[/bold], "
            "or [bold]/list mcp[/bold])"
        )
        return True

    # Default: summary view — show everything compactly.
    results = _load_verified_integrations()
    _render_integrations_table(console, results)
    _render_mcp_table(console, results)
    _render_models_table(console)
    return True


SLASH_COMMANDS: dict[str, SlashCommand] = {
    "/help": SlashCommand("/help", "show available commands", _cmd_help),
    "/?": SlashCommand("/?", "shortcut for /help", _cmd_help),
    "/exit": SlashCommand("/exit", "exit the REPL", _cmd_exit),
    "/quit": SlashCommand("/quit", "alias for /exit", _cmd_exit),
    "/clear": SlashCommand("/clear", "clear the screen and re-render the banner", _cmd_clear),
    "/reset": SlashCommand("/reset", "clear session state (keeps trust mode)", _cmd_reset),
    "/trust": SlashCommand("/trust", "toggle trust mode ('/trust off' to disable)", _cmd_trust),
    "/status": SlashCommand("/status", "show session status", _cmd_status),
    "/list": SlashCommand(
        "/list",
        "list integrations, MCP servers, and the active LLM connection "
        "('/list integrations', '/list models', '/list mcp')",
        _cmd_list,
    ),
}


def dispatch_slash(command_line: str, session: ReplSession, console: Console) -> bool:
    """Dispatch a slash command line. Returns False iff the REPL should exit."""
    parts = command_line.strip().split()
    if not parts:
        return True
    name = parts[0].lower()
    args = parts[1:]
    cmd = SLASH_COMMANDS.get(name)
    if cmd is None:
        console.print(
            f"[red]unknown command:[/red] {escape(name)}  (type [bold]/help[/bold])"
        )
        return True
    return cmd.handler(session, console, args)
