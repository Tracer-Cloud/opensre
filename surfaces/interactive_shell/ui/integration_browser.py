"""Searchable connection records with background status checks and explicit actions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from prompt_toolkit.application import Application
from rich.text import Text

from infrastructure.terminal.theme import DIM, ERROR, WARNING
from surfaces.interactive_shell.session.connection_checks import ConnectionCheck
from surfaces.shared.terminal.components.command_input import run_command_input
from surfaces.shared.terminal.components.input_form import (
    InputChoice,
    InputField,
    RecordAction,
    build_input_form,
)
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow
from surfaces.shared.terminal.tables.tables import status_style


@dataclass(frozen=True)
class IntegrationEntry:
    """Safe display metadata, without configuration values or credentials."""

    service: str
    can_verify: bool


@dataclass(frozen=True)
class IntegrationSelection:
    """An explicit action; a missing service requests the setup picker."""

    action: Literal["setup", "remove"]
    service: str | None = None


def build_integration_browser(
    entries: Sequence[IntegrationEntry],
    *,
    mcp: bool = False,
    verify: Callable[[str], Awaitable[dict[str, str] | None]] | None = None,
    checks: dict[str, ConnectionCheck] | None = None,
) -> Application[IntegrationSelection | None]:
    """Reuse the loop-list form; verification refreshes records without leaving it."""
    title = "MCP servers" if mcp else "Integrations"
    command = "/mcp list" if mcp else "/integrations list"
    configured = {entry.service: entry for entry in entries}
    history = {} if checks is None else checks
    pending = {
        entry.service
        for entry in entries
        if entry.can_verify and verify is not None and entry.service not in history
    }
    slots = asyncio.Semaphore(3)

    def fields(_values: dict[str, str]) -> list[InputField]:
        choices: list[InputChoice] = []
        for entry in entries:
            checked = history.get(entry.service)
            checking = entry.service in pending
            status = "Checking…" if checking else checked.status if checked else "Configured"
            if checking:
                detail = "Checking connectivity in the background…"
            elif checked:
                detail = checked.detail or "Connectivity check finished."
            elif entry.can_verify:
                detail = "Configured; connectivity not checked."
            else:
                detail = "No connectivity check available."
            details = [Text(detail, style=ERROR if status in {"failed", "missing"} else "")]
            if checked is not None:
                details.append(
                    Text(
                        f"Last checked: {checked.checked_at.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC",
                        style=DIM,
                    )
                )
            choices.append(
                InputChoice(
                    entry.service,
                    entry.service,
                    record=RecordRow(
                        (
                            Text(entry.service, style="bold"),
                            Text(checked.source if checked else "—", style=DIM),
                            Text(status, style=WARNING if checking else status_style(status)),
                        ),
                        details=tuple(details),
                    ),
                    search_text=detail,
                    details=(f"Full details: /integrations show {entry.service}",),
                    compact_details=(f"/integrations show {entry.service}",),
                )
            )
        return [
            InputField(
                "record",
                title,
                description=(
                    "Checking connections in background · Esc cancels"
                    if pending
                    else "Connection status · Select a row for details"
                )
                if entries
                else f"No {'MCP servers' if mcp else 'integrations'} configured. "
                f"F4 {'connect' if mcp else 'add'} a connection.",
                searchable=True,
                record_choices=True,
                record_columns=tuple(
                    RecordColumn(name) for name in ("Service", "Source", "Status")
                ),
                choices=tuple(choices),
            )
        ]

    def selected(choice: InputChoice | None) -> bool:
        return choice is not None

    def can_verify(choice: InputChoice | None) -> bool:
        return (
            choice is not None
            and choice.value in configured
            and configured[choice.value].can_verify
            and choice.value not in pending
            and verify is not None
        )

    async def check_service(service: str) -> None:
        if verify is None:
            return
        pending.add(service)
        try:
            async with slots:
                try:
                    result = await verify(service)
                except Exception:
                    row = {
                        "status": "failed",
                        "detail": "Connectivity check failed. Retry or reconfigure this connection.",
                    }
                else:
                    row = result or {
                        "status": "missing",
                        "detail": "Connection no longer configured.",
                    }
                history[service] = ConnectionCheck(
                    source=row.get("source", "—"),
                    status=row.get("status", "missing"),
                    detail=row.get("detail", ""),
                    checked_at=datetime.now(UTC),
                )
        finally:
            pending.discard(service)

    async def check(choice: InputChoice | None) -> None:
        if choice is not None and choice.value is not None:
            await check_service(choice.value)

    async def load(refresh: Callable[[], None]) -> None:
        async def check_and_refresh(service: str) -> None:
            await check_service(service)
            refresh()

        async with asyncio.TaskGroup() as group:
            for entry in entries:
                if entry.service in pending:
                    group.create_task(check_and_refresh(entry.service))

    async def setup(choice: InputChoice | None) -> IntegrationSelection:
        return IntegrationSelection("setup", choice.value if choice else None)

    async def remove(choice: InputChoice | None) -> IntegrationSelection:
        return IntegrationSelection("remove", choice.value if choice else None)

    async def add(_choice: InputChoice | None) -> IntegrationSelection:
        return IntegrationSelection("setup")

    # Leave room for the selected-item preview and three complete control rows.
    return build_input_form(
        {"record": ""},
        title=command,
        heading=title,
        submit_label="Close",
        fields=fields,
        validate=lambda _values: None,
        initial_field="record",
        browse_only=True,
        load_records=load,
        record_actions=(
            RecordAction(
                "enter",
                "Enter recheck",
                check,
                can_verify,
                progress_label="Verifying connection…",
            ),
            RecordAction("f2", "F2 setup", setup, selected),
            RecordAction("f3", "F3 disconnect" if mcp else "F3 remove", remove, selected),
            RecordAction("f4", "F4 connect" if mcp else "F4 add", add),
        ),
    )


def browse_integrations(
    entries: Sequence[IntegrationEntry],
    *,
    mcp: bool = False,
    verify: Callable[[str], Awaitable[dict[str, str] | None]] | None = None,
    checks: dict[str, ConnectionCheck] | None = None,
) -> IntegrationSelection | None:
    """Restore the terminal before returning a setup or confirmed-removal request."""
    return run_command_input(
        build_integration_browser(entries, mcp=mcp, verify=verify, checks=checks)
    )
