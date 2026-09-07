"""Slash commands for CLI parity, delegating to the Click CLI via subprocess."""

from __future__ import annotations

import os
import subprocess

from rich.console import Console

from config.constants import OPENSRE_PARENT_INTERACTIVE_SHELL_ENV
from core.agent_harness.spi.session_state import session_terminal, set_turn_outcome_hint
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.runtime.subprocess_runner import build_opensre_cli_argv
from surfaces.interactive_shell.telemetry.turn_outcome import format_wizard_cli_outcome
from surfaces.interactive_shell.ui import DIM, ERROR, print_command_output
from surfaces.shared.terminal.components.choice_menu import prepare_repl_output_line

_UPDATE_SUBPROCESS_TIMEOUT_SECONDS = 300
_HEADLESS_CLI_SUBPROCESS_TIMEOUT_SECONDS = 90.0


def publish_headless_slash_response(
    session: Session,
    *,
    message: str,
    ok: bool = True,
) -> None:
    """Pin an explicit slash reply for gateway/headless surfaces (wizards, setup)."""
    session.complete_latest_record(
        "slash",
        response_text=message.strip(),
        ok=ok,
        slash_outcome="headless_guidance",
    )


def _decode_subprocess_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _stash_collapsed_output(session: Session | None, body: str) -> None:
    terminal = session_terminal(session) if session is not None else None
    if terminal is not None:
        terminal.stash_collapsed_tool_output(body)


def _cli_command_succeeded(exit_code: int | None) -> bool:
    return exit_code == 0


def run_cli_command(
    console: Console,
    args: list[str],
    *,
    session: Session | None = None,
    subprocess_timeout: float | None = None,
    capture_output: bool = True,
) -> bool:
    """Helper to delegate complex or interactive Click commands to a child process.

    ``subprocess_timeout`` caps how long ``subprocess.run`` waits before raising
    :class:`~subprocess.TimeoutExpired`. Interactive flows use ``None`` so the
    child can prompt as long as needed; callers that hit the network without a
    guaranteed response (like ``opensre update``) pass a bounded timeout. The
    timeout applies whether or not output is captured — it no longer forces
    capture, so a long-running network command can still stream live to the
    real TTY (e.g. the install script's own progress output during an update)
    while still being killed if it hangs.

    ``capture_output`` (default ``True``) makes the helper capture stdout/stderr
    and replay them through ``console``, so delegated command output appears
    inside the REPL buffer (instead of bypassing ``console.print`` via the
    child's inherited stdout FD) and lands on the slash history row, where the
    action agent reads it back as its tool observation. Capture is the default
    because most delegated commands are non-interactive printers; only commands
    that prompt on the real TTY (``onboard``, ``login``, ``uninstall``), stream
    their own progress UI (``update``), or block indefinitely (``cron start``)
    must pass ``capture_output=False``.

    **Return value:** Reports subprocess success for headless/gateway sessions
    (``session`` with no terminal facet) so slash analytics can show failure.
    On the interactive REPL, always returns ``True`` so delegated CLI failures
    do not propagate to :func:`dispatch_slash` and exit the shell — failures
    are still recorded via :meth:`Session.mark_latest`.

    Ctrl+C sends :exc:`KeyboardInterrupt`, which subclasses :exc:`BaseException`
    rather than :exc:`Exception`; it is handled here so the REPL survives and the
    child process exits on SIGINT alongside the interrupted ``run`` call.
    """
    console.print()
    cmd = build_opensre_cli_argv(args)
    headless = session is not None and session_terminal(session) is None
    should_capture = capture_output or headless
    if headless and subprocess_timeout is None:
        subprocess_timeout = _HEADLESS_CLI_SUBPROCESS_TIMEOUT_SECONDS
    child_env = os.environ.copy()
    child_env[OPENSRE_PARENT_INTERACTIVE_SHELL_ENV] = "1"
    if should_capture:
        # Captured child stdout isn't a TTY, so force Rich colour there and parse
        # it back in print_command_output — otherwise its styling would be lost.
        child_env["FORCE_COLOR"] = "1"
        # Without a terminal (and with no exported COLUMNS) Rich in the child
        # falls back to 80 columns and truncates table cells with an ellipsis —
        # `/cron list` task ids come back as `ecf7c2580b…`, which the action
        # agent cannot chain into `/cron remove <id>`. Render captured output
        # wide; the REPL re-print re-wraps to the real terminal anyway. On a
        # "dumb" TERM Rich short-circuits to 80x25 and ignores COLUMNS, so give
        # the forced-colour child a real TERM as well (headless/CI surfaces).
        if child_env.get("TERM", "").lower() in {"dumb", "unknown"}:
            child_env["TERM"] = "xterm-256color"
        child_env.setdefault("COLUMNS", "200")
    exit_code: int | None = 0
    try:
        if should_capture:
            captured_result = subprocess.run(
                cmd,
                check=False,
                timeout=subprocess_timeout,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=child_env,
            )
            exit_code = captured_result.returncode
            print_command_output(
                console,
                captured_result.stdout or "",
                on_collapse=lambda body: _stash_collapsed_output(session, body),
            )
            print_command_output(
                console,
                captured_result.stderr or "",
                style=ERROR,
                on_collapse=lambda body: _stash_collapsed_output(session, body),
            )
            if captured_result.returncode != 0:
                console.print(
                    f"[{ERROR}]CLI command exited with non-zero code {captured_result.returncode}[/]"
                )
        else:
            # timeout=None is a no-op for subprocess.run, so this covers both the
            # timed (/update) and untimed (/onboard) interactive callers.
            interactive_result = subprocess.run(
                cmd, check=False, timeout=subprocess_timeout, env=child_env
            )
            exit_code = interactive_result.returncode
            # The child wrote straight to the terminal, bypassing Rich, so Rich has no
            # idea where the cursor ended up (e.g. mid-line after a \r-redrawn progress
            # bar). Force a fresh line before resuming Rich output, or the blank line
            # below just terminates the child's last line instead of being a real gap.
            prepare_repl_output_line()
            console.print()
            if interactive_result.returncode != 0:
                console.print(
                    f"[{ERROR}]CLI command exited with non-zero code {interactive_result.returncode}[/]"
                )
    except subprocess.TimeoutExpired as exc:
        exit_code = None
        # Same cursor hazard as the normal-exit and KeyboardInterrupt paths, and the
        # most likely one to actually hit it: the timeout exists specifically to
        # kill a hung install script, i.e. a streamed child mid-redraw of its own
        # progress bar.
        prepare_repl_output_line()
        print_command_output(
            console,
            _decode_subprocess_stream(exc.stdout),
            on_collapse=lambda body: _stash_collapsed_output(session, body),
        )
        print_command_output(
            console,
            _decode_subprocess_stream(exc.stderr),
            style=ERROR,
            on_collapse=lambda body: _stash_collapsed_output(session, body),
        )
        console.print(f"[{ERROR}]error:[/] CLI command timed out")
    except KeyboardInterrupt:
        exit_code = None
        # Same cursor hazard as the normal-exit path: Ctrl+C can land mid-line while
        # a streamed child is mid-redraw of its own progress bar.
        prepare_repl_output_line()
        console.print(f"[{DIM}]CLI command cancelled (Ctrl+C).[/]")
    except Exception as exc:
        exit_code = None
        console.print(f"[{ERROR}]error running CLI command:[/] {exc}")
    console.print()
    if session is not None and not should_capture:
        set_turn_outcome_hint(session, format_wizard_cli_outcome(args, exit_code=exit_code))
    ok = _cli_command_succeeded(exit_code)
    if session is not None and not ok:
        session.mark_latest(ok=False, kind="slash")
    # Headless/gateway surfaces need the real exit status for slash analytics.
    # Interactive REPL handlers must not return False to dispatch_slash on CLI
    # failure — that would exit the shell (/exit is the only intentional False).
    return ok if headless else True


def _cmd_onboard(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if session_terminal(session) is None:
        cli_cmd = " ".join(["uv run opensre onboard", *args]).strip()
        message = (
            "Onboarding is an interactive wizard for LLM provider setup. "
            "It cannot run inside a Telegram chat.\n\n"
            f"Run on the server:\n  {cli_cmd}\n\n"
            "Configure integrations separately with `/integrations setup <service>`."
        )
        console.print()
        console.print(message)
        publish_headless_slash_response(session, message=message)
        return True
    # The REPL loop treats ``/onboard`` as exclusive-stdin in
    # ``runtime.input_policy`` so the prompt_toolkit Application is torn down before
    # this handler runs — the wizard subprocess therefore gets exclusive
    # stdin and can drive its own interactive prompts without conflicting
    # with the shell's UI.
    return run_cli_command(console, ["onboard", *args], capture_output=False, session=session)


def _cmd_setup(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    if session_terminal(session) is None:
        message = (
            "Setup signs in to an OpenSRE account and activates its hosted model. "
            "It cannot run inside a Telegram chat.\n\n"
            "Run on the server:\n  uv run opensre setup\n\n"
            "Configure integrations separately with `/integrations setup <service>`."
        )
        console.print()
        console.print(message)
        publish_headless_slash_response(session, message=message)
        return True
    return run_cli_command(console, ["setup", *args], capture_output=False, session=session)


def _cmd_account(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    subcommand = args[0].lower() if args else "status"
    if session_terminal(session) is None and subcommand == "login":
        message = (
            "GitHub account login opens a browser and cannot run inside a chat.\n\n"
            "Run on the server:\n  uv run opensre account login"
        )
        console.print()
        console.print(message)
        publish_headless_slash_response(session, message=message)
        return True
    capture_output = subcommand in {"status", "logout"}
    handled = run_cli_command(
        console, ["account", *args], capture_output=capture_output, session=session
    )
    if subcommand == "logout" and session_terminal(session) is not None:
        from config.account import account_llm_route

        if account_llm_route() is None:
            console.print(f"[{DIM}]Signed out. Closing the interactive shell.[/]")
            return False
    return handled


def _cmd_auth(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    # ``login`` (and provider flows) prompt on the real TTY; status/logout print.
    capture_output = not args or args[0].lower() in {"status", "logout"}
    return run_cli_command(console, ["auth", *args], capture_output=capture_output, session=session)


def _cmd_login(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    # Interactive provider login prompts on the real TTY.
    return run_cli_command(console, ["auth", "login", *args], capture_output=False, session=session)


def _cmd_remote(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    # Remote-sync configuration prompts on the real TTY (click.prompt).
    return run_cli_command(console, ["remote", *args], capture_output=False, session=session)


def _cmd_guardrails(session: Session, console: Console, args: list[str]) -> bool:
    return run_cli_command(console, ["guardrails", *args], session=session)


def _cmd_update(session: Session, console: Console, args: list[str]) -> bool:  # noqa: ARG001
    # The install script streams its own terminal-aware progress UI.
    return run_cli_command(
        console,
        ["update", *args],
        subprocess_timeout=_UPDATE_SUBPROCESS_TIMEOUT_SECONDS,
        capture_output=False,
        session=session,
    )


def _cmd_uninstall(session: Session, console: Console, args: list[str]) -> bool:
    # Confirmation prompt (questionary.confirm) needs the real TTY.
    return run_cli_command(console, ["uninstall", *args], capture_output=False, session=session)


def _cmd_config(session: Session, console: Console, args: list[str]) -> bool:
    return run_cli_command(console, ["config", *args], session=session)


def _cmd_messaging(session: Session, console: Console, args: list[str]) -> bool:
    return run_cli_command(console, ["messaging", *args], session=session)


def _cmd_cron(session: Session, console: Console, args: list[str]) -> bool:
    # ``cron start`` blocks as the scheduler daemon and must stream to the real
    # TTY. Every other subcommand is a printer; the captured output reaches the
    # slash history row, where the action agent reads it back (e.g. task ids
    # from ``/cron list`` to chain a remove).
    capture_output = not args or args[0].lower() != "start"
    return run_cli_command(console, ["cron", *args], capture_output=capture_output, session=session)


def _cmd_sentry(session: Session, console: Console, args: list[str]) -> bool:
    return run_cli_command(console, ["sentry", *args], session=session)


def _cmd_posthog(session: Session, console: Console, args: list[str]) -> bool:
    return run_cli_command(console, ["posthog", *args], capture_output=True, session=session)


def _cmd_debug(session: Session, console: Console, args: list[str]) -> bool:
    return run_cli_command(console, ["debug", *args], session=session)


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/account",
        "Sign in to OpenSRE with GitHub and inspect the local account.",
        _cmd_account,
        usage=("/account", "/account login", "/account status", "/account logout"),
    ),
    SlashCommand(
        "/auth",
        "Log in to LLM providers and inspect local auth state.",
        _cmd_auth,
        usage=("/auth", "/auth status", "/auth login deepseek", "/auth logout deepseek"),
    ),
    SlashCommand(
        "/login",
        "Shortcut for LLM provider login.",
        _cmd_login,
        usage=("/login", "/login chatgpt", "/login claude", "/login deepseek"),
    ),
    SlashCommand(
        "/setup",
        "First-run setup: OpenSRE account, hosted model, then the interactive shell.",
        _cmd_setup,
        usage=("/setup",),
    ),
    SlashCommand(
        "/onboard",
        "Run the interactive onboarding wizard.",
        _cmd_onboard,
        usage=("/onboard", "/onboard local_llm"),
    ),
    SlashCommand(
        "/remote",
        "Connect to and trigger a remote deployed agent.",
        _cmd_remote,
        usage=(
            "/remote health",
            "/remote ops",
            "/remote pull",
            "/remote trigger",
        ),
    ),
    SlashCommand(
        "/guardrails",
        "Manage sensitive information guardrail rules.",
        _cmd_guardrails,
        usage=(
            "/guardrails audit",
            "/guardrails init",
            "/guardrails rules",
            "/guardrails test",
        ),
    ),
    SlashCommand(
        "/update",
        "Check for a newer version and update if available.",
        _cmd_update,
    ),
    SlashCommand(
        "/uninstall",
        "Remove OpenSRE and all local data from this machine.",
        _cmd_uninstall,
    ),
    SlashCommand(
        "/config",
        "Show or edit local OpenSRE config.",
        _cmd_config,
        usage=("/config show", "/config set <key> <value>"),
    ),
    SlashCommand(
        "/messaging",
        "Manage messaging security and identities.",
        _cmd_messaging,
        usage=(
            "/messaging pair",
            "/messaging allow",
            "/messaging revoke",
            "/messaging status",
        ),
    ),
    SlashCommand(
        "/cron",
        "Manage cron-driven scheduled deliveries.",
        _cmd_cron,
        usage=(
            "/cron list",
            "/cron add --name <name>",
            "/cron remove <id>",
            "/cron run <id>",
            "/cron logs <id>",
        ),
    ),
    SlashCommand(
        "/sentry",
        "Schedule and run automated Sentry morning digests or uptime watches.",
        _cmd_sentry,
        usage=(
            "/sentry digest run",
            "/sentry digest schedule list",
            "/sentry digest schedule add",
            "/sentry digest schedule run <id>",
            "/sentry digest schedule remove <id>",
            "/sentry uptime check",
            "/sentry uptime watch list",
            "/sentry uptime watch add",
            "/sentry uptime watch run <id>",
            "/sentry uptime watch remove <id>",
        ),
    ),
    SlashCommand(
        "/posthog",
        "Schedule and run automated PostHog per-metric summary reports.",
        _cmd_posthog,
        usage=(
            "/posthog report run",
            "/posthog report schedule list",
            "/posthog report schedule add",
            "/posthog report schedule run <id>",
            "/posthog report schedule remove <id>",
        ),
    ),
    SlashCommand(
        "/debug",
        "run targeted runtime diagnostics",
        _cmd_debug,
    ),
]
