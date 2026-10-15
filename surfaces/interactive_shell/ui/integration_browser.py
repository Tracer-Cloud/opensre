"""Searchable integration records with explicit actions and inline verification."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from prompt_toolkit.application import Application
from rich.text import Text

from infrastructure.terminal.theme import DIM, ERROR
from surfaces.shared.terminal.components.command_input import run_command_input
from surfaces.shared.terminal.components.input_form import (
    InputChoice,
    InputField,
    InputFormError,
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
) -> Application[IntegrationSelection | None]:
    """Reuse the loop-list form; verification refreshes records without leaving it."""
    title = "MCP servers" if mcp else "Integrations"
    command = "/mcp list" if mcp else "/integrations list"
    configured = {entry.service: entry for entry in entries}
    results: dict[str, dict[str, str]] = {}

    def fields(_values: dict[str, str]) -> list[InputField]:
        choices: list[InputChoice] = []
        for entry in entries:
            result = results.get(entry.service, {})
            status = result.get("status", "Not checked" if entry.can_verify else "Configured")
            detail = result.get("detail") or (
                "Configured; connectivity not checked."
                if entry.can_verify
                else "No connectivity check available."
            )
            choices.append(
                InputChoice(
                    entry.service,
                    entry.service,
                    record=RecordRow(
                        (
                            Text(entry.service, style="bold"),
                            Text(result.get("source", "—"), style=DIM),
                            Text(status, style=status_style(status)),
                        ),
                        details=(
                            Text(detail, style=ERROR if status in {"failed", "missing"} else ""),
                        ),
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
                description="Configured connections · Enter runs a connectivity check"
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
            and verify is not None
        )

    async def check(choice: InputChoice | None) -> None:
        if choice is None or choice.value is None or verify is None:
            return
        service = choice.value
        try:
            result = await verify(service)
        except Exception as exc:
            raise InputFormError(
                "Connectivity check failed. Retry or reconfigure this connection."
            ) from exc
        results[service] = result or {
            "status": "missing",
            "detail": "Connection no longer configured.",
        }

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
        record_actions=(
            RecordAction(
                "enter",
                "Enter verify",
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
) -> IntegrationSelection | None:
    """Restore the terminal before returning a setup or confirmed-removal request."""
    return run_command_input(build_integration_browser(entries, mcp=mcp, verify=verify))
