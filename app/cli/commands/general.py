"""Single-command CLI entrypoints that do not need their own groups."""

from __future__ import annotations

import json
import platform
import sys
import time

import click

from app.analytics.cli import (
    capture_cli_invoked,
    capture_investigation_completed,
    capture_investigation_failed,
    capture_investigation_started,
)
from app.cli.constants import ALERT_TEMPLATE_CHOICES
from app.cli.context import is_json_output, is_yes
from app.cli.exit_codes import ERROR, SUCCESS
from app.version import get_version


@click.command(name="update")
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    help="Report whether an update is available without installing.",
)
@click.option("--yes", "-y", "local_yes", is_flag=True, help="Skip the confirmation prompt.")
def update_command(check_only: bool, local_yes: bool) -> None:
    """Check for a newer version and update if one is available."""
    from app.cli.update import run_update

    capture_cli_invoked()
    raise SystemExit(run_update(check_only=check_only, yes=local_yes or is_yes()))


@click.command(name="version")
def version_command() -> None:
    """Print detailed version, Python and OS info."""
    capture_cli_invoked()
    if is_json_output():
        click.echo(
            json.dumps(
                {
                    "opensre": get_version(),
                    "python": platform.python_version(),
                    "os": platform.system().lower(),
                    "arch": platform.machine(),
                }
            )
        )
        return
    click.echo(f"opensre {get_version()}")
    click.echo(f"Python  {platform.python_version()}")
    click.echo(f"OS      {platform.system().lower()} ({platform.machine()})")


@click.command(name="health")
@click.option("--watch", is_flag=True, help="Continuously refresh the health report.")
@click.option(
    "--rate", default=5, show_default=True, help="Refresh interval in seconds (with --watch)."
)
def health_command(watch: bool, rate: int) -> None:
    """Show a quick health summary of the local agent setup."""
    from app.cli.health_view import render_health_json, render_health_report
    from app.config import get_environment
    from app.integrations.store import STORE_PATH
    from app.integrations.verify import verify_integrations

    capture_cli_invoked()

    def _run_once() -> int:
        results = verify_integrations()
        environment = get_environment().value

        if is_json_output():
            render_health_json(
                environment=environment,
                integration_store_path=STORE_PATH,
                results=results,
            )
        else:
            from rich.console import Console

            render_health_report(
                console=Console(highlight=False),
                environment=environment,
                integration_store_path=STORE_PATH,
                results=results,
            )

        if any(result.get("status") in {"missing", "failed"} for result in results):
            return ERROR
        return SUCCESS

    if not watch:
        raise SystemExit(_run_once())

    try:
        while True:
            click.clear()
            _run_once()
            time.sleep(rate)
    except KeyboardInterrupt:
        raise SystemExit(SUCCESS) from None


@click.command(name="investigate")
@click.option(
    "--input",
    "-i",
    "input_path",
    default=None,
    type=click.Path(),
    help="Path to an alert file (.json, .md, .txt, ...). Use '-' to read from stdin.",
)
@click.option("--input-json", default=None, help="Inline alert JSON string.")
@click.option("--interactive", is_flag=True, help="Paste an alert JSON payload into the terminal.")
@click.option(
    "--print-template",
    type=click.Choice(ALERT_TEMPLATE_CHOICES),
    default=None,
    help="Print a starter alert JSON template and exit.",
)
@click.option(
    "--service",
    default=None,
    help=(
        "Start a runtime investigation for a deployed service by name. "
        "Pulls status, recent logs, and health from the configured remote ops provider."
    ),
)
@click.option(
    "--slack-thread",
    default=None,
    help=(
        "Optional Slack thread reference in 'CHANNEL/TS' format. When set with --service, "
        "the thread's messages are pulled via Slack's conversations.replies API "
        "(requires SLACK_BOT_TOKEN in the environment) and included as investigation context."
    ),
)
@click.option(
    "--output", "-o", default=None, type=click.Path(), help="Output JSON file (default: stdout)."
)
@click.option(
    "--evaluate",
    is_flag=True,
    help="After final diagnosis, LLM-judge vs OpenRCA scoring_points (rubric stripped from agent alert).",
)
def investigate_command(
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
    print_template: str | None,
    service: str | None,
    slack_thread: str | None,
    output: str | None,
    evaluate: bool,
) -> None:
    """Run an RCA investigation against an alert payload."""
    if service:
        _run_service_investigation(
            service=service,
            slack_thread=slack_thread,
            other_inputs={
                "input_path": input_path,
                "input_json": input_json,
                "interactive": interactive,
                "print_template": print_template,
                "evaluate": evaluate,
            },
            output=output,
        )
        return
    if slack_thread:
        from app.cli.errors import OpenSREError

        raise OpenSREError(
            "--slack-thread requires --service.",
            suggestion="Pass --service <name> alongside --slack-thread CHANNEL/TS.",
        )

    from app.cli import write_json
    from app.cli.alert_templates import build_alert_template
    from app.cli.investigate import run_investigation_cli, run_investigation_cli_streaming
    from app.cli.payload import load_payload

    capture_investigation_started(
        input_path=input_path,
        input_json=input_json,
        interactive=interactive,
    )
    try:
        if print_template:
            write_json(build_alert_template(print_template), output)
            capture_investigation_completed()
            raise SystemExit(SUCCESS)

        payload = load_payload(
            input_path=input_path,
            input_json=input_json,
            interactive=interactive,
        )
        # Only stream the live UI when the user is interactively watching stdout
        # and hasn't asked for machine-readable JSON. Otherwise the spinner and
        # ANSI control codes corrupt the JSON payload that consumers expect on
        # stdout (pipes, redirection, --json, CI logs).
        # --evaluate forces the non-streaming path because the streaming runner
        # does not yet wire opensre_evaluate scoring through the renderer.
        stream_to_stdout = (
            sys.stdout.isatty() and not is_json_output() and output is None and not evaluate
        )
        if stream_to_stdout:
            result = run_investigation_cli_streaming(raw_alert=payload)
        else:
            result = run_investigation_cli(raw_alert=payload, opensre_evaluate=evaluate)
        write_json(result, output)
    except SystemExit:
        raise
    except Exception:
        capture_investigation_failed()
        raise

    capture_investigation_completed()
    raise SystemExit(SUCCESS)


def _run_service_investigation(
    *,
    service: str,
    slack_thread: str | None,
    other_inputs: dict[str, object],
    output: str | None,
) -> None:
    """Run a runtime investigation for a deployed service by name."""
    import os

    from app.cli.args import write_json
    from app.cli.errors import OpenSREError
    from app.cli.investigate import run_investigation_cli
    from app.remote.runtime_alert import build_runtime_alert_payload

    conflicting = [
        flag
        for flag, value in (
            ("--input", other_inputs.get("input_path")),
            ("--input-json", other_inputs.get("input_json")),
            ("--interactive", other_inputs.get("interactive")),
            ("--print-template", other_inputs.get("print_template")),
        )
        if value
    ]
    if conflicting:
        raise OpenSREError(
            f"--service cannot be combined with {', '.join(conflicting)}.",
            suggestion="Run 'opensre investigate --service <name>' on its own.",
        )

    slack_bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if slack_thread and not slack_bot_token:
        raise OpenSREError(
            "--slack-thread was provided but SLACK_BOT_TOKEN is not set.",
            suggestion="Export SLACK_BOT_TOKEN=xoxb-... in your environment and retry.",
        )

    capture_investigation_started(input_path=None, input_json=None, interactive=False)
    try:
        raw_alert = build_runtime_alert_payload(
            service,
            slack_thread_ref=slack_thread,
            slack_bot_token=slack_bot_token or None,
        )
        _eval = bool(other_inputs.get("evaluate"))
        result = run_investigation_cli(
            raw_alert=raw_alert,
            alert_name=raw_alert.get("alert_name"),
            pipeline_name=raw_alert.get("pipeline_name"),
            severity=raw_alert.get("severity"),
            opensre_evaluate=_eval,
        )
    except Exception:
        capture_investigation_failed()
        raise

    capture_investigation_completed()
    write_json(result, output)
    raise SystemExit(SUCCESS)
