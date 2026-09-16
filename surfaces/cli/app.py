"""OpenSRE CLI - open-source SRE agent.

Enable shell tab-completion (add to your shell profile for persistence):

  bash:  eval "$(_OPENSRE_COMPLETE=bash_source opensre)"
  zsh:   eval "$(_OPENSRE_COMPLETE=zsh_source opensre)"
  fish:  _OPENSRE_COMPLETE=fish_source opensre | source
"""

from __future__ import annotations

import os
import sys
from contextlib import suppress
from typing import TYPE_CHECKING

import click

from config.constants.product import RELEASE_STAGE_BANNER
from surfaces.cli import startup
from surfaces.cli.group import LazyRichGroup, ThemeParamType
from surfaces.cli.host import (
    CLI_HOST_CONTEXT_KEY,
    AfterBanner,
    CliHost,
    ShellLauncher,
    cli_host,
)
from surfaces.cli.invocation import (
    ensure_utf8_stdio,
    is_fast_help_invocation,
    is_fast_version_invocation,
    print_fast_version,
    resolve_command_parts,
)
from surfaces.cli.telemetry import (
    analytics_needs_flush,
    build_cli_invoked_properties,
    capture_cli_invoked,
    capture_exception,
    capture_first_run_if_needed,
    load_structured_error_type,
    render_landing,
    render_structured_error,
    report_exception,
    should_report_exception,
    shutdown_analytics,
)

if TYPE_CHECKING:
    from infrastructure.analytics.provider import Properties

# One-shot CLI exit: a queued or in-flight analytics event dies with the
# process because the sender runs on a daemon thread, so wait briefly for the
# POST to land before returning.
_ANALYTICS_FLUSH_TIMEOUT_SECONDS = 2.0

_CAPTURE_CLI_ANALYTICS = "capture_cli_analytics"
_CLI_ANALYTICS_CAPTURED = "cli_analytics_captured"
_CLI_ARGV = "cli_argv"
_RECORD_INSTALL_ONLY = "record_install_only"
# Launch work startup held back for the shell to run once its banner is painted.
_AFTER_BANNER = "after_banner"


def _cli_invoked_properties(ctx: click.Context) -> Properties:
    raw_argv = ctx.obj.get(_CLI_ARGV, []) if ctx.obj else []
    command_parts = resolve_command_parts(
        ctx.command,
        raw_argv if isinstance(raw_argv, list) else [],
    )
    obj = ctx.obj if ctx.obj else {}
    return build_cli_invoked_properties(
        entrypoint="opensre",
        command_parts=command_parts,
        json_output=bool(obj.get("json", False)),
        verbose=bool(obj.get("verbose", False)),
        debug=bool(obj.get("debug", False)),
        yes=bool(obj.get("yes", False)),
        interactive=bool(obj.get("interactive", True)),
    )


def _capture_accepted_cli_invocation(ctx: click.Context) -> None:
    if not ctx.obj.get(_CAPTURE_CLI_ANALYTICS, False):
        return
    if ctx.obj.get(_CLI_ANALYTICS_CAPTURED, False):
        return
    ctx.obj[_CLI_ANALYTICS_CAPTURED] = True
    capture_first_run_if_needed()
    if ctx.obj.get(_RECORD_INSTALL_ONLY, False):
        return
    capture_cli_invoked(_cli_invoked_properties(ctx))


def _repl_preference(
    *,
    resume_session_id: str | None,
    interactive: bool,
    passed_on_command_line: bool,
) -> bool | None:
    """Whether this invocation forces the shell on or off, or has no opinion.

    ``None`` is not "off" — it means defer, so ``ReplConfig`` still honors
    ``OPENSRE_INTERACTIVE`` and ``config.yml``. A bare ``opensre`` must land
    here, or the default run stops opening the shell.
    """
    if resume_session_id is not None:
        return True
    if passed_on_command_line:
        return interactive
    return None


def _run_without_subcommand(
    group: click.Group,
    *,
    launch_shell: ShellLauncher | None,
    resume_session_id: str | None,
    sync_on_exit: bool,
    interactive: bool,
    passed_on_command_line: bool,
    layout: str | None,
    theme: str | None,
    after_banner: AfterBanner,
) -> int:
    """Serve a bare ``opensre``: open the shell, or print the landing page.

    The shell needs a terminal on both ends and a host that can launch it, so a
    piped or redirected run — or a CLI invoked without a host — falls through
    to the landing page rather than waiting on a prompt nobody can answer.
    ``--resume`` opens a session even when config has the shell off, because
    resuming is the whole intent of that flag.
    """
    from config.repl_config import ReplConfig

    if launch_shell is not None and sys.stdin.isatty() and sys.stdout.isatty():
        config = ReplConfig.load(
            cli_enabled=_repl_preference(
                resume_session_id=resume_session_id,
                interactive=interactive,
                passed_on_command_line=passed_on_command_line,
            ),
            cli_layout=layout,
            cli_theme=theme,
        )
        if config.enabled or resume_session_id:
            exit_code = launch_shell(config, resume_session_id, after_banner)
            if sync_on_exit:
                from surfaces.cli.commands.remote_sync import run_remote_sync_on_exit

                run_remote_sync_on_exit()
            return exit_code

    # No shell to paint a banner: the held-back launch work runs here instead.
    if after_banner is not None:
        after_banner()
    click.echo(RELEASE_STAGE_BANNER, err=True)
    render_landing(group)
    return 0


@click.group(
    cls=LazyRichGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(prog_name="opensre")
@click.option(
    "--json", "-j", "json_output", is_flag=True, help="Emit machine-readable JSON output."
)
@click.option("--verbose", is_flag=True, help="Print extra diagnostic information.")
@click.option("--debug", is_flag=True, help="Print debug-level logs and traces.")
@click.option("--yes", "-y", is_flag=True, help="Auto-confirm all interactive prompts.")
@click.option(
    "--interactive/--no-interactive",
    default=True,
    help="Disable the interactive shell and print the landing page instead.",
)
@click.option(
    "--resume",
    "resume_session_id",
    default=None,
    metavar="SESSION-ID",
    help="Resume a previous interactive shell session by ID, prefix, or name substring.",
)
@click.option(
    "--sync-on-exit",
    is_flag=True,
    help="Sync sessions and memory after the interactive shell exits.",
)
@click.option(
    "--layout",
    type=click.Choice(["classic", "pinned"]),
    default=None,
    help="Interactive-shell layout: 'classic' (scrolling) or 'pinned' (fixed "
    "input bar). Overrides OPENSRE_LAYOUT env var and ~/.opensre/config.yml.",
)
@click.option(
    "--theme",
    type=ThemeParamType(),
    default=None,
    help="Interactive-shell color palette. Overrides OPENSRE_THEME env var "
    "and ~/.opensre/config.yml interactive.theme.",
)
@click.option(
    "--record-install",
    is_flag=True,
    hidden=True,
    help="Record installer analytics and exit.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    json_output: bool,
    verbose: bool,
    debug: bool,
    yes: bool,
    interactive: bool,
    resume_session_id: str | None,
    sync_on_exit: bool,
    layout: str | None,
    theme: str | None,
    record_install: bool,
) -> None:
    """OpenSRE - open-source SRE agent."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output
    ctx.obj["verbose"] = verbose
    ctx.obj["debug"] = debug
    ctx.obj["yes"] = yes
    ctx.obj["interactive"] = interactive
    ctx.obj[_RECORD_INSTALL_ONLY] = record_install

    from surfaces.cli.runtime_flags import sync_runtime_flags_from_click

    sync_runtime_flags_from_click(ctx)

    if verbose or debug:
        os.environ["TRACER_VERBOSE"] = "1"

    from config.repl_config import ReplConfig

    _capture_accepted_cli_invocation(ctx)

    if record_install:
        raise click.exceptions.Exit(0)

    if ctx.invoked_subcommand is None:
        interactive_source = ctx.get_parameter_source("interactive")
        raise SystemExit(
            _run_without_subcommand(
                cli,
                launch_shell=cli_host(ctx).launch_shell,
                resume_session_id=resume_session_id,
                sync_on_exit=sync_on_exit,
                interactive=interactive,
                passed_on_command_line=(
                    interactive_source is not None and interactive_source.name == "COMMANDLINE"
                ),
                layout=layout,
                theme=theme,
                after_banner=ctx.obj.get(_AFTER_BANNER),
            )
        )

    # Apply interactive.theme / OPENSRE_THEME / --theme for subcommands (onboard, etc.).
    from infrastructure.terminal.theme import set_active_theme

    set_active_theme(ReplConfig.load(cli_theme=theme).theme)


def _should_capture_cli_exception(exc: click.ClickException) -> bool:
    """Return whether a Click error represents an unexpected internal failure."""
    return should_report_exception(exc)


def main(argv: list[str] | None = None, *, host: CliHost | None = None) -> int:
    """Run the CLI; ``host`` supplies what only the process entrypoint can (shell, gateway)."""
    ensure_utf8_stdio()
    cli_argv = list(sys.argv[1:] if argv is None else argv)
    if is_fast_version_invocation(cli_argv):
        print_fast_version(cli_argv)
        return 0
    fast_help = is_fast_help_invocation(cli, cli_argv)
    after_banner = None if fast_help else startup.run(cli, cli_argv)
    StructuredError = load_structured_error_type()

    try:
        cli(
            args=cli_argv,
            standalone_mode=False,
            obj={
                _CAPTURE_CLI_ANALYTICS: True,
                _CLI_ARGV: cli_argv,
                _AFTER_BANNER: after_banner,
                CLI_HOST_CONTEXT_KEY: host or CliHost(),
            },
        )
    except KeyboardInterrupt:
        # A KeyboardInterrupt that escapes cli() was not handled by our
        # double-exit logic (e.g. click.prompt, an unpatched library prompt).
        # Print a newline so the terminal cursor lands on a clean line, then
        # exit quietly — Click's "Aborted!" message is intentionally suppressed.
        print(flush=True)
        return 0
    except click.Abort:
        # Click raises Abort for some prompt-level cancel paths. Treat it as a
        # clean user cancel, not as an unexpected CLI failure.
        print(flush=True)
        return 0
    except click.ClickException as exc:
        if _should_capture_cli_exception(exc):
            report_exception(exc, context="surfaces.cli.main")
        exc.show()
        return exc.exit_code
    except StructuredError as exc:
        # A structured error raised by non-CLI code (tools/integrations) is not
        # a ClickException, so render it as a clean panel (no traceback) here.
        return render_structured_error(exc)
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        if exc.code is not None:
            click.echo(exc.code, err=True)
            return 1
        return 0
    except BaseException as exc:
        if not isinstance(exc, KeyboardInterrupt):
            capture_exception(exc, context="surfaces.cli.main.unhandled")
            with suppress(Exception):
                import sentry_sdk as _sentry_sdk

                _sentry_sdk.flush(timeout=2)
        raise
    finally:
        # Help never starts analytics; importing the provider here would
        # resolve the git version and fingerprint just to ask if a flush
        # is needed.
        if not fast_help:
            if analytics_needs_flush():
                shutdown_analytics(flush=True, timeout=_ANALYTICS_FLUSH_TIMEOUT_SECONDS)
            else:
                shutdown_analytics(flush=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
