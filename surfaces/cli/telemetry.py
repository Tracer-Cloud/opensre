"""Lazy platform shims for CLI analytics, Sentry, error reporting, and landing UI.

Each function defers its heavy ``platform``/UI import until called, keeping CLI
startup cheap. ``surfaces.cli.app`` imports these names, so tests patch the
seam there (e.g. ``surfaces.cli.app.capture_cli_invoked``) and the
entrypoint's own callers pick up the patched global.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from collections.abc import Mapping

    from infrastructure.analytics.provider import Properties
    from infrastructure.errors import OpenSREError


def capture_first_run_if_needed() -> None:
    from infrastructure.analytics.provider import capture_first_run_if_needed as _capture

    _capture()


def capture_cli_invoked(properties: Properties | None = None) -> None:
    from infrastructure.analytics.capture import capture_cli_invoked as _capture

    _capture(properties)


def capture_account_authenticated() -> None:
    from infrastructure.analytics.capture import capture_account_authenticated as _capture

    _capture()


def analytics_needs_flush() -> bool:
    from infrastructure.analytics.provider import analytics_needs_flush as _needs_flush

    return _needs_flush()


def shutdown_analytics(*, flush: bool = False, timeout: float | None = None) -> None:
    from infrastructure.analytics.provider import shutdown_analytics as _shutdown

    if timeout is None:
        _shutdown(flush=flush)
    else:
        _shutdown(flush=flush, timeout=timeout)


def build_cli_invoked_properties(
    *,
    entrypoint: str,
    command_parts: list[str],
    json_output: bool,
    verbose: bool,
    debug: bool,
    yes: bool,
    interactive: bool,
) -> Properties:
    from infrastructure.analytics.event_properties import build_cli_invoked_properties as _build

    return _build(
        entrypoint=entrypoint,
        command_parts=command_parts,
        json_output=json_output,
        verbose=verbose,
        debug=debug,
        yes=yes,
        interactive=interactive,
    )


def report_exception(
    exc: BaseException,
    *,
    context: str,
    extra: Mapping[str, Any] | None = None,
    expected: bool = False,
) -> bool:
    from surfaces.shared.error_handling.exception_reporting import (
        report_exception as _report_exception,
    )

    return _report_exception(exc, context=context, extra=extra, expected=expected)


def should_report_exception(exc: click.ClickException, *, expected: bool = False) -> bool:
    from surfaces.shared.error_handling.exception_reporting import (
        should_report_exception as _should_report_exception,
    )

    return _should_report_exception(exc, expected=expected)


def init_sentry(*, entrypoint: str | None = None) -> None:
    from infrastructure.observability.errors.sentry import init_sentry as _init_sentry

    _init_sentry(entrypoint=entrypoint)


def capture_exception(
    exc: BaseException,
    *,
    context: str | None = None,
    extra: Mapping[str, Any] | None = None,
    tags: Mapping[str, str] | None = None,
) -> str | None:
    from infrastructure.observability.errors.sentry import capture_exception as _capture_exception

    return _capture_exception(exc, context=context, extra=extra, tags=tags)


def render_landing(group: click.Group) -> None:
    from surfaces.cli.layout import render_landing as _render_landing

    _render_landing(group)


def load_structured_error_type() -> type[OpenSREError]:
    from infrastructure.errors import OpenSREError

    return OpenSREError


def render_structured_error(exc: OpenSREError) -> int:
    """Render a structured ``OpenSREError`` as a clean panel and return its exit code.

    Used for structured errors raised by non-CLI code (tools/integrations) that
    are not ``ClickException`` instances, so Click never renders them itself.
    """
    from rich.console import Console

    from infrastructure.terminal.errors import render_error

    hint: str | None = None
    if exc.suggestion:
        parts = [exc.suggestion]
        if exc.docs_url:
            parts.append(f"Docs: {exc.docs_url}")
        hint = "  ".join(parts)
    render_error(exc, console=Console(stderr=True, highlight=False), hint=hint)
    return int(exc.exit_code)
