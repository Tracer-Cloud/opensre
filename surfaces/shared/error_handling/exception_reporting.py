"""Shared exception reporting policy for CLI and interactive-shell boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import click

from infrastructure.errors import OpenSREError
from infrastructure.observability.errors.sentry import capture_exception


def should_report_exception(exc: BaseException, *, expected: bool = False) -> bool:
    """Return whether a caught exception should be reported to Sentry."""
    if expected:
        return False
    return not isinstance(
        exc,
        (KeyboardInterrupt, EOFError, OpenSREError, click.Abort, click.ClickException),
    )


def report_exception(
    exc: BaseException,
    *,
    context: str,
    extra: Mapping[str, Any] | None = None,
    expected: bool = False,
) -> bool:
    """Best-effort Sentry report for swallowed boundary exceptions."""
    if not should_report_exception(exc, expected=expected):
        return False
    capture_exception(exc, context=context, extra=extra)
    return True


__all__ = ["report_exception", "should_report_exception"]
