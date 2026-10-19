"""Opt-in foreground telemetry watch and retained lifecycle reports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import click
import yaml
from filelock import Timeout
from pydantic import Field, ValidationError

from config.constants.telemetry import TELEMETRY_CLICKHOUSE_PASSWORD_ENV
from config.llm_credentials import resolve_env_credential
from config.strict_config import StrictConfigModel
from core.telemetry.investigation import investigate, sanitize
from core.telemetry.models import WatchRule
from core.telemetry.storage.store import WatchStore
from core.telemetry.watch import WatchRunner, poll
from integrations.clickhouse import ClickHouseEvidenceConfig, ClickHouseEvidenceReader


class WatchConfig(StrictConfigModel):
    source: ClickHouseEvidenceConfig = Field(default_factory=ClickHouseEvidenceConfig)
    watch: WatchRule


@click.group("triage")
def triage_command() -> None:
    """Watch scoped OTel telemetry and retain incident reports (opt-in pilot)."""


@triage_command.command("watch")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Required watch YAML; contains no passwords.",
)
@click.option(
    "--state",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="Required local SQLite path; one watch per file.",
)
@click.option("--once", is_flag=True, help="Optional: process one completed window, then exit.")
@click.option(
    "--telegram",
    is_flag=True,
    help="Optional: deliver lifecycle reports using configured Telegram credentials.",
)
@click.option(
    "--chat-id",
    default=None,
    help="Optional Telegram chat override; otherwise use the configured default.",
)
def watch(config_path: Path, state: Path, once: bool, telegram: bool, chat_id: str | None) -> None:
    """Poll completed metric windows; investigate only a new incident."""
    if chat_id and not telegram:
        raise click.UsageError("--chat-id requires --telegram.")
    try:
        config = WatchConfig.model_validate(yaml.safe_load(config_path.read_text(encoding="utf-8")))
    except (ValueError, OSError, yaml.YAMLError) as exc:
        # Config contains no credentials; omit Pydantic input echoes nevertheless.
        detail = (
            exc.errors(include_input=False)
            if isinstance(exc, ValidationError)
            else type(exc).__name__
        )
        raise click.ClickException(f"Invalid watch config {config_path}: {detail}") from exc
    password = resolve_env_credential(TELEMETRY_CLICKHOUSE_PASSWORD_ENV)
    if not password:
        raise click.ClickException(f"Set required {TELEMETRY_CLICKHOUSE_PASSWORD_ENV} and retry.")
    credentials = None
    if telegram:
        from integrations.telegram import load_credentials_from_env

        try:
            credentials = load_credentials_from_env(chat_id_override=chat_id)
        except Exception as exc:
            raise click.ClickException(
                "Configure Telegram with `opensre integrations setup telegram`."
            ) from exc
    identity = {
        "config": config.model_dump(),
        "destination": credentials.chat_id if credentials else "terminal",
        "telegram": telegram,
        "bot_identity": credentials.bot_token.split(":", 1)[0] if credentials else "",
    }
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    state = state.expanduser().resolve()
    reader = ClickHouseEvidenceReader(config.source, password)

    def deliver(event_id: str, body: str) -> bool:
        safe_body = sanitize(body)
        click.echo(f"\nEvent {event_id}\n{safe_body}")
        if credentials is None:
            return True
        from integrations.telegram import post_telegram_message

        ok, _, _ = post_telegram_message(
            credentials.chat_id, safe_body, credentials.bot_token, link_previews=False
        )
        if not ok:
            click.echo("Telegram delivery pending; saved report will be retried.", err=True)
        return ok

    try:
        reader.verify()
        store = WatchStore(state, fingerprint)
        runner = WatchRunner(config.watch, reader, store, investigate, deliver)
        click.echo(
            f"Watching {config.watch.service} ({config.watch.environment}); reports: {state}"
        )
        for health in poll(runner, once=once):
            click.echo(f"Telemetry: {health}")
    except KeyboardInterrupt:
        click.echo("Watch stopped; reports and pending delivery are retained.")
    except Timeout as exc:
        raise click.ClickException("Another process is using this --state file.") from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:
        raise click.ClickException(
            "Telemetry watch unavailable; check credentials, schema and connectivity."
        ) from exc
    finally:
        reader.close()


@triage_command.command("reports")
@click.option(
    "--state",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Required SQLite path used by the watch.",
)
def reports(state: Path) -> None:
    """Show complete retained opening/recovery reports and delivery status."""
    try:
        events = WatchStore(state, None).events()
    except Exception as exc:
        raise click.ClickException("Cannot read this watch state file.") from exc
    for event in events:
        status = "delivered" if event["delivered"] else "pending"
        click.echo(f"\nEvent {event['id']} ({status})\n{sanitize(event['body'])}")
    if not events:
        click.echo("No incident reports retained.")
