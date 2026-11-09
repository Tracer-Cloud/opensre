"""Sentry capture for integration-validator broad-exception sites.

Every ``except Exception`` block in ``integrations/<vendor>.py`` validators
should call :func:`report_validation_failure` *before* returning the degraded
``ValidationResult``. This keeps vendor-level failure trends visible in Sentry
without changing operator-visible output.
"""

from __future__ import annotations

import logging
from typing import Any

from infrastructure.observability.errors.boundary import report_exception


def report_validation_failure(
    exc: BaseException,
    *,
    logger: logging.Logger,
    integration: str,
    method: str,
    severity: str = "warning",
    extras: dict[str, Any] | None = None,
    include_traceback: bool = False,
) -> None:
    """Log + Sentry-capture a validator broad-except failure with vendor tags.

    Args:
        exc: The exception caught in the broad-except block.
        logger: The caller's module-level logger.
        integration: Vendor identifier (e.g. ``"postgresql"``, ``"kafka"``).
        method: Function or method name where the failure happened. Use
            ``"<outer>.<inner>"`` for nested probes (e.g.
            ``"get_replication_status.statement_probe"``).
        severity: ``logging``-compatible level name; defaults to ``"warning"``
            since most validator failures are vendor/config issues rather
            than bugs in OpenSRE.
        extras: Optional structured fields (DAG id, statement name, etc.).
            Merged into Sentry ``extra`` without becoming Sentry tags, so
            they don't inflate Sentry's tag cardinality.
        include_traceback: When ``False`` (default), only the one-line message is
            logged, so a vendor/config failure (e.g. a ``401`` during
            ``/integrations``) does not dump a full stack trace into the REPL.
            The exception is still captured to Sentry with its traceback. Set
            ``True`` only when the local traceback genuinely aids debugging.
    """
    report_exception(
        exc,
        logger=logger,
        message=f"[{integration}] {method} validation failed",
        severity=severity,
        tags={
            "surface": "integration",
            "integration": integration,
            "event": "validation_failed",
            "method": method,
        },
        extras=extras,
        include_traceback=include_traceback,
    )


def report_classify_failure(
    exc: BaseException,
    *,
    logger: logging.Logger,
    integration: str,
    record_id: str,
) -> None:
    """Log + Sentry-capture a classify failure for an integration record.

    ``report_exception`` scrubs ``pydantic.ValidationError`` detail (e.g. a
    secret rendered via ``input_value=...``) out of local logs before this
    call reaches it, so no sanitization is needed here.
    """
    report_exception(
        exc,
        logger=logger,
        message=f"classify_failed: integration={integration} record_id={record_id}",
        severity="warning",
        tags={
            "surface": "integration",
            "component": "integrations",
            "integration": integration,
            "event": "classify_failed",
        },
        extras={"record_id": record_id},
    )
