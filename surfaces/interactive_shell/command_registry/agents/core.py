"""Slash command: ``/fleet`` (registered local AI agent fleet view).

Bare ``/fleet`` renders the registered-agents dashboard; subcommands
cover ``budget``, ``bus``, ``claim``, ``conflicts``, ``kill``, ``release``,
and ``trace`` (with more surfaces planned for monitor-local-agents).
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from pathlib import Path

from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.tree import Tree

from surfaces.interactive_shell.command_registry.agents.conflicts_view import render_conflicts
from surfaces.interactive_shell.command_registry.agents.kill import _cmd_agents_kill
from surfaces.interactive_shell.command_registry.agents.trace import _cmd_agents_trace
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    WARNING,
    print_repl_table,
    render_agents_table,
    repl_table,
)
from tools.system.fleet_monitoring.bus import BusMessage, subscribe
from tools.system.fleet_monitoring.config import (
    agents_config_path,
    load_agents_config,
    set_agent_budget,
)
from tools.system.fleet_monitoring.conflicts import (
    DEFAULT_WINDOW_SECONDS,
    WriteEvent,
    detect_conflicts,
)
from tools.system.fleet_monitoring.coordination import BranchClaims
from tools.system.fleet_monitoring.discovery import registered_and_discovered_agents
from tools.system.fleet_monitoring.registry import AgentRegistry

_AGENTS_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("list", "show registered local AI agents"),
    ("budget", "view or edit per-agent hourly budgets"),
    ("bus", "live-tail the cross-agent context bus"),
    ("claim", "claim a branch for an agent"),
    ("conflicts", "show file-write conflicts between local AI agents"),
    ("kill", "SIGTERM → SIGKILL a local agent by PID"),
    ("release", "release a branch claim"),
    ("trace", "live tail of an agent's stdout by pid"),
    ("graph", "render the wait-on dependency graph as a tree"),
    ("wait", "mark <pid> as waiting on another pid: /fleet wait <pid> --on <other-pid>"),
)


def _opensre_agent_id() -> str:
    return f"opensre:{os.getpid()}"


def _display_path(path: Path) -> str:
    """Replace the user's home prefix with ``~`` for cleaner CLI output."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def _print_config_error(console: Console, exc: ValidationError) -> None:
    console.print(f"[{ERROR}]agents.yaml has invalid contents:[/] {escape(str(exc))}")


def _cmd_agents_list(console: Console) -> bool:
    """Render registered plus read-only discovered agents as a Rich table.

    Bare ``/fleet`` resolves here. Explicit registry rows keep winning
    on PID collisions; process discovery fills in Cursor, Claude Code,
    Codex, Aider, and Gemini CLI sessions that the user never registered.
    """
    registry = AgentRegistry()
    render_agents_table(console, registered_and_discovered_agents(registry))
    return True


def _format_bus_message(msg: BusMessage) -> str:
    """Render one ``BusMessage`` as ``[agent] path — summary`` (path optional)."""
    parts = [f"[{HIGHLIGHT}]\\[{escape(msg.agent)}][/]"]
    if msg.path:
        parts.append(escape(msg.path))
        parts.append("—")
    parts.append(escape(msg.summary))
    return " ".join(parts)


def _cmd_agents_bus(console: Console) -> bool:
    """Live-tail the cross-agent context bus until ``Ctrl-C`` or broker exit.

    Self-elects a broker if none is running, then streams each ``BusMessage``
    as it arrives. The loop ends in three ways, each with explicit feedback:
    ``KeyboardInterrupt`` (user detached), broker disconnect (e.g. the
    publishing process exited), or socket error.
    """
    console.print(
        f"[{DIM}]tailing /fleet bus — Ctrl-C to exit[/]",
    )
    try:
        for msg in subscribe():
            console.print(_format_bus_message(msg))
    except KeyboardInterrupt:
        console.print(f"[{DIM}](detached)[/]")
        return True
    except OSError as exc:
        console.print(f"[{ERROR}]bus error:[/] {escape(str(exc))}")
        return False
    # ``subscribe()`` returned cleanly — the broker closed our connection
    # (e.g. it stopped, or its host process exited). Surface that explicitly
    # so the user isn't left wondering why the prompt came back.
    console.print(f"[{DIM}]bus broker disconnected[/]")
    return True


def _cmd_agents_conflicts(console: Console) -> bool:
    # Real write-event collection (a filesystem blast-radius watcher) is not
    # implemented yet. Until it exists, the event source is empty and
    # `/fleet conflicts` reports "no conflicts detected".
    events: list[WriteEvent] = []
    conflicts = detect_conflicts(
        events,
        window_seconds=DEFAULT_WINDOW_SECONDS,
        opensre_agent_id=_opensre_agent_id(),
    )
    console.print(render_conflicts(conflicts))
    return True


def _cmd_agents_claim(session: Session, console: Console, args: list[str]) -> bool:
    """Handle /fleet claim <branch> <agent-name>."""
    if len(args) < 2:
        console.print(f"[{ERROR}]Usage:[/] /fleet claim <branch> <agent-name>")
        session.mark_latest(ok=False, kind="slash")
        return False

    branch = args[0].strip()
    agent_name = args[1].strip()

    # Look up the PID from the registry for the given agent name
    registry = AgentRegistry()
    pid = None
    for record in registry.list():
        if record.name == agent_name:
            pid = record.pid
            break

    if pid is None:
        console.print(
            f"[{ERROR}]Agent '{escape(agent_name)}' not found in registry. "
            "Use /fleet to see registered agents."
        )
        session.mark_latest(ok=False, kind="slash")
        return False

    claims = BranchClaims()
    claim = claims.claim(branch, agent_name, pid)

    if claim is None:
        existing = claims.get(branch)
        assert existing is not None  # claim() only returns None when branch is held
        console.print(
            f"[{ERROR}]Cannot claim:[/] {escape(branch)} is already held by "
            f"{escape(existing.agent_name)} (pid {existing.pid}). "
            "Use /fleet release first."
        )
        session.mark_latest(ok=False, kind="slash")
        return False

    console.print(
        f"[{HIGHLIGHT}]Branch {escape(branch)} now held by {escape(agent_name)} (pid {pid}).[/]"
    )
    return True


def _cmd_agents_release(session: Session, console: Console, args: list[str]) -> bool:
    """Handle /fleet release <branch>."""
    if len(args) < 1:
        console.print(f"[{ERROR}]Usage:[/] /fleet release <branch>")
        session.mark_latest(ok=False, kind="slash")
        return False

    branch = args[0].strip()
    claims = BranchClaims()

    existing = claims.get(branch)
    if existing is None:
        console.print(f"[{ERROR}]{escape(branch)} is not currently held by any agent.")
        session.mark_latest(ok=False, kind="slash")
        return False

    # release() cannot return None here because we confirmed existing is not None above
    removed = claims.release(branch)
    assert removed is not None
    console.print(
        f"[{HIGHLIGHT}]Released {escape(branch)} (was held by {escape(removed.agent_name)}).[/]"
    )
    return True


def _cmd_agents_budget(session: Session, console: Console, args: list[str]) -> bool:
    """View or edit per-agent budgets stored in ``~/.opensre/agents.yaml``.

    No args -> render the current budgets as a table. Two args
    (``<agent> <usd>``) -> set ``hourly_budget_usd`` for that agent and
    persist. Anything else -> usage hint.
    """
    if not args:
        try:
            config = load_agents_config()
        except ValidationError as exc:
            _print_config_error(console, exc)
            session.mark_latest(ok=False, kind="slash")
            return True
        if not config.agents:
            console.print(
                f"[{DIM}]no per-agent budgets configured.[/]  "
                "use [bold]/fleet budget <agent> <usd>[/bold] to set one."
            )
            return True
        table = repl_table(title="agent budgets", title_style=BOLD_BRAND)
        table.add_column("agent", style="bold")
        table.add_column("hourly $", justify="right")
        table.add_column("progress min", justify="right")
        table.add_column("error %", justify="right")
        for name in sorted(config.agents):
            budget = config.agents[name]
            table.add_row(
                escape(name),
                f"${budget.hourly_budget_usd:.2f}" if budget.hourly_budget_usd is not None else "-",
                str(budget.progress_minutes) if budget.progress_minutes is not None else "-",
                f"{budget.error_rate_pct:.1f}" if budget.error_rate_pct is not None else "-",
            )
        print_repl_table(console, table)
        return True

    if len(args) != 2:
        console.print(f"[{ERROR}]usage:[/] /fleet budget [<agent> <usd>]")
        session.mark_latest(ok=False, kind="slash")
        return True

    name = args[0].strip()
    raw_usd = args[1]
    try:
        usd = float(raw_usd)
    except ValueError:
        console.print(f"[{ERROR}]invalid budget:[/] {escape(raw_usd)} is not a number")
        session.mark_latest(ok=False, kind="slash")
        return True
    # ``nan`` and ``inf`` slip past ``usd <= 0`` because both
    # ``float("nan") <= 0`` and ``float("inf") <= 0`` are ``False``.
    # Without this guard a stored ``nan`` would corrupt agents.yaml
    # (next load fails Pydantic's ``gt=0`` since ``nan > 0`` is
    # ``False``) and ``inf`` would render as ``$inf`` in the dashboard.
    if not math.isfinite(usd) or usd <= 0:
        console.print(f"[{ERROR}]invalid budget:[/] must be a positive finite number")
        session.mark_latest(ok=False, kind="slash")
        return True

    try:
        set_agent_budget(name, usd)
    except ValidationError as exc:
        _print_config_error(console, exc)
        session.mark_latest(ok=False, kind="slash")
        return True

    console.print(
        f"updated [bold]{escape(name)}[/]: ${usd:.2f}/hr -> {_display_path(agents_config_path())}"
    )
    return True


def _cmd_agents_wait(session: Session, console: Console, args: list[str]) -> bool:
    """Handle ``/fleet wait <pid> --on <other-pid>``.

    Parse the two pids out of ``args``, registers the dependency in the agent registry.
    """
    if len(args) != 3 or args[1] != "--on":
        console.print(f"[{ERROR}]usage:[/] /fleet wait <pid> --on <other-pid>")
        session.mark_latest(ok=False, kind="slash")
        return True

    try:
        pid = int(args[0])
    except ValueError:
        console.print(f"[{ERROR}]invalid pid:[/] {escape(args[0])}")
        session.mark_latest(ok=False, kind="slash")
        return True

    try:
        on_pid = int(args[2])
    except ValueError:
        console.print(f"[{ERROR}]invalid other-pid:[/] {escape(args[2])}")
        session.mark_latest(ok=False, kind="slash")
        return True

    if pid == on_pid:
        console.print(f"[{ERROR}]invalid pid:[/] {pid} waiting for itself")
        session.mark_latest(ok=False, kind="slash")
        return True

    registry = AgentRegistry()
    waiter = registry.get(pid)
    if waiter is None:
        console.print(f"[{ERROR}]pid {pid} is not in the agent registry[/]")
        session.mark_latest(ok=False, kind="slash")
        return True

    target = registry.get(on_pid)
    if target is None:
        console.print(f"[{ERROR}]pid {on_pid} is not in the agent registry[/]")
        session.mark_latest(ok=False, kind="slash")
        return True

    waiter = waiter.add_waits_on(target)
    registry.register(waiter)
    console.print(
        f"[{HIGHLIGHT}]{escape(waiter.name)} (pid {pid}) now waits on "
        f"{escape(target.name)} (pid {on_pid}).[/]"
    )
    return True


def _cmd_agents_graph(console: Console) -> bool:
    """Render the ``waits_on`` dependency graph as a Rich tree.

    Single-pass DFS over the inverse ``waits_on`` edges (depended-on
    -> waiter), building the Rich tree as it descends. A back edge — a
    pid re-encountered while still in the active path — is the
    canonical cycle witness for a directed graph; a warning naming the agents
    in the loop is emitted instead.
    """

    def _label(pid: int, ppid: int | None = None) -> str:
        r = records[pid]
        if ppid is None:
            return f"{escape(r.name)} ({pid}) \\[active]"

        pr = records[ppid]
        return f"{escape(r.name)} ({pid}) \\[waiting on {escape(pr.name)}]"

    def _walk(pid: int, parent: Tree, path: list[int], visited: set[int]) -> list[int] | None:
        for child in waiters_of.get(pid, []):
            if child in visited:
                return path[path.index(child) :] + [child]

            path.append(child)
            visited.add(child)
            node = parent.add(_label(child, pid))
            c = _walk(child, node, path, visited)
            if c is not None:
                return c

            path.pop()
            visited.remove(child)
        return None

    registry = AgentRegistry()
    records = {r.pid: r for r in registry.list()}
    if not records:
        console.print(f"[{DIM}]no registered agents[/]")
        return True

    waiters_of: dict[int, list[int]] = defaultdict(list)
    for record in records.values():
        for on_pid in record.waits_on:
            waiters_of[on_pid].append(record.pid)

    # Roots are pids that wait on nothing. If every pid waits on
    # something the graph is fully covered by a cycle — fall back to
    # all pids so the walker enters somewhere and surfaces the back
    # edge instead of silently exiting on an empty root list.
    roots = [pid for pid, r in records.items() if not r.waits_on] or list(records)

    trees: list[Tree] = []
    chain: str | None = None
    for root in roots:
        tree = Tree(label=_label(root))
        cycle = _walk(root, tree, [root], {root})
        if cycle is not None:
            chain = " -> ".join(f"{records[p].name} ({p})" for p in cycle)
            break
        trees.append(tree)

    for i, tree in enumerate(trees):
        console.print(tree)
        if i != len(trees) - 1 and chain is None:
            console.line()

    if chain is not None:
        console.print(f"[{WARNING}]: agent dependency cycle detected: {escape(chain)}.[/]")
    return True


def _cmd_agents(session: Session, console: Console, args: list[str]) -> bool:
    # The sampler is lazy: the first /fleet renders a cold snapshot and starts
    # the background sampler so CPU/token columns warm up on subsequent views.
    session.terminal.ensure_fleet_sampler_started()
    if not args or args[0].lower().strip() in {"list", "ls"}:
        return _cmd_agents_list(console)

    sub = args[0].lower().strip()
    if sub == "budget":
        return _cmd_agents_budget(session, console, args[1:])
    if sub == "bus":
        return _cmd_agents_bus(console)
    if sub == "conflicts":
        return _cmd_agents_conflicts(console)

    if sub == "claim":
        return _cmd_agents_claim(session, console, args[1:])

    if sub == "kill":
        return _cmd_agents_kill(session, console, args[1:])

    if sub == "release":
        return _cmd_agents_release(session, console, args[1:])

    if sub == "trace":
        return _cmd_agents_trace(session, console, args[1:])

    if sub == "wait":
        return _cmd_agents_wait(session, console, args[1:])

    if sub == "graph":
        return _cmd_agents_graph(console)

    console.print(
        f"[{ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/fleet[/bold], [bold]/fleet budget[/bold], "
        "[bold]/fleet bus[/bold], [bold]/fleet claim[/bold], "
        "[bold]/fleet conflicts[/bold], [bold]/fleet kill[/bold], "
        "[bold]/fleet release[/bold], [bold]/fleet trace[/bold], "
        "[bold]/fleet wait[/bold] or [bold]/fleet graph[/bold])"
    )
    session.mark_latest(ok=False, kind="slash")
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/fleet",
        "Show and manage registered local AI agents.",
        _cmd_agents,
        usage=(
            "/fleet",
            "/fleet list",
            "/fleet budget",
            "/fleet bus",
            "/fleet claim",
            "/fleet conflicts",
            "/fleet kill",
            "/fleet release",
            "/fleet trace",
            "/fleet wait",
            "/fleet graph",
        ),
        first_arg_completions=_AGENTS_FIRST_ARGS,
    ),
]
