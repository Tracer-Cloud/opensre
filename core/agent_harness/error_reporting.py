"""Core-owned default error reporter for the shared agent harness."""

from __future__ import annotations

import logging

from infrastructure.observability.errors.sentry import capture_exception

log = logging.getLogger(__name__)


class DefaultErrorReporter:
    """:class:`core.agent_harness.ports.ErrorReporter` using platform observability."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or log

    def report(self, exc: BaseException, *, context: str, expected: bool = False) -> None:
        if expected:
            self._logger.debug("%s: %s", context, exc)
            return
        self._logger.debug("%s", context, exc_info=exc)
        capture_exception(exc, context=context)
