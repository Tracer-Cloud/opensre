"""Remote agent CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from app.cli.commands.remote_health import _save_remote_base_url, run_remote_health_check
from app.cli.context import is_json_output, is_yes
from app.cli.errors import OpenSREError

if TYPE_CHECKING:
    from app.remote.client import PreflightResult, RemoteAgentClient


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


def _browse_investigations(ctx: click.Context, style: Any, questionary: Any, console: Any) -> None:
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


def _run_preflight(url: str, api_key: str | None, console: Any) -> PreflightResult:
    """Run a preflight check with a live status indicator."""
    from rich.status import Status

    from app.remote.client import RemoteAgentClient

    client = RemoteAgentClient(url, api_key=api_key)
    with Status("  Connecting...", console=console, spinner="dots"):
        return client.preflight()


def _preflight_payload(preflight: PreflightResult) -> dict[str, Any]:
    """Serialize preflight results for JSON output."""
    return {
        "ok": preflight.ok,
        "version": preflight.version,
        "server_type": preflight.server_type,
        "endpoints": preflight.endpoints,
        "latency_ms": preflight.latency_ms,
        "error": preflight.error,
        "system": preflight.system,
        "supports_investigate": preflight.supports_investigate,
        "supports_stream": preflight.supports_stream,
        "supports_live_stream": preflight.supports_live_stream,
        "supports_langgraph": preflight.supports_langgraph,
        "status": preflight.status_label,
    }


def _render_preflight_status(
    url: str,
    label: str,
    preflight: PreflightResult | None,
    console: Any,
) -> None:
    """Print a rich one-liner showing connection health."""
    if preflight is None:
        console.print("  [bold cyan]Remote Agent[/bold cyan]  [dim]no remote URL configured[/dim]")
        return

    base = f"[bold]{url}[/bold] [dim]({label})[/dim]"

    if not preflight.ok:
        console.print(f"  [bold cyan]Remote Agent[/bold cyan]  [red]●[/red] {base}")
        console.print(f"  [red]{preflight.error}[/red]")
        return

    parts = [f"v{preflight.version}"] if preflight.version else []
    parts.append(f"{preflight.latency_ms}ms")
    if preflight.supports_live_stream:
        parts.append("stream")
    elif preflight.supports_investigate:
        parts.append("stream-unavailable")
    if preflight.supports_langgraph:
        parts.append("langgraph")
    detail = "  ".join(parts)

    dot = "[green]●[/green]" if preflight.status_label == "healthy" else "[yellow]●[/yellow]"
    console.print(f"  [bold cyan]Remote Agent[/bold cyan]  {dot} {base}  {detail}")

    sys_metrics = preflight.system
    if sys_metrics:
        metric_parts: list[str] = []
        cpu = sys_metrics.get("cpu")
        if cpu:
            metric_parts.append(f"load: {cpu['load_avg_1m']}")
        mem = sys_metrics.get("memory")
        if mem:
            metric_parts.append(f"mem: {mem['percent']}%")
        disk = sys_metrics.get("disk")
        if disk:
            metric_parts.append(f"disk: {disk['percent']}%")
        uptime = sys_metrics.get("uptime")
        if uptime:
            metric_parts.append(f"up: {uptime['human']}")
        if metric_parts:
            console.print(f"  [dim]{' │ '.join(metric_parts)}[/dim]")

    if preflight.supports_investigate and not preflight.supports_live_stream:
        console.print("  [yellow]Live investigation streaming unavailable on this remote.[/yellow]")
        console.print(
            "  [dim]Redeploy the latest remote server to stream LangGraph step events.[/dim]"
        )


def _render_health_with_preflight(preflight: PreflightResult, base_url: str, console: Any) -> None:
    """Render health using the already-gathered preflight result."""
    from rich.panel import Panel
    from rich.table import Table

    header = Table.grid(padding=(0, 2))
    header.add_row("[bold]URL[/bold]", base_url)
    if preflight.version:
        header.add_row("[bold]Version[/bold]", preflight.version)

    st = preflight.server_type
    if st == "lightweight":
        st_display = "[green]lightweight[/green]"
    elif st == "langgraph":
        st_display = "[cyan]langgraph[/cyan]"
    else:
        st_display = f"[yellow]{st}[/yellow]"
    header.add_row("[bold]Server type[/bold]", st_display)

    if preflight.endpoints:
        header.add_row("[bold]Endpoints[/bold]", ", ".join(preflight.endpoints))

    header.add_row("[bold]Latency[/bold]", f"{preflight.latency_ms}ms")
    if preflight.supports_live_stream:
        header.add_row("[bold]Live events[/bold]", "[green]available[/green]")
    elif preflight.supports_investigate:
        header.add_row("[bold]Live events[/bold]", "[yellow]unavailable[/yellow]")
        header.add_row(
            "[bold]Action[/bold]",
            "Redeploy the latest remote server to stream LangGraph steps.",
        )

    if not preflight.ok:
        header.add_row("[bold]Status[/bold]", f"[red]{preflight.error}[/red]")

    console.print(
        Panel(header, title="[bold cyan]Remote Agent Health[/bold cyan]", border_style="cyan")
    )


def _render_health_rich(data: dict[str, Any], base_url: str) -> None:
    """Render health-check JSON as a Rich panel with system metrics table."""
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console(highlight=False)
    console.print()

    version = data.get("version", "unknown")
    server_type = data.get("server_type", "unknown")
    endpoints = data.get("endpoints") or []

    header = Table.grid(padding=(0, 2))
    header.add_row("[bold]URL[/bold]", base_url)
    header.add_row("[bold]Version[/bold]", version)
    header.add_row("[bold]Server type[/bold]", server_type)
    header.add_row("[bold]Endpoints[/bold]", ", ".join(endpoints) or "none")
    console.print(
        Panel(header, title="[bold cyan]Remote Agent Health[/bold cyan]", border_style="cyan")
    )

    system = data.get("system")
    if not system:
        console.print()
        return

    table = Table(box=box.SIMPLE_HEAVY, show_header=True, title="System Metrics")
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value")

    cpu = system.get("cpu")
    if cpu:
        cores = cpu.get("core_count", "?")
        table.add_row(
            "CPU Load (1/5/15m)",
            f"{cpu.get('load_avg_1m', '?')} / {cpu.get('load_avg_5m', '?')}"
            f" / {cpu.get('load_avg_15m', '?')}  ({cores} cores)",
        )

    mem = system.get("memory")
    if mem:
        pct = mem.get("percent", 0)
        color = "green" if pct < 70 else ("yellow" if pct < 90 else "red")
        table.add_row(
            "Memory",
            f"[{color}]{pct}%[/{color}]  "
            f"{mem.get('used_gb', '?')}GB / {mem.get('total_gb', '?')}GB"
            f"  ({mem.get('available_gb', '?')}GB free)",
        )

    disk = system.get("disk")
    if disk:
        pct = disk.get("percent", 0)
        color = "green" if pct < 70 else ("yellow" if pct < 90 else "red")
        table.add_row(
            "Disk",
            f"[{color}]{pct}%[/{color}]  "
            f"{disk.get('used_gb', '?')}GB / {disk.get('total_gb', '?')}GB"
            f"  ({disk.get('free_gb', '?')}GB free)",
        )

    uptime = system.get("uptime")
    if uptime:
        table.add_row("Uptime", uptime.get("human", "?"))

    plat = system.get("platform")
    if plat:
        table.add_row("OS", plat.get("os", "?"))
        table.add_row("Arch", plat.get("arch", "?"))
        table.add_row("Python", plat.get("python", "?"))
        table.add_row("Hostname", plat.get("hostname", "?"))

    proc = system.get("process")
    if proc:
        parts: list[str] = []
        if "rss_mb" in proc:
            parts.append(f"RSS {proc['rss_mb']}MB")
        if "open_fds" in proc:
            parts.append(f"{proc['open_fds']} open fds")
        if parts:
            table.add_row("Server Process", " │ ".join(parts))

    console.print(table)
    console.print()


def _build_investigation_choices(
    preflight: PreflightResult | None,
    questionary: Any,
) -> list[Any]:
    """Build investigation menu items adapted to server capabilities."""
    if preflight and not preflight.ok:
        return [
            questionary.Choice(
                "Run investigation (sample alert)  [unavailable]",
                value="investigate-sample",
                disabled="server unreachable",
            ),
        ]

    if preflight and preflight.supports_langgraph and not preflight.supports_stream:
        return [
            questionary.Choice("Run investigation (custom alert)", value="investigate-langgraph"),
            questionary.Choice(
                "Run investigation (sample alert)", value="investigate-sample-langgraph"
            ),
        ]

    if preflight and not preflight.supports_stream and preflight.supports_investigate:
        return [
            questionary.Choice(
                "Run investigation (custom alert)  [stream required]",
                value="investigate",
                disabled="redeploy remote to enable live event streaming",
            ),
            questionary.Choice(
                "Run investigation (sample alert)  [stream required]",
                value="investigate-sample",
                disabled="redeploy remote to enable live event streaming",
            ),
        ]

    return [
        questionary.Choice("Run investigation (custom alert)", value="investigate"),
        questionary.Choice("Run investigation (sample alert)", value="investigate-sample"),
    ]


def _managed_ec2_deployment_status(
    url: str | None,
    label: str | None,
) -> dict[str, str]:
    """Return the managed EC2 deployment status for the selected remote, if any."""
    from app.cli.commands.deploy import _get_deployment_status
    from app.remote.client import normalize_url

    status = _get_deployment_status()
    if not status.get("ip"):
        return {}

    managed_url = normalize_url(f"http://{status['ip']}:{status.get('port', '8080')}")
    if label == "ec2":
        return {**status, "url": managed_url}
    if url and normalize_url(url) == managed_url:
        return {**status, "url": managed_url}
    return {}


def _build_deploy_choices(
    managed_status: dict[str, str],
    preflight: PreflightResult | None,
    questionary: Any,
) -> list[Any]:
    """Build deploy-related menu items for managed remotes."""
    if not managed_status:
        return []

    label = "Redeploy remote (EC2)"
    if preflight and preflight.supports_investigate and not preflight.supports_live_stream:
        label = "Redeploy remote (enable streaming)"

    return [
        questionary.Separator("─── Deploy"),
        questionary.Choice(label, value="redeploy-ec2"),
    ]


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

    if not explicit_url and len(remotes) > 1:
        url = _pick_remote(remotes, active_name, style, questionary, console)
        if url is None:
            return
        ctx.obj["url"] = url
        for name, remote_url in remotes.items():
            if remote_url == url:
                set_active_remote(name)
                active_name = name
                break

    label = active_name or "custom"
    if url:
        for name, remote_url in remotes.items():
            if remote_url == url:
                label = name
                break

    preflight: PreflightResult | None = None
    if url:
        preflight = _run_preflight(url, _context_value(ctx, "api_key"), console)

    console.print()
    _render_preflight_status(url or "", label, preflight, console)
    console.print()

    while True:
        configure_choices: list[Any] = [
            questionary.Choice("Add new remote", value="configure-add"),
        ]
        if len(remotes) > 1:
            configure_choices.append(
                questionary.Choice("Switch active remote", value="configure-switch"),
            )

        investigation_choices = _build_investigation_choices(preflight, questionary)
        managed_deployment = _managed_ec2_deployment_status(url, label)
        deploy_choices = _build_deploy_choices(managed_deployment, preflight, questionary)

        can_list = not preflight or preflight.ok
        list_choices: list[Any] = []
        if can_list:
            list_choices = [
                questionary.Choice("List investigations", value="list"),
                questionary.Choice("Pull investigation reports", value="pull"),
            ]

        action = questionary.select(
            "What would you like to do?",
            choices=[
                questionary.Choice("Check health", value="health"),
                *investigation_choices,
                *list_choices,
                *deploy_choices,
                questionary.Separator("─── Configure"),
                *configure_choices,
                questionary.Separator(),
                questionary.Choice("Exit", value="exit"),
            ],
            style=style,
        ).ask()

        if action is None or action == "exit":
            return

        if action == "redeploy-ec2":
            from app.cli.commands.deploy import _prompt_deploy_branch, _redeploy_ec2

            branch = _prompt_deploy_branch(questionary, style)
            if branch is None:
                continue

            confirmation = f"Tear down current EC2 remote and redeploy from '{branch}'?"
            if preflight and preflight.supports_investigate and not preflight.supports_live_stream:
                confirmation = (
                    f"Tear down current EC2 remote and redeploy from '{branch}' "
                    "to restore live investigation streaming?"
                )

            if not questionary.confirm(
                confirmation,
                default=False,
                style=style,
            ).ask():
                console.print("  [dim]Cancelled.[/dim]")
                console.print()
                continue

            _redeploy_ec2(ctx, branch=branch, console=console)
            explicit_url = None
            remotes = load_named_remotes()
            active_name = load_active_remote_name()
            url = load_remote_url()
            if url:
                ctx.obj["url"] = url

            label = active_name or "custom"
            if url:
                for name, remote_url in remotes.items():
                    if remote_url == url:
                        label = name
                        break
                preflight = _run_preflight(url, _context_value(ctx, "api_key"), console)
                console.print()
                _render_preflight_status(url, label, preflight, console)
            else:
                preflight = None
            console.print()
            continue

        if action == "configure-add":
            name = questionary.text("Remote name (e.g. staging, local):", style=style).ask()
            if not name:
                continue
            new_url = questionary.text("Remote URL:", default="", style=style).ask()
            if not new_url:
                continue
            make_active = questionary.confirm(
                "Set as active remote?", default=True, style=style
            ).ask()
            save_named_remote(name, new_url, set_active=bool(make_active), source="manual")
            if make_active:
                console.print(f"  Saved and activated: [bold]{name}[/bold] → {new_url}")
            else:
                console.print(f"  Saved: [bold]{name}[/bold] → {new_url}")
            remotes = load_named_remotes()
            continue

        if action == "configure-switch":
            switched_url = _pick_remote(remotes, active_name, style, questionary, console)
            if switched_url:
                for name, remote_url in remotes.items():
                    if remote_url == switched_url:
                        set_active_remote(name)
                        active_name = name
                        console.print(f"  Active remote: [bold]{name}[/bold] → {switched_url}")
                        break
                url = switched_url
                ctx.obj["url"] = url
                preflight = _run_preflight(url, _context_value(ctx, "api_key"), console)
                console.print()
                _render_preflight_status(url, name, preflight, console)
            console.print()
            continue

        if action == "health":
            if preflight and url:
                _render_health_with_preflight(preflight, url, console)
            else:
                ctx.invoke(remote_health)
            console.print()
            continue

        if action == "investigate":
            alert_input = questionary.text("Alert JSON payload:", style=style).ask()
            if not alert_input:
                click.echo("  No payload provided.")
                continue
            _run_streamed_investigation(ctx, _parse_alert_json(alert_input))
            continue

        if action == "investigate-sample":
            click.echo("  Using sample alert: etl-daily-orders-failure (critical)")
            _run_streamed_investigation(ctx, _sample_alert_payload())
            continue

        if action in ("investigate-langgraph", "investigate-sample-langgraph"):
            if action == "investigate-langgraph":
                alert_input = questionary.text("Alert JSON payload:", style=style).ask()
                if not alert_input:
                    click.echo("  No payload provided.")
                    continue
                payload = _parse_alert_json(alert_input)
            else:
                click.echo("  Using sample alert: etl-daily-orders-failure (critical)")
                payload = _sample_alert_payload()
            _run_langgraph_investigation(ctx, payload)
            continue

        if action == "list":
            _browse_investigations(ctx, style, questionary, console)
            continue

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
        console.print()


def _pick_remote(
    remotes: dict[str, str],
    active_name: str | None,
    style: Any,
    questionary: Any,
    console: Any,
) -> str | None:
    """Prompt the user to select from saved remotes. Returns the chosen URL."""
    choices: list[Any] = []
    default_url: str | None = None
    for name, url in remotes.items():
        suffix = "  ← active" if name == active_name else ""
        choices.append(questionary.Choice(f"{name}  ({url}){suffix}", value=url))
        if name == active_name:
            default_url = url

    console.print()
    console.print("  [bold cyan]Remote Agent[/bold cyan]  multiple remotes configured")
    console.print()

    selected: str | None = questionary.select(
        "Which remote?",
        choices=choices,
        default=default_url,
        style=style,
    ).ask()
    return selected


def _run_streamed_investigation(ctx: click.Context, raw_alert: dict[str, Any]) -> None:
    """Stream an investigation from the remote server with live terminal UI.

    Catches 404 on ``/investigate/stream`` and switches to the
    LangGraph trigger path when appropriate.
    """
    import httpx

    from app.remote.renderer import StreamRenderer

    client = _load_remote_client(
        ctx,
        missing_url_hint="Pass --url or run 'opensre remote health <url>'.",
    )
    try:
        events = client.stream_investigate(raw_alert)
        StreamRenderer().render_stream(events)
        _save_remote_base_url(client)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            _handle_stream_404(ctx, client, raw_alert)
            return
        raise OpenSREError(
            f"Remote investigation failed: HTTP {exc.response.status_code}",
            suggestion="Run 'opensre remote health' to verify the remote agent.",
        ) from exc
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


def _handle_stream_404(
    ctx: click.Context,
    client: RemoteAgentClient,
    raw_alert: dict[str, Any],
) -> None:
    """Diagnose a 404 on ``/investigate/stream`` and keep streaming paths only."""
    from rich.console import Console

    console = Console(highlight=False)
    preflight = client.preflight()

    if preflight.supports_langgraph:
        console.print(
            "  [yellow]Streaming endpoint not available — LangGraph deployment detected.[/yellow]"
        )
        console.print("  [dim]Auto-switching to LangGraph trigger path...[/dim]")
        console.print()
        _run_langgraph_investigation(ctx, raw_alert)
        return

    if preflight.ok and preflight.supports_investigate:
        version_hint = f" (v{preflight.version})" if preflight.version else ""
        raise OpenSREError(
            f"Live investigation streaming is unavailable on this server{version_hint}.",
            suggestion=(
                "Redeploy the latest remote server to stream LangGraph step events. "
                "Use 'opensre remote investigate --no-stream' only if you explicitly "
                "want the legacy blocking request."
            ),
        )

    version_hint = f" (v{preflight.version})" if preflight.version else ""
    raise OpenSREError(
        f"Endpoint /investigate/stream not found on server{version_hint}.",
        suggestion=(
            "The remote server may need updating. "
            "Redeploy with the latest version or use 'opensre remote trigger'."
        ),
    )


def _run_langgraph_investigation(ctx: click.Context, raw_alert: dict[str, Any]) -> None:
    """Run an investigation through the LangGraph ``/threads`` API.

    If ``/threads`` returns 404 (misdetected server type), falls back to
    the lightweight streaming path automatically.
    """
    import httpx

    from app.remote.renderer import StreamRenderer

    client = _load_remote_client(
        ctx,
        missing_url_hint="Pass --url or run 'opensre remote health <url>'.",
    )
    try:
        events = client.trigger_investigation(raw_alert)
        StreamRenderer().render_stream(events)
        _save_remote_base_url(client)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            from rich.console import Console

            console = Console(highlight=False)
            console.print("  [yellow]LangGraph endpoint not available on this server.[/yellow]")
            console.print("  [dim]Falling back to lightweight server path...[/dim]")
            console.print()
            _run_streamed_investigation(ctx, raw_alert)
            return
        raise OpenSREError(
            f"Remote investigation failed: HTTP {exc.response.status_code}",
            suggestion="Run 'opensre remote health' to verify the remote agent.",
        ) from exc
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


@click.group(name="remote", invoke_without_command=True)
@click.option(
    "--url", default=None, help="Remote agent base URL (e.g. 1.2.3.4 or http://host:2024)."
)
@click.option(
    "--api-key", default=None, envvar="OPENSRE_API_KEY", help="API key for the remote agent."
)
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
@click.option(
    "--json", "output_json", is_flag=True, help="Print machine-readable JSON health report."
)
@click.pass_context
def remote_health(ctx: click.Context, output_json: bool) -> None:
    """Check the health of a remote deployed agent."""
    client = _load_remote_client(
        ctx,
        missing_url_hint="Pass a URL or run 'opensre remote health <url>'.",
    )
    run_remote_health_check(
        base_url=client.base_url,
        api_key=_context_value(ctx, "api_key"),
        output_json=output_json,
        save_url=True,
        client=client,
    )


@remote.command(name="trigger")
@click.option("--alert-json", default=None, help="Inline alert JSON payload string.")
@click.option("--detach", is_flag=True, help="Fire the investigation and return immediately.")
@click.pass_context
def remote_trigger(ctx: click.Context, alert_json: str | None, detach: bool) -> None:
    """Trigger an investigation on a remote deployed agent and stream results."""
    import httpx

    from app.remote.renderer import StreamRenderer

    client = _load_remote_client(
        ctx,
        missing_url_hint="Pass --url or run 'opensre remote trigger --url <host>'.",
    )
    if detach:
        click.echo("Detach mode is not yet supported; streaming inline.")
    try:
        events = client.trigger_investigation(_parse_alert_json(alert_json) if alert_json else None)
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
@click.option(
    "--sample", is_flag=True, default=False, help="Use the built-in sample alert payload."
)
@click.option(
    "--no-stream",
    is_flag=True,
    default=False,
    help="Use blocking /investigate instead of live streaming.",
)
@click.pass_context
def remote_investigate(
    ctx: click.Context, alert_json: str | None, sample: bool, no_stream: bool
) -> None:
    """Run an investigation on the lightweight remote server.

    \b
    By default the investigation streams live progress (tool calls,
    reasoning steps) to the terminal.  Use --no-stream for a blocking
    request that prints the result once complete.
    """
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

    if no_stream:
        _run_blocking_investigation(ctx, raw_alert)
    else:
        _run_streamed_investigation(ctx, raw_alert)


def _run_blocking_investigation(ctx: click.Context, raw_alert: dict[str, Any]) -> None:
    """Run an investigation using the blocking /investigate endpoint."""
    import httpx

    client = _load_remote_client(
        ctx,
        missing_url_hint="Pass --url or run 'opensre remote health <url>'.",
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
@click.option(
    "--latest", is_flag=True, default=False, help="Download only the most recent investigation."
)
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
