"""Best-effort decision records, separate from conversation and terminal output."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from infrastructure.analytics.repl_context import get_prompt_turn_id
from infrastructure.observability.trace.redaction import redact_sensitive
from infrastructure.observability.trace.spans import (
    current_trace_session_id,
    emit_span,
    is_session_trace_active,
)
from infrastructure.safety.secret_redaction import redact_text

log = logging.getLogger(__name__)


def _redact_text_values(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: _redact_text_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_text_values(item) for item in value]
    return value


def record_decision(
    name: str,
    *,
    attributes: dict[str, Any],
    context: Callable[[], dict[str, Any]] | None = None,
    session_id: str | None = None,
) -> None:
    """Retain full diagnostic text with credential redaction; never affect execution."""
    if not is_session_trace_active() or not (session_id or current_trace_session_id()):
        return
    try:
        attrs = {**(context() if context else {}), **attributes}
        turn_id = get_prompt_turn_id()
        if turn_id:
            attrs.setdefault("turn_id", turn_id)
        emit_span(
            span_kind="decision",
            name=name,
            attributes=_redact_text_values(redact_sensitive(attrs)),
            session_id=session_id,
        )
    except Exception:
        log.debug("could not record decision %s", name, exc_info=True)
