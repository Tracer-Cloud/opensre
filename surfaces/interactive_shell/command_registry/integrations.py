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
from surfaces.interactive_shell.command_registry.setup_resume import resume_after_setup
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
from surfaces.shared.terminal.components.rendering import (
    _repl_table_width,
    print_repl_table,
    repl_print,
)

_ROOT_INTEGRATIONS = "/integrations"
_ROOT_MCP = "/mcp"


def _configured_service_choices() -> list[tuple[str, str]]:
    """Build picker choices from configured integrations (no live verification)."""
    return [(name, name) for name in repl_data.configured_integration_names()]


def _handle_remove(session: Session, console: Console, service: str | None) -> bool:
    """Remove an integration with a native inline-picker confirmation (no subprocess)."""
    from infrastructure.analytics.capture import capture_integration_removed
    from integrations.registry import resolve_management_service
    from integrations.store import remove_integration
    from integrations.webapp_vault import delete_webapp_org_integration

    svc = resolve_management_service(service) if service else service
    if not svc:
        if not repl_tty_interactive():
            repl_print(console, f"[{DIM}]usage:[/] /integrations remove <service>")
            session.mark_latest(ok=False, kind="slash")
            return True
        choices = _configured_service_choices()
        if not choices:
            repl_print(console, f"[{DIM}]no integrations in store to remove.[/]")
            return True
        svc = repl_choose_one(
            title="select integration to remove",
            breadcrumb=f"{_ROOT_INTEGRATIONS}{CRUMB_SEP}remove",
            choices=choices,
        )
        if not svc:
            return True

    if repl_tty_interactive():
        confirmed = repl_choose_one(
            title=f"remove '{escape(svc)}'?",
            breadcrumb=f"{_ROOT_INTEGRATIONS}{CRUMB_SEP}remove{CRUMB_SEP}{escape(svc)}",
            choices=[
                ("no", "No, cancel"),
                ("yes", f"Yes, remove '{svc}'"),
            ],
        )
        prepare_repl_output_line()
        if confirmed != "yes":
            repl_print(console, f"[{DIM}]cancelled.[/]")
            session.refresh_integration_state()
            return True
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

    render_integrations_table(console, results)
    _print_verify_summary(console, results, single_service=service is not None)
    return True


def _cmd_verify(session: Session, console: Console, args: list[str]) -> bool:
    return _cmd_integrations(session, console, ["verify", *args])


def _render_integration_show(console: Console, service: str) -> bool:
    """Verify and print one integration. Returns False when the service is unknown."""
    from integrations.registry import resolve_management_service

    normalized = resolve_management_service(service)
    configured = set(repl_data.configured_integration_names())
    if normalized not in configured:
        repl_print(console, f"[{ERROR}]service not found:[/] {escape(normalized)}")
        return False

    prepare_repl_output_line()
    with console.status(
        f"[{DIM}]Verifying {escape(normalized)}…[/]",
        spinner="dots",
    ):
        match = repl_data.verify_integration(normalized)
    if match is None:
        repl_print(console, f"[{ERROR}]service not found:[/] {escape(normalized)}")
        return False

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
            resume_after_setup(session, console)
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
    # ``result`` is True for any interactive run; the resume re-checks the credential.
    resume_after_setup(session, console, service=service.lower())
    return result


def _cmd_integrations(session: Session, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_integrations_menu(session, console)

    sub = (args[0].lower() if args else "list").strip()

    if sub in ("list", "ls"):
        prepare_repl_output_line()
        with console.status(f"[{DIM}]Verifying integrations…[/]", spinner="dots"):
            results = repl_data.load_verified_integrations()
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
        if not _render_integration_show(console, args[1]):
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


def _interactive_integrations_menu(session: Session, console: Console) -> bool:
    root = _ROOT_INTEGRATIONS
    while True:
        sub = repl_choose_one(
            title="integrations",
            breadcrumb=root,
            choices=[
                ("list", "/integrations list"),
                ("verify", "/integrations verify"),
                ("show", "/integrations show <service>"),
                ("setup", "/integrations setup <service>"),
                ("remove", "/integrations remove <service>"),
                ("done", "done"),
            ],
        )
        if sub is None or sub == "done":
            return True
        show_section_break = False
        if sub == "list":
            _cmd_integrations(session, console, ["list"])
            show_section_break = True
        elif sub == "verify":
            _cmd_integrations(session, console, ["verify"])
            show_section_break = True
        elif sub == "setup":
            _cmd_integrations(session, console, ["setup"])
            show_section_break = True
        elif sub == "show":
            choices = _configured_service_choices()
            if not choices:
                repl_print(console, f"[{DIM}]no integrations in store to show.[/]")
                show_section_break = True
            else:
                svc = repl_choose_one(
                    title="service",
                    breadcrumb=f"{root}{CRUMB_SEP}show",
                    choices=choices,
                )
                if svc and _render_integration_show(console, svc):
                    show_section_break = True
        elif sub == "remove":
            _handle_remove(session, console, None)
            show_section_break = True
        if show_section_break:
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
        return _handle_remove(session, console, args[1] if len(args) > 1 else None)

    repl_print(
        console,
        f"[{ERROR}]unknown subcommand:[/] {escape(sub)}  "
        "(try [bold]/mcp list[/bold], [bold]/mcp connect[/bold], or [bold]/mcp disconnect[/bold])",
    )
    session.mark_latest(ok=False, kind="slash")
    return True


def _interactive_mcp_menu(session: Session, console: Console) -> bool:
    root = _ROOT_MCP
    while True:
        sub = repl_choose_one(
            title="mcp",
            breadcrumb=root,
            choices=[
                ("list", "/mcp list"),
                ("connect", "/mcp connect <server>"),
                ("disconnect", "/mcp disconnect <server>"),
                ("done", "done"),
            ],
        )
        if sub is None or sub == "done":
            return True
        show_section_break = False
        if sub == "list":
            _cmd_mcp(session, console, ["list"])
            show_section_break = True
        elif sub == "connect":
            _cmd_mcp(session, console, ["connect"])
            show_section_break = True
        elif sub == "disconnect":
            choices = _mcp_service_choices()
            if not choices:
                repl_print(console, f"[{DIM}]no MCP servers configured.[/]")
                show_section_break = True
            else:
                svc = repl_choose_one(
                    title="server",
                    breadcrumb=f"{root}{CRUMB_SEP}disconnect",
                    choices=choices,
                )
                if svc:
                    _cmd_mcp(session, console, ["disconnect", svc])
                    show_section_break = True
        if show_section_break:
            repl_section_break(console)


_INTEGRATIONS_FIRST_ARGS: tuple[tuple[str, str], ...] = (
    ("setup", "guided setup for an integration"),
    ("remove", "remove a configured integration"),
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
            "/integrations setup <service>",
            "/integrations list",
            "/integrations verify",
            "/integrations verify <service>",
            "/integrations show <service>",
            "/integrations remove <service>",
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
