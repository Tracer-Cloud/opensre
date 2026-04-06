"""Remote agent CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from app.remote.client import RemoteAgentClient


def _context_value(ctx: click.Context, key: str) -> str | None:
    raw_value = ctx.obj.get(key) if ctx.obj else None
    return raw_value if isinstance(raw_value, str) and raw_value else None


def _remote_style(questionary: Any) -> Any:
    return questionary.Style(
        [
            ("qmark", "fg:cyan bold"),
            ("question", "bold"),
            ("answer", "fg:cyan bold"),
            ("pointer", "fg:cyan bold"),
            ("highlighted", "fg:cyan bold"),
        ]
    )


def _load_remote_client(ctx: click.Context, *, missing_url_hint: str) -> RemoteAgentClient:
    from app.cli.wizard.store import load_remote_url
    from app.remote.client import RemoteAgentClient

    resolved_url = _context_value(ctx, "url") or load_remote_url()
    if not resolved_url:
        raise click.ClickException(f"No remote URL configured. {missing_url_hint}")

    return RemoteAgentClient(resolved_url, api_key=_context_value(ctx, "api_key"))


def _save_remote_base_url(client: RemoteAgentClient) -> None:
    from app.cli.wizard.store import save_remote_url

    save_remote_url(client.base_url)


def _parse_alert_json(alert_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(alert_json)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid alert JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise click.ClickException("Invalid alert JSON: expected a JSON object.")
    return payload


def _sample_alert_payload() -> dict[str, str]:
    from app.remote.client import SYNTHETIC_ALERT

    return {
        "alert_name": "etl-daily-orders-failure",
        "pipeline_name": "etl_daily_orders",
        "severity": "critical",
        "message": SYNTHETIC_ALERT,
    }


def _run_remote_interactive(ctx: click.Context) -> None:
    import questionary
    from rich.console import Console

    from app.cli.wizard.store import load_remote_url, save_remote_url

    console = Console(highlight=False)
    url = _context_value(ctx, "url") or load_remote_url()
    status = f"  connected to {url}" if url else "  no remote URL configured"
    style = _remote_style(questionary)

    console.print()
    console.print(f"  [bold cyan]Remote Agent[/bold cyan]  {status}")
    console.print()

    action = questionary.select(
        "What would you like to do?",
        choices=[
            questionary.Choice("Check health", value="health"),
            questionary.Choice("Run investigation (custom alert)", value="investigate"),
            questionary.Choice("Run investigation (sample alert)", value="investigate-sample"),
            questionary.Choice("List investigations", value="list"),
            questionary.Choice("Pull investigation reports", value="pull"),
            questionary.Choice("Configure remote URL", value="configure"),
            questionary.Separator(),
            questionary.Choice("Exit", value="exit"),
        ],
        style=style,
    ).ask()

    if action is None or action == "exit":
        return

    if action == "configure":
        new_url = questionary.text("Remote URL:", default=url or "", style=style).ask()
        if new_url:
            save_remote_url(new_url)
            click.echo(f"  Saved: {new_url}")
        return

    if action == "health":
        ctx.invoke(remote_health)
        return

    if action == "investigate":
        alert_input = questionary.text("Alert JSON payload:", style=style).ask()
        if alert_input:
            ctx.invoke(remote_investigate, alert_json=alert_input, sample=False)
        else:
            click.echo("  No payload provided.")
        return

    if action == "investigate-sample":
        click.echo("  Using sample alert: etl-daily-orders-failure (critical)")
        ctx.invoke(remote_investigate, alert_json=json.dumps(_sample_alert_payload()), sample=False)
        return

    if action == "list":
        ctx.invoke(remote_pull, latest=False, pull_all=False, output_dir="./investigations")
        return

    mode = questionary.select(
        "Which investigations?",
        choices=[
            questionary.Choice("Latest only", value="latest"),
            questionary.Choice("All", value="all"),
        ],
        style=style,
    ).ask()
    if mode == "latest":
        ctx.invoke(remote_pull, latest=True, pull_all=False, output_dir="./investigations")
    elif mode == "all":
        ctx.invoke(remote_pull, latest=False, pull_all=True, output_dir="./investigations")


@click.group(name="remote", invoke_without_command=True)
@click.option("--url", default=None, help="Remote agent base URL (e.g. 1.2.3.4 or http://host:2024).")
@click.option("--api-key", default=None, envvar="OPENSRE_API_KEY", help="API key for the remote agent.")
@click.pass_context
def remote(ctx: click.Context, url: str | None, api_key: str | None) -> None:
    """Connect to and trigger a remote deployed agent."""
    ctx.ensure_object(dict)
    ctx.obj["url"] = url
    ctx.obj["api_key"] = api_key

    if ctx.invoked_subcommand is None:
        _run_remote_interactive(ctx)


@remote.command(name="health")
@click.pass_context
def remote_health(ctx: click.Context) -> None:
    """Check the health of a remote deployed agent."""
    import httpx

    client = _load_remote_client(
        ctx,
        missing_url_hint="Pass a URL or run 'opensre remote health <url>'.",
    )
    try:
        click.echo(json.dumps(client.health(), indent=2))
        _save_remote_base_url(client)
    except httpx.TimeoutException as exc:
        raise click.ClickException(f"Connection timed out reaching {client.base_url}.") from exc
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Health check failed: {exc}") from exc


@remote.command(name="trigger")
@click.option("--alert-json", default=None, help="Inline alert JSON payload string.")
@click.pass_context
def remote_trigger(ctx: click.Context, alert_json: str | None) -> None:
    """Trigger an investigation on a remote deployed agent and stream results."""
    import httpx

    from app.remote.renderer import StreamRenderer

    client = _load_remote_client(
        ctx,
        missing_url_hint="Pass a URL or run 'opensre remote trigger <url>'.",
    )
    try:
        events = client.trigger_investigation(
            _parse_alert_json(alert_json) if alert_json else None
        )
        StreamRenderer().render_stream(events)
        _save_remote_base_url(client)
    except httpx.TimeoutException as exc:
        raise click.ClickException(f"Connection timed out reaching {client.base_url}.") from exc
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Remote investigation failed: {exc}") from exc


@remote.command(name="investigate")
@click.option("--alert-json", default=None, help="Inline alert JSON payload string.")
@click.option("--sample", is_flag=True, default=False, help="Use the built-in sample alert payload.")
@click.pass_context
def remote_investigate(ctx: click.Context, alert_json: str | None, sample: bool) -> None:
    """Run an investigation on the lightweight remote server."""
    import httpx

    client = _load_remote_client(
        ctx,
        missing_url_hint="Pass --url or run 'opensre remote health <url>'.",
    )

    if alert_json:
        raw_alert = _parse_alert_json(alert_json)
    elif sample:
        raw_alert = _sample_alert_payload()
        click.echo("  Using sample alert: etl-daily-orders-failure (critical)")
    else:
        raise click.ClickException("Provide --alert-json or --sample.")

    click.echo("Sending investigation request (this may take a few minutes)...")
    try:
        result = client.investigate(raw_alert)
        _save_remote_base_url(client)
    except httpx.TimeoutException as exc:
        raise click.ClickException(f"Connection timed out: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Remote investigation failed: {exc}") from exc

    click.echo(f"\n  Investigation ID: {result.get('id', 'N/A')}")
    root_cause = str(result.get("root_cause", ""))
    if root_cause:
        click.echo(f"\n  Root Cause:\n  {root_cause}")
    report = str(result.get("report", ""))
    if report:
        click.echo(f"\n  Report:\n  {report}")


@remote.command(name="pull")
@click.option("--latest", is_flag=True, default=False, help="Download only the most recent investigation.")
@click.option("--all", "pull_all", is_flag=True, default=False, help="Download all investigations.")
@click.option("--output-dir", default="./investigations", help="Directory to save .md files to.")
@click.pass_context
def remote_pull(ctx: click.Context, latest: bool, pull_all: bool, output_dir: str) -> None:
    """Download investigation .md files from the remote server."""
    import httpx

    client = _load_remote_client(
        ctx,
        missing_url_hint="Pass --url or run 'opensre remote health <url>'.",
    )
    try:
        investigations = client.list_investigations()
        _save_remote_base_url(client)
    except httpx.TimeoutException as exc:
        raise click.ClickException(f"Connection timed out: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Failed to list investigations: {exc}") from exc

    if not investigations:
        click.echo("No investigations found on the remote server.")
        return

    if not latest and not pull_all:
        click.echo(f"Found {len(investigations)} investigation(s):\n")
        for investigation in investigations:
            click.echo(f"  {investigation['id']}  ({investigation.get('created_at', '?')})")
        click.echo("\nUse --latest or --all to download, or run:\n  opensre remote pull --latest")
        return

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for investigation in investigations[:1] if latest else investigations:
        investigation_id = investigation["id"]
        try:
            content = client.get_investigation(investigation_id)
            destination = output_path / f"{investigation_id}.md"
            destination.write_text(content, encoding="utf-8")
            click.echo(f"  Downloaded: {destination}")
        except Exception as exc:  # noqa: BLE001
            click.echo(f"  Failed to download {investigation_id}: {exc}", err=True)
