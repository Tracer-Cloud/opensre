"""Operator commands for durable SigNoz alert investigations."""

from __future__ import annotations

import json
import sys
from typing import Any

import click
from rich.console import Console
from rich.text import Text

from config.constants.signoz import SIGNOZ_API_KEY_ENV
from config.llm_credentials import delete_credential, resolve_env_credential
from core.domain.alerts.triage.storage import TriageStore
from infrastructure.process.runtime_flags import is_json_output
from integrations.catalog import resolve_effective_integrations
from integrations.signoz import connect_source
from surfaces.shared.terminal.triage import print_investigations, print_report


@click.group(name="triage", invoke_without_command=True)
@click.pass_context
def triage_command(ctx: click.Context) -> None:
    """Manage automatic SigNoz alert investigations."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


def _emit(value: Any) -> None:
    if is_json_output():
        click.echo(json.dumps(value, default=str))
    else:
        Console().print(Text(json.dumps(value, indent=2, default=str)))


@triage_command.command(name="connect")
@click.option("--name", default="")
@click.option("--query-url", default="")
@click.option("--services", default="", help="Required comma-separated exact service names.")
@click.option(
    "--ingress-url",
    default="",
    help="Required gateway URL reachable from SigNoz; HTTPS for remote ingress.",
)
@click.option(
    "--api-key-env",
    default=SIGNOZ_API_KEY_ENV,
    show_default=True,
    help="Credential name; the key is never a command argument.",
)
def triage_connect(
    name: str, query_url: str, services: str, ingress_url: str, api_key_env: str
) -> None:
    """Connect query access and display authenticated webhook setup."""
    configured = resolve_effective_integrations().get("signoz", {})
    query_url = query_url or str(configured.get("url", ""))
    values = {
        "name": name,
        "query_url": query_url,
        "services": services,
        "ingress_url": ingress_url,
    }
    if not all(values.values()):
        if not sys.stdin.isatty() or is_json_output():
            raise click.UsageError(
                "Provide --name, --query-url (or configured SigNoz), --services, and --ingress-url."
            )
        from surfaces.shared.terminal.components.command_input import run_command_input
        from surfaces.shared.terminal.triage_input import build_connect_form

        args = run_command_input(build_connect_form(values))
        if args is None:
            return
        values = dict(zip((a[2:].replace("-", "_") for a in args[::2]), args[1::2], strict=True))
    key = resolve_env_credential(api_key_env)
    if not key and configured.get("url") == values["query_url"]:
        key = str(configured.get("api_key", ""))
    if not key:
        if not sys.stdin.isatty() or is_json_output():
            raise click.ClickException(
                "Configure a query-only SigNoz service-account credential first."
            )
        key = click.prompt("Query-only service-account API key", hide_input=True)
    try:
        source, password = connect_source(
            TriageStore(),
            name=values["name"],
            query_url=values["query_url"],
            api_key=key,
            services=tuple(values["services"].split(",")),
            ingress_url=values["ingress_url"],
            credential_env=api_key_env,
        )
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(
        {
            "source_id": source.id,
            "query_access": "ready",
            "webhook_delivery": "untested",
            "channel": {
                "name": source.name,
                "webhook_configs": [
                    {
                        "url": source.webhook_url,
                        "send_resolved": True,
                        "http_config": {
                            "basic_auth": {"username": source.username, "password": password}
                        },
                    }
                ],
            },
            "next": "Settings → Notification Channels → Webhook. Save these credentials now; shown once. Test proves delivery only. Select this channel on your chosen alert; OpenSRE never changes paging.",
        }
    )


@triage_command.command(name="status")
def triage_status() -> None:
    """Show query, webhook delivery, and gateway worker readiness separately."""
    _emit(TriageStore().status())


@triage_command.command(name="list")
def triage_list() -> None:
    """List recent alert occurrences and independent investigation outcomes."""
    records = TriageStore().list()
    if is_json_output():
        _emit(records)
    else:
        print_investigations(Console(), records)


@triage_command.command(name="show")
@click.argument("investigation_id")
def triage_show(investigation_id: str) -> None:
    """Read persisted reports, current resolution state, and separate evidence."""
    try:
        record = TriageStore().show(investigation_id)
    except KeyError:
        raise click.ClickException("Investigation ID not found") from None
    if is_json_output():
        _emit(record)
    else:
        print_report(Console(), record)


@triage_command.command(name="ask")
@click.argument("investigation_id")
@click.argument("question")
def triage_ask(investigation_id: str, question: str) -> None:
    """Queue a restricted follow-up on an existing occurrence."""
    try:
        job = TriageStore().ask(investigation_id, question)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit({"queued": job, "occurrence_id": investigation_id})


def _source_action(source_id: str, action: str) -> None:
    store = TriageStore()
    try:
        source = store.source(source_id)
        if not (action == "remove" and source.removed):
            store.control(source_id, action)
        if action == "remove" and source.credential_kind == "owned":
            delete_credential(source.credential_ref)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    _emit({"source_id": source_id, "action": action, "reports_retained": True})


@triage_command.command(name="pause")
@click.argument("source_id")
def triage_pause(source_id: str) -> None:
    """Record events while stopping new and cooperatively cancelling active work."""
    _source_action(source_id, "pause")


@triage_command.command(name="resume")
@click.argument("source_id")
def triage_resume(source_id: str) -> None:
    """Resume future occurrences without replaying paused backlog."""
    _source_action(source_id, "resume")


@triage_command.command(name="remove")
@click.argument("source_id")
def triage_remove(source_id: str) -> None:
    """Remove a source's authority while retaining accepted events and reports."""
    _source_action(source_id, "remove")


@triage_command.group(name="demo", invoke_without_command=True)
@click.option("--id", "demo_id", default=None, help="Resume an existing owned demo.")
@click.pass_context
def triage_demo(ctx: click.Context, demo_id: str | None) -> None:
    """Start/resume the isolated, fully automated payment-error demonstration."""
    from integrations.signoz import PaymentDemo

    if ctx.invoked_subcommand is not None:
        return

    def ensure_gateway() -> int:
        from config.llm_settings import has_credentials_for_active_llm_provider
        from surfaces.cli.commands.gateway import gateway_start_command, verified_gateway_web_port

        if not has_credentials_for_active_llm_provider():
            raise ValueError(
                "Configure your model before starting the demo; model calls may incur cost"
            )
        store = TriageStore()
        if not store.status()["worker"].get("ready"):
            ctx.invoke(gateway_start_command, foreground=False)
            from integrations.signoz import wait_for

            wait_for(
                lambda: store.status()["worker"].get("ready"),
                seconds=30,
                message="Waiting for gateway triage worker",
                progress=click.echo,
            )
        return verified_gateway_web_port()

    try:
        demo = PaymentDemo(demo_id, progress=click.echo)
        click.echo(
            f"Demo {demo.id}. Uses Docker (8 GiB RAM, 15 GiB free disk) and your configured model. Status: triage demo status {demo.id}"
        )
        _emit(demo.start(ensure_gateway))
    except Exception as exc:
        if "demo" in locals():
            demo.data["failure"] = type(exc).__name__
            demo.save()
        # Download/provider failures can contain URLs/secrets; emit only safe classes.
        safe = (
            str(exc)
            if isinstance(exc, (ValueError, RuntimeError, TimeoutError))
            else type(exc).__name__
        )
        raise click.ClickException(safe) from None


@triage_demo.command(name="status")
@click.argument("demo_id")
def triage_demo_status(demo_id: str) -> None:
    """Read progress and the retained report ID."""
    _demo_operation(demo_id, "status")


@triage_demo.command(name="reset")
@click.argument("demo_id")
def triage_demo_reset(demo_id: str) -> None:
    """Disable the fault, verify recovery, wait for native resolution."""
    _demo_operation(demo_id, "reset")


@triage_demo.command(name="cleanup")
@click.argument("demo_id")
def triage_demo_cleanup(demo_id: str) -> None:
    """Remove only this demo's owned resources; retain reports and gateway."""
    _demo_operation(demo_id, "cleanup")


def _demo_operation(demo_id: str, operation: str) -> None:
    from integrations.signoz import PaymentDemo, demo_root

    if not (demo_root(demo_id) / "manifest.json").exists():
        raise click.ClickException("Demo not found")
    try:
        _emit(getattr(PaymentDemo(demo_id, progress=click.echo), operation)())
    except (ValueError, RuntimeError, TimeoutError) as exc:
        raise click.ClickException(str(exc)) from None
