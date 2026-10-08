"""Local agent fleet management CLI commands."""

from __future__ import annotations

import json
import time

import click
from rich.console import Console
from rich.markup import escape

from infrastructure.process.runtime_flags import is_json_output
from infrastructure.terminal.theme import BOLD_BRAND, DIM, HIGHLIGHT
from surfaces.shared.terminal.components.rendering import repl_table
from tools.system.fleet_monitoring.discovery import (
    classify_command_provider,
    discover_agent_processes,
    display_command,
    process_command,
    registered_and_discovered_agents,
)
from tools.system.fleet_monitoring.registry import AgentRecord, AgentRegistry


@click.group(name="fleet")
def fleet() -> None:
    """Manage the local AI agent fleet (Claude Code, Cursor, Aider, ...)."""


def _pid_exists(pid: int) -> bool:
    try:
        from tools.system.fleet_monitoring.probe import pid_exists
    except ModuleNotFoundError as exc:
        if exc.name != "psutil":
            raise
        raise click.ClickException(
            "agent process watching requires psutil; run `uv sync` or reinstall OpenSRE"
        ) from exc
    return pid_exists(pid)


@fleet.command(name="list")
def list_agents() -> None:
    """List registered and auto-discovered local agents."""
    from surfaces.shared.terminal.agents.agents_view import render_agents_table

    agents = registered_and_discovered_agents(AgentRegistry())
    if is_json_output():
        click.echo(json.dumps([agent.to_dict() for agent in agents], indent=2))
        return
    console = Console()
    render_agents_table(console, agents)


@fleet.command(name="register")
@click.argument("pid", type=int)
@click.argument("name", required=False)
@click.option("--command", "command_override", help="Command string to store for this agent.")
def register_agent(pid: int, name: str | None, command_override: str | None) -> None:
    """Start tracking a local agent process."""
    if pid <= 0:
        raise click.BadParameter("must be a positive integer", param_hint="pid")

    agent_name = name or f"agent-{pid}"
    command = command_override or process_command(pid) or agent_name
    AgentRegistry().register(
        AgentRecord(
            name=agent_name,
            pid=pid,
            command=command,
            provider=classify_command_provider(command),
        )
    )
    click.echo(f"registered {agent_name} (pid {pid})")


@fleet.command(name="forget")
@click.argument("pid", type=int)
def forget_agent(pid: int) -> None:
    """Stop tracking a local agent process."""
    removed = AgentRegistry().forget(pid)
    if removed is None:
        click.echo(f"no registered agent for pid {pid}")
        return
    click.echo(f"forgot {removed.name} (pid {pid})")


@fleet.command(name="scan")
@click.option("--register", "should_register", is_flag=True, help="Register all discovered agents.")
@click.option("--all", "include_all", is_flag=True, help="Include noisy helper processes.")
def scan_agents(should_register: bool, include_all: bool) -> None:
    """Discover running Cursor, Claude Code, Aider, and Codex sessions."""
    console = Console(highlight=False)
    candidates = discover_agent_processes(include_all=include_all)
    if not candidates:
        console.print(f"[{DIM}]no running AI-agent sessions detected[/]")
        console.print(f"[{DIM}]use [bold]opensre fleet scan --all[/bold] to inspect helpers[/]")
        return

    table = repl_table(title="agent scan", title_style=BOLD_BRAND)
    table.add_column("pid", justify="right", style=DIM, no_wrap=True)
    table.add_column("agent", style="bold")
    table.add_column("command", overflow="ellipsis", no_wrap=True, max_width=48)

    registry = AgentRegistry()
    for candidate in candidates:
        table.add_row(
            str(candidate.pid),
            escape(candidate.name),
            escape(display_command(candidate.command)),
        )
        if should_register:
            registry.register(candidate.to_record())

    console.print(table)
    if should_register:
        console.print(f"[{HIGHLIGHT}]registered {len(candidates)} agent(s)[/]")
    elif include_all:
        console.print(
            f"[{DIM}]showing helper processes; use "
            "[bold]opensre fleet scan[/bold] for likely agent sessions only[/]"
        )
    else:
        console.print(
            f"[{DIM}]Next: run [bold]opensre fleet scan --register[/bold] "
            f"to track {len(candidates)} process(es)[/]"
        )


@fleet.command(name="watch")
@click.argument("pid", type=int)
@click.option("--interval", default=1.0, show_default=True, help="Polling interval in seconds.")
@click.option("--timeout", type=float, default=None, help="Stop watching after this many seconds.")
def watch_agent(pid: int, interval: float, timeout: float | None) -> None:
    """Watch a PID until it exits and print a notification."""
    if pid <= 0:
        raise click.BadParameter("must be a positive integer", param_hint="pid")
    if interval <= 0:
        raise click.BadParameter("must be positive", param_hint="--interval")
    if timeout is not None and timeout <= 0:
        raise click.BadParameter("must be positive", param_hint="--timeout")

    started = time.monotonic()
    if not _pid_exists(pid):
        click.echo(f"pid {pid} is not running")
        return

    click.echo(f"watching pid {pid}; press Ctrl+C to stop")
    while _pid_exists(pid):
        if timeout is not None and time.monotonic() - started >= timeout:
            raise click.ClickException(f"pid {pid} is still running after {timeout:g}s")
        time.sleep(interval)
    click.echo(f"pid {pid} exited")
