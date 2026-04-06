"""Remote agent CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from app.cli.context import is_json_output, is_yes
from app.cli.errors import OpenSREError

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
        raise OpenSREError(
            "No remote URL configured.",
            suggestion=missing_url_hint,
            docs_url="https://github.com/Tracer-Cloud/opensre#remote-agent",
        )

    return RemoteAgentClient(resolved_url, api_key=_context_value(ctx, "api_key"))


def _save_remote_base_url(client: RemoteAgentClient) -> None:
    from app.cli.wizard.store import save_remote_url

    save_remote_url(client.base_url)

    from app.cli.wizard.store import load_named_remotes, save_named_remote

    remotes = load_named_remotes()
    if client.base_url not in remotes.values():
        save_named_remote("custom", client.base_url, set_active=True, source="cli")


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


def _resolve_active_url(ctx: click.Context) -> str | None:
    """Return the active remote URL, preferring --url flag over the store."""
    from app.cli.wizard.store import load_remote_url

    return _context_value(ctx, "url") or load_remote_url()


def _browse_investigations(
    ctx: click.Context, style: Any, questionary: Any, console: Any
) -> None:
    """Fetch remote investigations and let the user pick one to view."""
    import httpx

    client = _load_remote_client(
        ctx,
        missing_url_hint="Pass --url or run 'opensre remote health <url>'.",
    )
    try:
        investigations = client.list_investigations()
        _save_remote_base_url(client)
    except httpx.TimeoutException as exc:
        raise OpenSREError(
            f"Connection timed out: {exc}",
            suggestion="Check network connectivity and verify the remote agent is running.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise OpenSREError(
            f"Failed to list investigations: {exc}",
            suggestion="Run 'opensre remote health' to verify the remote agent.",
        ) from exc

    if not investigations:
        console.print("  [dim]No investigations found on the remote server.[/dim]")
        return

    while True:
        console.print()
        console.print(f"  [bold cyan]Investigations[/bold cyan]  {len(investigations)} available")
        console.print()

        choices = [
            questionary.Choice(
                f"{inv['id']}  ({inv.get('created_at', '?')})",
                value=inv["id"],
            )
            for inv in investigations
        ]
        choices.append(questionary.Separator())
        choices.append(questionary.Choice("← Back", value="_back"))

        selected = questionary.select(
            "Select an investigation to view:",
            choices=choices,
            style=style,
        ).ask()

        if selected is None or selected == "_back":
            return

        console.print()
        console.print(f"  [bold]Loading {selected}...[/bold]")

        try:
            content = client.get_investigation(selected)
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [red]Failed to load: {exc}[/red]")
            continue

        console.print()
        for line in content.strip().splitlines():
            console.print(f"  {line}")
        console.print()

        after = questionary.select(
            "",
            choices=[
                questionary.Choice("← Back to list", value="back"),
                questionary.Choice("Save to file", value="save"),
                questionary.Choice("Exit", value="exit"),
            ],
            style=style,
        ).ask()

        if after == "save":
            out_dir = Path("./investigations")
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / f"{selected}.md"
            dest.write_text(content, encoding="utf-8")
            console.print(f"  [green]Saved:[/green] {dest}")

        if after is None or after == "exit":
            return


def _run_remote_interactive(ctx: click.Context) -> None:
    import questionary
    from rich.console import Console

    from app.cli.wizard.store import (
        load_active_remote_name,
        load_named_remotes,
        load_remote_url,
        save_named_remote,
        set_active_remote,
    )

    console = Console(highlight=False)
    style = _remote_style(questionary)

    explicit_url = _context_value(ctx, "url")
    url = explicit_url or load_remote_url()
    remotes = load_named_remotes()
    active_name = load_active_remote_name()

    # If multiple remotes exist and no --url flag, let the user pick
    if not explicit_url and len(remotes) > 1:
        url = _pick_remote(remotes, active_name, style, questionary, console)
        if url is None:
            return
        ctx.obj["url"] = url

    if url:
        label = active_name or "custom"
        for name, remote_url in remotes.items():
            if remote_url == url:
                label = name
                break
        status = f"connected to [bold]{url}[/bold] [dim]({label})[/dim]"
    else:
        status = "[dim]no remote URL configured[/dim]"

    console.print()
    console.print(f"  [bold cyan]Remote Agent[/bold cyan]  {status}")
    console.print()

    configure_choices: list[Any] = [
        questionary.Choice("Add new remote", value="configure-add"),
    ]
    if len(remotes) > 1:
        configure_choices.append(
            questionary.Choice("Switch active remote", value="configure-switch"),
        )

    action = questionary.select(
        "What would you like to do?",
        choices=[
            questionary.Choice("Check health", value="health"),
            questionary.Choice("Run investigation (custom alert)", value="investigate"),
            questionary.Choice("Run investigation (sample alert)", value="investigate-sample"),
            questionary.Choice("List investigations", value="list"),
            questionary.Choice("Pull investigation reports", value="pull"),
            questionary.Separator("─── Configure"),
            *configure_choices,
            questionary.Separator(),
            questionary.Choice("Exit", value="exit"),
        ],
        style=style,
    ).ask()

    if action is None or action == "exit":
        return

    if action == "configure-add":
        name = questionary.text("Remote name (e.g. staging, local):", style=style).ask()
        if not name:
            return
        new_url = questionary.text("Remote URL:", default="", style=style).ask()
        if not new_url:
            return
        make_active = questionary.confirm("Set as active remote?", default=True, style=style).ask()
        save_named_remote(name, new_url, set_active=bool(make_active), source="manual")
        if make_active:
            console.print(f"  Saved and activated: [bold]{name}[/bold] → {new_url}")
        else:
            console.print(f"  Saved: [bold]{name}[/bold] → {new_url}")
        return

    if action == "configure-switch":
        switched_url = _pick_remote(remotes, active_name, style, questionary, console)
        if switched_url:
            for name, remote_url in remotes.items():
                if remote_url == switched_url:
                    set_active_remote(name)
                    console.print(f"  Active remote: [bold]{name}[/bold] → {switched_url}")
                    break
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
        _browse_investigations(ctx, style, questionary, console)
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


def _pick_remote(
    remotes: dict[str, str],
    active_name: str | None,
    style: Any,
    questionary: Any,
    console: Any,
) -> str | None:
    """Prompt the user to select from saved remotes. Returns the chosen URL."""
    choices: list[Any] = []
    for name, url in remotes.items():
        suffix = "  ← active" if name == active_name else ""
        choices.append(questionary.Choice(f"{name}  ({url}){suffix}", value=url))

    console.print()
    console.print("  [bold cyan]Remote Agent[/bold cyan]  multiple remotes configured")
    console.print()

    selected: str | None = questionary.select(
        "Which remote?",
        choices=choices,
        style=style,
    ).ask()
    return selected


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
        if is_yes() or is_json_output():
            raise OpenSREError(
                "No subcommand provided.",
                suggestion=(
                    "Use 'opensre remote health', 'opensre remote trigger', "
                    "'opensre remote investigate', or 'opensre remote pull'."
                ),
            )
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
        data = client.health()
        click.echo(json.dumps(data, indent=2))
        _save_remote_base_url(client)
    except httpx.TimeoutException as exc:
        raise OpenSREError(
            f"Connection timed out reaching {client.base_url}.",
            suggestion="Check network connectivity and verify the remote agent is running.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise OpenSREError(
            f"Health check failed: {exc}",
            suggestion="Verify the remote URL with 'opensre remote --url <host> health'.",
        ) from exc


@remote.command(name="trigger")
@click.option("--alert-json", default=None, help="Inline alert JSON payload string.")
@click.option("--detach", is_flag=True, help="Fire the investigation and return immediately.")
@click.pass_context
def remote_trigger(ctx: click.Context, alert_json: str | None, detach: bool) -> None:
    """Trigger an investigation on a remote deployed agent and stream results."""
    import httpx

    client = _load_remote_client(
        ctx,
        missing_url_hint="Pass --url or run 'opensre remote trigger --url <host>'.",
    )
    try:
        if detach:
            payload = _parse_alert_json(alert_json) if alert_json else _sample_alert_payload()
            result = client.investigate(payload)
            _save_remote_base_url(client)
            inv_id = result.get("id", "N/A")
            if is_json_output():
                click.echo(json.dumps({"id": inv_id, "status": "triggered"}))
            else:
                click.echo(f"  Investigation triggered: {inv_id}")
                click.echo("  Use 'opensre remote pull --latest' to download results.")
            return

        from app.remote.renderer import StreamRenderer

        events = client.trigger_investigation(
            _parse_alert_json(alert_json) if alert_json else None
        )
        StreamRenderer().render_stream(events)
        _save_remote_base_url(client)
    except httpx.TimeoutException as exc:
        raise OpenSREError(
            f"Connection timed out reaching {client.base_url}.",
            suggestion="Check network connectivity and verify the remote agent is running.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise OpenSREError(
            f"Remote investigation failed: {exc}",
            suggestion="Run 'opensre remote health' to verify the remote agent.",
        ) from exc


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
        raise OpenSREError(
            "No alert payload provided.",
            suggestion="Pass --alert-json '{...}' or use --sample for a demo payload.",
        )

    click.echo("Sending investigation request (this may take a few minutes)...")
    try:
        result = client.investigate(raw_alert)
        _save_remote_base_url(client)
    except httpx.TimeoutException as exc:
        raise OpenSREError(
            f"Connection timed out: {exc}",
            suggestion="The remote agent may be overloaded. Try again or check 'opensre remote health'.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise OpenSREError(
            f"Remote investigation failed: {exc}",
            suggestion="Run 'opensre remote health' to verify the remote agent.",
        ) from exc

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
        raise OpenSREError(
            f"Connection timed out: {exc}",
            suggestion="Check network connectivity and verify the remote agent is running.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise OpenSREError(
            f"Failed to list investigations: {exc}",
            suggestion="Run 'opensre remote health' to verify the remote agent.",
        ) from exc

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
