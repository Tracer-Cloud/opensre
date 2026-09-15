"""Core-owned default turn accounting for the shared agent harness."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult

log = logging.getLogger(__name__)


class DefaultTurnAccounting:
    """:class:`core.agent_harness.ports.TurnAccounting` for non-terminal surfaces."""

    def __init__(self, session: Any, text: str) -> None:
        self._session = session
        self._text = text

    def record_action_result(self, action_result: ToolCallingTurnResult) -> None:
        _ = action_result

    def finalize(self, result: TurnResult) -> TurnResult:
        response = (result.assistant_response_text or "").strip()
        if response or getattr(self._session, "pending_user_choice", None) is not None:
            _append_turn_detail(
                self._session,
                kind="chat",
                prompt=self._text,
                response=response or None,
            )
        with contextlib.suppress(AttributeError):
            self._session.last_assistant_intent = result.final_intent
        return result


def _append_turn_detail(
    session: Any,
    *,
    kind: str,
    prompt: str,
    response: str | None,
    llm_run: Any | None = None,
) -> None:
    store = getattr(session, "store", None)
    append_turn_detail = getattr(store, "append_turn_detail", None)
    session_id = getattr(session, "session_id", "")
    if not callable(append_turn_detail) or not isinstance(session_id, str) or not session_id:
        return
    try:
        append_turn_detail(
            session_id,
            kind,
            prompt,
            response=response,
            model=getattr(llm_run, "model", None) if llm_run is not None else None,
            provider=getattr(llm_run, "provider", None) if llm_run is not None else None,
            latency_ms=getattr(llm_run, "latency_ms", None) if llm_run is not None else None,
            system_prompt=getattr(llm_run, "final_system_prompt", None)
            if llm_run is not None
            else None,
        )
    except Exception:
        log.debug("failed to persist default turn detail", exc_info=True)
