"""Shared error-reporting helpers for boundary exception handling.

Use these helpers at every ``except`` site that intentionally swallows or
re-raises an exception, so the failure is always visible in Sentry and logs.

Tagging conventions (pass via ``tags``):
  surface   — cli | interactive_shell | service_client | tool | node |
               pipeline | integration | remote_server | analytics | auth |
               webapp | mcp | sandbox | deployment | masking
  component — module-level identifier, e.g. ``integrations.grafana.tempo``
  integration — vendor when applicable: grafana | splunk | vercel | …
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from pydantic import ValidationError

from infrastructure.observability.errors.sentry import capture_exception


def report_exception(
    exc: BaseException,
    *,
    logger: logging.Logger,
    message: str,
    severity: str = "error",
    tags: dict[str, str] | None = None,
    extras: dict[str, Any] | None = None,
    include_traceback: bool = True,
) -> None:
    """Log + Sentry-capture an exception with structured context.

    Use at boundaries where an exception is intentionally swallowed (the
    function returns a degraded value to its caller).
    """
    # `ValidationError.__str__` renders the rejected input inline via
    # `input_value=...`, which for a model-level failure is the whole input
    # mapping (secrets included). Swap in a message-only exception before it
    # reaches either sink: local `exc_info` logging bypasses Sentry's
    # `before_send` scrubber entirely, and Sentry's regex scrubber is a second
    # line of defense we shouldn't rely on exclusively. The wrapper keeps
    # `exc`'s `__traceback__` (but not `__cause__`/`__context__`, which would
    # print the raw text again as part of the chain) so logs and Sentry still
    # point at the failing model/field. Reuse the `integration` tag (every
    # current caller sets one) for a human phrase instead of falling back to
    # `message`'s tag-style text (e.g. "classify_failed: integration=... ")
    # duplicated as the exception's own str().
    if isinstance(exc, ValidationError):
        integration = (tags or {}).get("integration")
        safe_text = f"{integration} config validation failed" if integration else message
        safe_exc = ValueError(safe_text)
        safe_exc.__traceback__ = exc.__traceback__
        exc = safe_exc
    log_fn = getattr(logger, severity, logger.error)
    # Some expected fallback paths (e.g. remote network probe timeouts) should
    # remain visible in logs without dumping a full traceback into the terminal UI.
    log_fn("%s", message, exc_info=exc if include_traceback else False)
    # Info-severity reports are expected/graceful fallbacks; skip Sentry to avoid noise.
    if severity == "info":
        return
    combined: dict[str, Any] = {}
    if tags:
        combined.update({f"tag.{k}": v for k, v in tags.items()})
    if extras:
        combined.update(extras)
    if tags:
        capture_exception(exc, extra=combined or None, tags=tags)
    else:
        capture_exception(exc, extra=combined or None)


@contextmanager
def report_and_reraise(
    *,
    logger: logging.Logger,
    message: str,
    tags: dict[str, str] | None = None,
    extras: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Context manager that captures to Sentry + re-raises (for top-level boundaries)."""
    try:
        yield
    except Exception as exc:
        report_exception(exc, logger=logger, message=message, tags=tags, extras=extras)
        raise
