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


def _cmd_integrations(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    sub = (args[0].lower() if args else "list").strip()

    if sub in ("list", "ls"):
        _render_integrations_table(console, _load_verified_integrations())
        return True

    if sub == "verify":
        results = _load_verified_integrations()
        _render_integrations_table(console, results)
        failed = [r for r in results if r.get("status") in ("failed", "missing")]
        if failed:
            console.print(f"[yellow]{len(failed)} integration(s) need attention.[/yellow]")
        else:
            console.print("[green]all integrations ok.[/green]")
        return True

    if sub == "show":
        if len(args) < 2:
            console.print("[red]usage:[/red] /integrations show <service>")
            return True
        service = args[1].lower()
        results = _load_verified_integrations()
        match = next((r for r in results if r.get("service") == service), None)
        if match is None:
            console.print(f"[red]service not found:[/red] {escape(service)}")
            return True
        table = Table(title=f"Integration: {service}", title_style="bold cyan", show_header=False)
        table.add_column("key", style="bold")
        table.add_column("value")
        for k, v in match.items():
            table.add_row(k, str(v))
        console.print(table)
        return True

    console.print(
        f"[red]unknown subcommand:[/red] {escape(sub)}  "
        "(try [bold]/integrations list[/bold], [bold]/integrations verify[/bold], "
        "or [bold]/integrations show <service>[/bold])"
    )
    return True


def _cmd_mcp(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    sub = (args[0].lower() if args else "list").strip()

    if sub in ("list", "ls"):
        _render_mcp_table(console, _load_verified_integrations())
        return True

    if sub == "connect":
        console.print(
            "[dim]to connect an MCP server, run:[/dim] [bold]opensre integrations setup[/bold]"
        )
        return True

    if sub == "disconnect":
        console.print(
            "[dim]to remove an MCP server, run:[/dim] [bold]opensre integrations remove <service>[/bold]"
        )
        return True

    console.print(
        f"[red]unknown subcommand:[/red] {escape(sub)}  "
        "(try [bold]/mcp list[/bold], [bold]/mcp connect[/bold], or [bold]/mcp disconnect[/bold])"
    )
    return True


def _cmd_model(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    sub = (args[0].lower() if args else "show").strip()

    if sub == "show":
        _render_models_table(console)
        return True

    if sub == "set":
        console.print(
            "[yellow]model switching mid-session is not yet supported.[/yellow]\n"
            "[dim]set LLM_PROVIDER / model env vars and restart opensre.[/dim]"
        )
        return True

    console.print(
        f"[red]unknown subcommand:[/red] {escape(sub)}  "
        "(try [bold]/model show[/bold] or [bold]/model set <id>[/bold])"
    )
    return True


def _cmd_health(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    from app.cli.health_view import render_health_report
    from app.config import get_environment
    from app.integrations.store import STORE_PATH
    from app.integrations.verify import verify_integrations

    results = verify_integrations()
    environment = get_environment().value
    render_health_report(
        console=console,
        environment=environment,
        integration_store_path=STORE_PATH,
        results=results,
    )
    return True


def _cmd_doctor(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    from app.cli.commands.doctor import _CHECKS, _check

    _STATUS_STYLES: dict[str, str] = {"ok": "green", "warn": "yellow", "error": "red"}
    table = Table(title="OpenSRE Doctor", title_style="bold cyan")
    table.add_column("check", style="bold")
    table.add_column("status")
    table.add_column("detail", style="dim", overflow="fold")

    issues = 0
    for name, fn in _CHECKS:
        result = _check(name, fn)
        status = result["status"]
        style = _STATUS_STYLES.get(status, "dim")
        table.add_row(name, f"[{style}]{status}[/{style}]", result["detail"])
        if status in ("warn", "error"):
            issues += 1

    console.print(table)
    if issues:
        console.print(f"[yellow]{issues} issue(s) found.[/yellow]")
    else:
        console.print("[green]all checks passed.[/green]")
    return True


def _cmd_version(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    import platform

    from app.version import get_version

    table = Table(title="Version info", title_style="bold cyan", show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("opensre", get_version())
    table.add_row("python", platform.python_version())
    table.add_row("os", f"{platform.system().lower()} ({platform.machine()})")
    console.print(table)
    return True


def _cmd_template(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    import json

    from app.cli.alert_templates import build_alert_template
    from app.cli.constants import ALERT_TEMPLATE_CHOICES

    if not args:
        console.print(
            f"[red]usage:[/red] /template <type>  (choices: {', '.join(ALERT_TEMPLATE_CHOICES)})"
        )
        return True

    template_name = args[0].lower()
    try:
        payload = build_alert_template(template_name)
    except ValueError:
        console.print(
            f"[red]unknown template:[/red] {escape(template_name)}  "
            f"(choices: {', '.join(ALERT_TEMPLATE_CHOICES)})"
        )
        return True

    console.print_json(json.dumps(payload, indent=2))
    return True


def _cmd_investigate_file(session: ReplSession, console: Console, args: list[str]) -> bool:
    from pathlib import Path

    from app.cli.investigate import run_investigation_for_session

    if not args:
        console.print("[red]usage:[/red] /investigate <file>")
        return True

    path = Path(args[0])
    if not path.exists():
        console.print(f"[red]file not found:[/red] {escape(str(path))}")
        return True

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]cannot read file:[/red] {escape(str(exc))}")
        return True

    try:
        final_state = run_investigation_for_session(
            alert_text=text,
            context_overrides=session.accumulated_context or None,
        )
    except KeyboardInterrupt:
        console.print("[yellow]investigation cancelled.[/yellow]")
        session.record("alert", args[0], ok=False)
        return True
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]investigation failed:[/red] {escape(str(exc))}")
        session.record("alert", args[0], ok=False)
        return True

    session.last_state = final_state
    # Match `_run_new_alert` in loop.py: inherit service / cluster / region
    # across subsequent investigations in the same REPL session.  Without
    # this, follow-up free-text alerts would lose context that `/investigate`
    # just discovered (#243 requirement 7: "the session remembers everything").
    session.accumulate_from_state(final_state)
    session.record("alert", f"/investigate {args[0]}")
    return True


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


def _cmd_history(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if not session.history:
        console.print("[dim]no history yet.[/dim]")
        return True

    table = Table(title="Session history", title_style="bold cyan")
    table.add_column("#", style="dim", justify="right")
    table.add_column("type", style="bold")
    table.add_column("ok")
    table.add_column("text", overflow="fold")

    for i, entry in enumerate(session.history, start=1):
        ok_flag = entry.get("ok", True)
        ok_style = "green" if ok_flag else "red"
        ok_label = "✓" if ok_flag else "✗"
        table.add_row(
            str(i),
            entry.get("type", "?"),
            f"[{ok_style}]{ok_label}[/{ok_style}]",
            escape(str(entry.get("text", ""))),
        )
    console.print(table)
    return True


def _cmd_last(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if session.last_state is None:
        console.print("[dim]no investigation in this session yet.[/dim]")
        return True

    root_cause = session.last_state.get("root_cause", "")
    report = session.last_state.get("problem_md") or session.last_state.get("slack_message") or ""

    if root_cause:
        console.print(f"[bold cyan]root cause:[/bold cyan] {escape(str(root_cause))}")
    if report:
        console.print(escape(str(report)))
    if not root_cause and not report:
        console.print("[dim]last investigation has no report content.[/dim]")
    return True


def _cmd_save(session: ReplSession, console: Console, args: list[str]) -> bool:
    import json
    from pathlib import Path

    if session.last_state is None:
        console.print("[dim]nothing to save — run an investigation first.[/dim]")
        return True

    if not args:
        console.print("[red]usage:[/red] /save <path>  (e.g. /save report.md or /save out.json)")
        return True

    dest = Path(args[0])
    try:
        if dest.suffix.lower() == ".json":
            dest.write_text(json.dumps(session.last_state, indent=2, default=str), encoding="utf-8")
        else:
            root_cause = session.last_state.get("root_cause", "")
            report = (
                session.last_state.get("problem_md")
                or session.last_state.get("slack_message")
                or ""
            )
            lines = []
            if root_cause:
                lines.append(f"## Root Cause\n\n{root_cause}\n")
            if report:
                lines.append(f"## Report\n\n{report}\n")
            dest.write_text("\n".join(lines) or "(no report content)", encoding="utf-8")
        console.print(f"[green]saved:[/green] {escape(str(dest))}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]save failed:[/red] {escape(str(exc))}")
    return True


def _cmd_context(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if not session.accumulated_context:
        console.print("[dim]no infra context accumulated yet.[/dim]")
        return True

    table = Table(title="Accumulated context", title_style="bold cyan", show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    for k, v in sorted(session.accumulated_context.items()):
        table.add_row(k, escape(str(v)))
    console.print(table)
    return True


def _cmd_cost(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    table = Table(title="Session cost", title_style="bold cyan", show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("interactions", str(len(session.history)))

    if session.token_usage:
        inp = session.token_usage.get("input", 0)
        out = session.token_usage.get("output", 0)
        table.add_row("input tokens", f"{inp:,}")
        table.add_row("output tokens", f"{out:,}")
    else:
        table.add_row("token usage", "[dim]not available (LangSmith not wired yet)[/dim]")

    console.print(table)
    return True


def _cmd_verbose(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if args and args[0].lower() in ("off", "false", "0", "disable"):
        os.environ.pop("TRACER_VERBOSE", None)
        console.print("[dim]verbose logging off[/dim]")
    else:
        os.environ["TRACER_VERBOSE"] = "1"
        console.print("[yellow]verbose logging on[/yellow]")
    return True


def _cmd_compact(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    before = len(session.history)
    if before > 20:
        session.history = session.history[-20:]
        console.print(f"[dim]compacted: kept last 20 of {before} entries.[/dim]")
    else:
        console.print(f"[dim]nothing to compact ({before} entries, limit is 20).[/dim]")
    return True


def _cmd_stop(session: ReplSession, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    console.print("[dim]press [bold]Ctrl+C[/bold] to cancel an in-flight investigation.[/dim]")
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
    "/integrations": SlashCommand(
        "/integrations",
        "manage integrations ('/integrations list', '/integrations verify', '/integrations show <service>')",
        _cmd_integrations,
    ),
    "/mcp": SlashCommand(
        "/mcp",
        "manage MCP servers ('/mcp list', '/mcp connect', '/mcp disconnect')",
        _cmd_mcp,
    ),
    "/model": SlashCommand(
        "/model",
        "show or set the active LLM ('/model show', '/model set <id>')",
        _cmd_model,
    ),
    "/health": SlashCommand("/health", "show integration and agent health", _cmd_health),
    "/doctor": SlashCommand("/doctor", "run full environment diagnostic", _cmd_doctor),
    "/version": SlashCommand("/version", "print version, Python and OS info", _cmd_version),
    "/template": SlashCommand(
        "/template",
        "print a starter alert JSON template ('/template generic|datadog|grafana|honeycomb|coralogix')",
        _cmd_template,
    ),
    "/investigate": SlashCommand(
        "/investigate",
        "run an RCA investigation from a file ('/investigate <file>')",
        _cmd_investigate_file,
    ),
    "/history": SlashCommand("/history", "show session interaction history", _cmd_history),
    "/last": SlashCommand("/last", "reprint the most recent investigation report", _cmd_last),
    "/save": SlashCommand("/save", "save last investigation to a file ('/save <path>')", _cmd_save),
    "/context": SlashCommand("/context", "show accumulated infra context", _cmd_context),
    "/cost": SlashCommand("/cost", "show token usage and session cost", _cmd_cost),
    "/verbose": SlashCommand(
        "/verbose", "toggle verbose logging ('/verbose off' to disable)", _cmd_verbose
    ),
    "/compact": SlashCommand("/compact", "trim old session history to free memory", _cmd_compact),
    "/stop": SlashCommand(
        "/stop", "reminder: press Ctrl+C to cancel an in-flight investigation", _cmd_stop
    ),
    "/cancel": SlashCommand("/cancel", "alias for /stop", _cmd_stop),
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
        console.print(f"[red]unknown command:[/red] {escape(name)}  (type [bold]/help[/bold])")
        return True
    return cmd.handler(session, console, args)
