"""Slash commands for /integrations and /mcp."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

import surfaces.interactive_shell.command_registry.repl_data as repl_data
from core.agent_harness.spi.session_state import session_terminal
from surfaces.interactive_shell.command_registry.cli_parity import (
    publish_headless_slash_response,
    run_cli_command,
)
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import (
    BOLD_BRAND,
    DIM,
    ERROR,
    HIGHLIGHT,
    MCP_INTEGRATION_SERVICES,
    WARNING,
    render_integrations_table,
    render_mcp_table,
    repl_table,
)
from surfaces.shared.terminal.components.choice_menu import (
    CRUMB_SEP,
    prepare_repl_output_line,
    repl_choose_one,
    repl_section_break,
    repl_tty_interactive,
)
from surfaces.shared.terminal.components.detail_panel import repl_show_details
from surfaces.shared.terminal.components.rendering import (
    _repl_table_width,
    print_repl_table,
    repl_print,
)

_ROOT_INTEGRATIONS = "/integrations"
_ROOT_MCP = "/mcp"

_MAX_OBSERVATION_DETAIL_CHARS = 160


def _record_integrations_observation(session: Session, results: list[dict[str, str]]) -> None:
    """Stash a compact text view of verification results for agent summarization.

    Lets the agent answer questions like "is sentry installed?" by summarizing
    what ``/integrations`` actually found, instead of leaving the user with only
    a raw table. Kept plain-text and bounded so it is cheap to feed back to the
    assistant.
    """
    lines: list[str] = []
    for record in results:
        service = str(record.get("service", "")).strip()
        if not service:
            continue
        status = str(record.get("status", "")).strip() or "unknown"
        detail = str(record.get("detail", "")).strip()
        if len(detail) > _MAX_OBSERVATION_DETAIL_CHARS:
            detail = f"{detail[: _MAX_OBSERVATION_DETAIL_CHARS - 1]}…"
        line = f"- {service}: {status}"
        if detail:
            line += f" ({detail})"
        lines.append(line)
    if lines:
        session.agent.last_observation = "Integration status from `/integrations`:\n" + "\n".join(
            lines
        )


def _record_integration_show_observation(session: Session, match: dict[str, str]) -> None:
    """Stash a compact text view of a single integration's verified details."""
    lines: list[str] = []
    for key, value in match.items():
        text = str(value).strip()
        if len(text) > _MAX_OBSERVATION_DETAIL_CHARS:
            text = f"{text[: _MAX_OBSERVATION_DETAIL_CHARS - 1]}…"
        lines.append(f"- {key}: {text}")
    if lines:
        session.agent.last_observation = (
            "Integration detail from `/integrations show`:\n" + "\n".join(lines)
        )


def _configured_service_choices() -> list[tuple[str, str]]:
    """Build picker choices from configured integrations (no live verification)."""
    return [(name, name) for name in repl_data.configured_integration_names()]


def _handle_remove(
    session: Session, console: Console, service: str | None, *, mcp: bool = False
) -> bool:
    """Remove an integration with a native inline-picker confirmation (no subprocess)."""
    from infrastructure.analytics.capture import capture_integration_removed
    from integrations.registry import resolve_management_service
    from integrations.store import remove_integration
    from integrations.webapp_vault import delete_webapp_org_integration

    svc = resolve_management_service(service) if service else service
    if not svc and not repl_tty_interactive():
        repl_print(console, f"[{DIM}]usage:[/] /integrations remove <service>")
        session.mark_latest(ok=False, kind="slash")
        return True

    if repl_tty_interactive():
        pick_service = not svc
        root = _ROOT_MCP if mcp else _ROOT_INTEGRATIONS
        action = "Disconnect" if mcp else "Remove"
        initial = svc
        while True:
            if pick_service:
                choices = _mcp_service_choices() if mcp else _configured_service_choices()
                if not choices:
                    repl_show_details(
                        title=f"{root} › {action}",
                        fields=[],
                        note="No configured services to remove. Use setup/connect to add one.",
                    )
                    return True
                svc = repl_choose_one(
                    title="Choose server" if mcp else "Choose integration",
                    breadcrumb=f"{root}{CRUMB_SEP}{action}",
                    choices=choices,
                    panel=True,
                    initial_value=initial,
                )
                if svc is None:
                    return True
                initial = svc
            confirmed = repl_choose_one(
                title=f"{action} {svc}?",
                breadcrumb=f"{root}{CRUMB_SEP}{action}{CRUMB_SEP}{svc}",
                panel=True,
                initial_value="no",
                note="Removes this service's saved connection configuration.",
                choices=[("no", "Cancel"), ("yes", f"{action} {svc}")],
            )
            if confirmed == "yes":
                break
            if pick_service:
                continue
            prepare_repl_output_line()
            repl_print(console, f"[{DIM}]cancelled.[/]")
            session.refresh_integration_state()
            return True
        prepare_repl_output_line()
    else:
        import sys

        try:
            import questionary

            confirmed_bool = questionary.confirm(f"  Remove '{svc}'?", default=False).ask()
        except (EOFError, KeyboardInterrupt):
            session.refresh_integration_state()
            return True
        if not confirmed_bool:
            print("  Cancelled.", file=sys.stderr)
            session.refresh_integration_state()
            return True

    if not svc:
        return True
    if remove_integration(svc):
        delete_webapp_org_integration(svc)
        repl_print(console, f"[{HIGHLIGHT}]removed '{escape(svc)}'.[/]")
        capture_integration_removed(svc)
    else:
        repl_print(console, f"[{ERROR}]no integration found for:[/] {escape(svc)}")
        session.mark_latest(ok=False, kind="slash")
    session.refresh_integration_state()
    return True


def _mcp_service_choices() -> list[tuple[str, str]]:
    names = [
        name
        for name in repl_data.configured_integration_names()
        if name in MCP_INTEGRATION_SERVICES
    ]
    return [(name, name) for name in names]


def _print_verify_summary(
    console: Console, results: list[dict[str, str]], *, single_service: bool
) -> None:
    failed = [r for r in results if r.get("status") in ("failed", "missing")]
    if single_service:
        if not results:
            return
        service = escape(str(results[0].get("service", "?")))
        style = WARNING if failed else HIGHLIGHT
        detail = "needs attention" if failed else "ok"
        repl_print(console, f"[{style}]{service} {detail}.[/]")
        if failed:
            repl_print(
                console,
                f"[{DIM}]Reconfigure with /integrations setup {service} — "
                "the detail column above names what is missing.[/]",
            )
        return
    if failed:
        repl_print(console, f"[{WARNING}]{len(failed)} integration(s) need attention.[/]")
        repl_print(
            console,
            f"[{DIM}]Reconfigure with /integrations setup <service> — "
            "the detail column above names what is missing.[/]",
        )
    else:
        repl_print(console, f"[{HIGHLIGHT}]all integrations ok.[/]")


def _run_verify(session: Session, console: Console, service: str | None = None) -> bool:
    normalized = ""
    if service is not None:
        from integrations.registry import SUPPORTED_VERIFY_SERVICES, resolve_management_service

        normalized = resolve_management_service(service)
        if normalized not in SUPPORTED_VERIFY_SERVICES:
            repl_print(
                console,
                f"[{ERROR}]unsupported verify target:[/] {escape(normalized)}  "
                f"(try [bold]/verify[/bold] with no args to verify all)",
            )
            session.mark_latest(ok=False, kind="slash")
            return True

    prepare_repl_output_line()
    label = escape(normalized) if service is not None else "integrations"
    with console.status(f"[{DIM}]Verifying {label}…[/]", spinner="dots"):
        if service is not None:
            match = repl_data.verify_integration(normalized)
            if match is None:
                repl_print(console, f"[{ERROR}]service not found:[/] {escape(normalized)}")
                session.mark_latest(ok=False, kind="slash")
                return True
            results = [match]
        else:
            results = repl_data.load_verified_integrations()

    _record_integrations_observation(session, results)
    render_integrations_table(console, results)
    _print_verify_summary(console, results, single_service=service is not None)
    return True


def _cmd_verify(session: Session, console: Console, args: list[str]) -> bool:
    return _cmd_integrations(session, console, ["verify", *args])


def _render_integration_show(
    session: Session, console: Console, service: str, *, interactive: bool = False
) -> bool:
    """Verify and print one integration. Returns False when the service is unknown."""
    from integrations.registry import resolve_management_service

    normalized = resolve_management_service(service)
    configured = set(repl_data.configured_integration_names())
    if normalized not in configured:
        repl_print(console, f"[{ERROR}]service not found:[/] {escape(normalized)}")
        return False

    if not interactive:
        prepare_repl_output_line()
    with console.status(
        f"[{DIM}]Verifying {escape(normalized)}…[/]",
        spinner="dots",
    ):
        match = repl_data.verify_integration(normalized)
    if match is None:
        repl_print(console, f"[{ERROR}]service not found:[/] {escape(normalized)}")
        return False

    _record_integration_show_observation(session, match)

    if interactive:
        repl_show_details(
            title=f"Integration › {normalized}",
            fields=[
                (key.replace("_", " ").capitalize(), str(value)) for key, value in match.items()
            ],
        )
        return True

    width = _repl_table_width(console)
    table = repl_table(
        title=f"Integration: {normalized}",
        title_style=BOLD_BRAND,
        show_header=False,
        width=width,
    )
    table.add_column("key", style="bold", no_wrap=True)
    value_width = max(20, width - 20)
    table.add_column("value", overflow="fold", max_width=value_width)
    for key, value in match.items():
        table.add_row(escape(key), escape(str(value)))
    print_repl_table(console, table)
    return True


def _run_integrations_setup(session: Session, console: Console, args: list[str]) -> bool:
    headless = session_terminal(session) is None
    if len(args) < 2:
        # Bare setup delegates to the CLI service picker on the REPL; headless
        # surfaces (Telegram) have no picker, so return usage guidance instead.
        if not headless:
            # Interactive service picker + credential prompts on the real TTY.
            result = run_cli_command(console, ["integrations", "setup"], capture_output=False)
            session.refresh_integration_state()
            return result
        repl_print(console, f"[{DIM}]usage:[/] /integrations setup <service>")
        publish_headless_slash_response(
            session, message="Usage: /integrations setup <service>", ok=False
        )
        return True

    service = args[1]
    cli_cmd = " ".join(["uv run opensre integrations setup", service, *args[2:]]).strip()
    if headless:
        message = (
            f"{escape(service)} setup needs interactive credentials (API keys, URLs, tokens) "
            f"and cannot finish in Telegram.\n\n"
            f"Run on the server:\n  {cli_cmd}\n\n"
            "Then check status with `/integrations list` or "
            f"`/integrations verify {escape(service)}`."
        )
        repl_print(console, message)
        publish_headless_slash_response(session, message=message, ok=True)
        session.refresh_integration_state()
        return True

    result = run_cli_command(
        console,
        ["integrations", "setup", service, *args[2:]],
        capture_output=False,
        session=session,
    )
    session.refresh_integration_state()
    return result


def _cmd_integrations(session: Session, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_integrations_menu(session, console)

    sub = (args[0].lower() if args else "list").strip()

    if sub in ("list", "ls"):
        prepare_repl_output_line()
        with console.status(f"[{DIM}]Verifying integrations…[/]", spinner="dots"):
            results = repl_data.load_verified_integrations()
        _record_integrations_observation(session, results)
        render_integrations_table(console, results)
        return True

    if sub == "verify":
        if len(args) > 2:
            repl_print(
                console,
                f"[{DIM}]usage:[/] /integrations verify [service]  "
                f"(or [bold]/verify [service][/bold])",
            )
            session.mark_latest(ok=False, kind="slash")
            return True
        return _run_verify(session, console, args[1] if len(args) == 2 else None)

    if sub == "setup":
        return _run_integrations_setup(session, console, args)

    if sub == "remove":
        return _handle_remove(session, console, args[1] if len(args) > 1 else None)

    if sub == "show":
        if len(args) < 2:
            repl_print(console, f"[{DIM}]usage:[/] /integrations show <service>")
            session.mark_latest(ok=False, kind="slash")
            return True
        if not _render_integration_show(session, console, args[1]):
            session.mark_latest(ok=False, kind="slash")
        return True

    repl_print(
        console,
        f"[{ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/integrations list[/bold], [bold]/integrations verify[/bold], "
        "or [bold]/integrations show <service>[/bold])",
    )
    session.mark_latest(ok=False, kind="slash")
    return True


def _show_connections(session: Session, console: Console, *, mcp: bool = False) -> None:
    with console.status(f"[{DIM}]Verifying connections…[/]", spinner="dots"):
        results = repl_data.load_verified_integrations()
    if mcp:
        results = [item for item in results if item.get("service") in MCP_INTEGRATION_SERVICES]
    _record_integrations_observation(session, results)
    repl_show_details(
        title="MCP › Connected servers" if mcp else "Integrations › Connections",
        fields=[
            (
                item.get("service", "Unknown"),
                f"{item.get('status', 'unknown')} · {item.get('detail', '')}",
            )
            for item in results
        ],
        note=(
            "No configured connections. Use Connect server."
            if mcp
            else "No configured integrations. Use Set up integration."
        )
        if not results
        else "",
    )


def _browse_integration_details(session: Session, console: Console) -> None:
    initial: str | None = None
    while True:
        choices = _configured_service_choices()
        if not choices:
            repl_show_details(
                title="Integrations › Details",
                fields=[],
                note="No configured integrations. Use Set up integration.",
            )
            return
        svc = repl_choose_one(
            title="Integration details",
            breadcrumb=f"{_ROOT_INTEGRATIONS}{CRUMB_SEP}Details",
            panel=True,
            choices=choices,
            initial_value=initial,
        )
        if svc is None:
            return
        initial = svc
        _render_integration_show(session, console, svc, interactive=True)


def _interactive_integrations_menu(session: Session, console: Console) -> bool:
    initial = "list"
    while True:
        sub = repl_choose_one(
            title="Integrations",
            breadcrumb=_ROOT_INTEGRATIONS,
            panel=True,
            initial_value=initial,
            choices=[
                ("list", "View integrations ›"),
                ("verify", "Verify connections"),
                ("show", "View details ›"),
                ("setup", "Set up integration ›"),
                ("remove", "Remove integration ›"),
                ("done", "Done"),
            ],
        )
        if sub is None or sub == "done":
            return True
        initial = sub
        if sub == "list":
            _show_connections(session, console)
        elif sub == "show":
            _browse_integration_details(session, console)
        elif sub == "remove":
            _handle_remove(session, console, None)
        else:
            _cmd_integrations(session, console, [sub])
            repl_section_break(console)


def _cmd_mcp(session: Session, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_mcp_menu(session, console)

    sub = (args[0].lower() if args else "list").strip()

    if sub in ("list", "ls"):
        render_mcp_table(console, repl_data.load_verified_integrations())
        return True

    if sub == "connect":
        return _run_integrations_setup(session, console, ["setup", *args[1:]])

    if sub == "disconnect":
        return _handle_remove(session, console, args[1] if len(args) > 1 else None, mcp=True)

    repl_print(
        console,
        f"[{ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/mcp list[/bold], [bold]/mcp connect[/bold], or [bold]/mcp disconnect[/bold])",
    )
    session.mark_latest(ok=False, kind="slash")
    return True


def _interactive_mcp_menu(session: Session, console: Console) -> bool:
    initial = "list"
    while True:
        sub = repl_choose_one(
            title="MCP servers",
            breadcrumb=_ROOT_MCP,
            panel=True,
            initial_value=initial,
            choices=[
                ("list", "Connected servers ›"),
                ("connect", "Connect server ›"),
                ("disconnect", "Disconnect server ›"),
                ("done", "Done"),
            ],
        )
        if sub is None or sub == "done":
            return True
        initial = sub
        if sub == "list":
            _show_connections(session, console, mcp=True)
        elif sub == "disconnect":
            _handle_remove(session, console, None, mcp=True)
        else:
            _cmd_mcp(session, console, [sub])
            repl_section_break(console)


_INTEGRATIONS_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("list", "list all configured integrations"),
    ("ls", "alias for list"),
    ("verify", "run health checks on all integrations"),
    ("show", "show details for a single integration"),
)

_MCP_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("list", "list connected MCP servers"),
    ("ls", "alias for list"),
    ("connect", "add an MCP server via opensre integrations setup"),
    ("disconnect", "remove an MCP server"),
)

COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/verify",
        "Verify configured integration connectivity.",
        _cmd_verify,
        usage=("/verify", "/verify <service>"),
    ),
    SlashCommand(
        "/integrations",
        "Manage integrations.",
        _cmd_integrations,
        usage=(
            "/integrations",
            "/integrations list",
            "/integrations verify",
            "/integrations verify <service>",
            "/integrations show <service>",
        ),
        notes=("In a TTY, bare /integrations opens an interactive menu.",),
        first_arg_completions=_INTEGRATIONS_FIRST_ARGS,
    ),
    SlashCommand(
        "/mcp",
        "Manage MCP servers.",
        _cmd_mcp,
        usage=("/mcp", "/mcp list", "/mcp connect", "/mcp disconnect"),
        notes=("In a TTY, bare /mcp opens an interactive menu.",),
        first_arg_completions=_MCP_FIRST_ARGS,
    ),
]

__all__ = ["COMMANDS"]
