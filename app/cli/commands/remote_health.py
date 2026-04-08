"""Shared helpers for remote deployment health checks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from app.remote.client import RemoteAgentClient


def _save_remote_base_url(client: RemoteAgentClient) -> None:
    from app.cli.wizard.store import save_remote_url

    save_remote_url(client.base_url)

    from app.cli.wizard.store import load_named_remotes, save_named_remote

    remotes = load_named_remotes()
    if client.base_url not in remotes.values():
        save_named_remote("custom", client.base_url, set_active=True, source="cli")


def _human_duration(seconds: int | None) -> str:
    if not isinstance(seconds, int) or seconds < 0:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    minutes, rem = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rem}s"
    hours, rem_minutes = divmod(minutes, 60)
    return f"{hours}h {rem_minutes}m"


def _render_remote_health_report(report: dict[str, Any]) -> None:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    from app.cli.health_view import status_badge

    console = Console(highlight=False)

    remote_version = str(report.get("remote_version", "unknown"))
    local_version = str(report.get("local_version", "unknown"))
    latency_ms = report.get("latency_ms")
    latency_text = f"{latency_ms}ms" if isinstance(latency_ms, int) else "unknown"
    uptime_text = _human_duration(report.get("uptime_seconds"))
    started_at = str(report.get("started_at") or "unknown")
    status_text = str(report.get("status", "unknown"))

    version_style = "green" if remote_version == local_version else "yellow"
    status_style = {
        "passed": "green",
        "warn": "yellow",
        "failed": "red",
    }.get(status_text, "white")

    meta = Table.grid(padding=(0, 1))
    meta.add_row("[bold]Remote URL[/bold]", str(report.get("base_url", "-")))
    meta.add_row("[bold]Public IP[/bold]", str(report.get("public_ip") or "unknown"))
    meta.add_row("[bold]Instance ID[/bold]", str(report.get("instance_id") or "unknown"))
    meta.add_row("[bold]Region[/bold]", str(report.get("region") or "unknown"))
    meta.add_row("[bold]Status[/bold]", Text(status_text.upper(), style=f"bold {status_style}"))
    meta.add_row("[bold]Latency[/bold]", latency_text)
    meta.add_row("[bold]Remote version[/bold]", Text(remote_version, style=f"bold {version_style}"))
    meta.add_row("[bold]Local version[/bold]", local_version)
    meta.add_row("[bold]Uptime[/bold]", uptime_text)
    meta.add_row("[bold]Started at[/bold]", started_at)

    panel = Panel.fit(meta, title="[bold cyan]Remote Health[/bold cyan]", border_style="cyan")
    console.print(panel)
    console.print()

    checks = report.get("checks")
    if isinstance(checks, list) and checks:
        table = Table(title="Checks", box=box.SIMPLE_HEAVY, show_lines=False)
        table.add_column("Check", style="bold cyan")
        table.add_column("Endpoint", style="dim")
        table.add_column("Status")
        table.add_column("Detail")

        for check in checks:
            if not isinstance(check, dict):
                continue
            table.add_row(
                str(check.get("name", "-")),
                str(check.get("endpoint", "-")),
                status_badge(str(check.get("status", "unknown"))),
                str(check.get("detail", "-")),
            )

        console.print(table)

    hints = report.get("hints")
    if isinstance(hints, list) and hints:
        console.print()
        for hint in hints:
            console.print(f"[yellow]- {hint}[/yellow]")


def run_remote_health_check(
    *,
    base_url: str,
    api_key: str | None = None,
    output_json: bool = False,
    save_url: bool = True,
    client: RemoteAgentClient | None = None,
) -> None:
    import httpx
    from rich.console import Console

    from app.version import get_version

    resolved_client = client
    if resolved_client is None:
        from app.remote.client import RemoteAgentClient

        resolved_client = RemoteAgentClient(base_url, api_key=api_key)

    try:
        console = Console(highlight=False)
        with console.status("Checking remote deployment health...", spinner="dots"):
            report = resolved_client.probe_health(local_version=get_version())

        if output_json:
            click.echo(json.dumps(report, indent=2))
        else:
            _render_remote_health_report(report)

        if save_url:
            _save_remote_base_url(resolved_client)
    except httpx.TimeoutException as exc:
        raise click.ClickException(
            "Connection timed out reaching "
            f"{resolved_client.base_url}. Instance may still be starting. Retry in 30s "
            "or check AWS console/system logs."
        ) from exc
    except httpx.ConnectError as exc:
        raise click.ClickException(
            "Could not connect to "
            f"{resolved_client.base_url}. The server process may not be running. "
            "SSH into the instance and check: `systemctl status opensre` and "
            "`cat /var/log/opensre-remote.log`."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"Health check failed: {exc}") from exc
