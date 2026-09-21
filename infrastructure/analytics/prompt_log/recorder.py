"""Prompt/response capture through the session, local log, and analytics sinks."""

from __future__ import annotations

import contextlib
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Protocol

from config.prompt_log import PromptLogConfig
from config.version import get_opensre_version
from core.llm_invoke_errors import LLM_PROVIDER_FAILURE_KINDS, classify_provider_error_kind
from infrastructure.analytics.prompt_log.sinks.local_jsonl import (
    append_prompt_log_record,
)
from infrastructure.analytics.prompt_log.sinks.posthog_ai import capture_ai_generation
from infrastructure.analytics.provider import JsonValue
from infrastructure.safety.secret_redaction import redact_text

_SUPPORTED_TURN_KINDS = frozenset({"agent", "follow_up", "new_alert", "background_task"})

# Sentinel for turns handled by terminal tools/slash commands without the
# conversational assistant LLM (PostHog ``$ai_model`` / ``$ai_provider``).
NO_CONVERSATIONAL_AGENT = "no_conversational_agent"

# Sentinel for turns where the conversational LLM was attempted but failed
# before the model/provider identity could be resolved (missing key, bad
# config). Distinct from ``NO_CONVERSATIONAL_AGENT`` so provider failures are
# never mislabeled as terminal-action turns in analytics.
UNKNOWN_LLM = "unknown"

# Maps PromptRecorder turn_kind to session turn kind stored in turn_detail records.
_TURN_TO_SESSION_KIND: dict[str, str] = {
    "agent": "chat",
    "follow_up": "follow_up",
    "new_alert": "alert",
    "background_task": "cli_command",
}


def _latest_slash_outcome(session: Any, *, start: int = 0) -> str | None:
    history = getattr(session, "history", None)
    if not isinstance(history, list):
        return None
    for entry in reversed(history[start:]):
        if not isinstance(entry, dict) or entry.get("type") != "slash":
            continue
        outcome = entry.get("slash_outcome")
        if isinstance(outcome, str) and outcome:
            return outcome
        return None
    return None


def _fallback_terminal_response(*, prompt: str) -> str:
    stripped = prompt.strip()
    if stripped:
        return f"terminal turn handled: {stripped}"
    return "terminal turn handled"


class _RunInfo(Protocol):
    """Read-only run metadata accepted without depending on harness accounting."""

    @property
    def model(self) -> str | None:
        """Resolved model name, when available."""

    @property
    def provider(self) -> str | None:
        """Resolved provider name, when available."""

    @property
    def latency_ms(self) -> int | None:
        """Elapsed run time in milliseconds, when available."""

    @property
    def input_tokens(self) -> int | None:
        """Provider-reported input usage, when available."""

    @property
    def output_tokens(self) -> int | None:
        """Provider-reported output usage, when available."""


class PromptRecorder:
    """Captures one `(prompt, response)` pair and flushes to configured sinks."""

    def __init__(
        self,
        *,
        config: PromptLogConfig,
        turn_kind: str,
        session_id: str,
        turn_id: str,
        prompt: str,
        session: Any,
        surface: str = "interactive_shell",
    ) -> None:
        self._config = config
        self._turn_kind = turn_kind
        self._session_id = session_id
        self._turn_id = turn_id
        self._prompt = prompt
        self._session = session
        history = getattr(session, "history", None)
        self._history_start = len(history) if isinstance(history, list) else 0
        self._surface = surface
        self._properties: dict[str, JsonValue] = {}
        self._response: str = ""
        self._error_kind: str = ""
        self._error_message: str = ""
        self._model: str | None = None
        self._provider: str | None = None
        self._latency_ms: int | None = None
        self._input_tokens: int | None = None
        self._output_tokens: int | None = None
        self._llm_attempted: bool | None = None
        self._start = time.monotonic()
        self._flushed = False

    @staticmethod
    def current() -> PromptRecorder | None:
        """Return the recorder owned by the current turn."""
        return current_recorder.get()

    def set_properties(self, properties: dict[str, JsonValue]) -> None:
        """Attach host-specific analytics metadata."""
        self._properties.update(properties)

    def set_run(self, run: _RunInfo) -> None:
        """Attach the model and provider-reported usage of the agent run."""
        self._llm_attempted = True
        self._model = run.model or self._model
        self._provider = run.provider or self._provider
        if run.input_tokens is not None:
            self._input_tokens = run.input_tokens
        if run.output_tokens is not None:
            self._output_tokens = run.output_tokens

    def set_llm_attempted(self, attempted: bool) -> None:
        """Record whether dispatch used a provider or a deterministic tool call."""
        self._llm_attempted = attempted

    @property
    def turn_id(self) -> str:
        """Stable correlation id for this prompt turn."""
        return self._turn_id

    @classmethod
    def start(
        cls,
        *,
        session: Any,
        text: str,
        turn_kind: str,
        surface: str = "interactive_shell",
    ) -> PromptRecorder | None:
        config = PromptLogConfig.load()
        if not config.enabled or turn_kind not in _SUPPORTED_TURN_KINDS:
            # When prompt logging is fully disabled, no recorder is created and
            # no turn_detail records are written to the session file. This means
            # the crash-recovery fallback in load_session() will produce empty
            # cli_agent_messages for sessions that crashed before flush(). The
            # conversation_snapshot written at clean exit is unaffected.
            return None
        return cls(
            config=config,
            turn_kind=turn_kind,
            session_id=_session_id(session),
            turn_id=str(uuid.uuid4()),
            prompt=_sanitize_text(text, config=config),
            session=session,
            surface=surface,
        )

    @classmethod
    def for_background_task(
        cls,
        *,
        session: Any,
        command: str,
        task_id: str,
    ) -> PromptRecorder | None:
        """Create a recorder for an async background task.

        Background CLI tasks finish long after
        the originating turn has flushed, so their stdout/stderr/exit outcome is
        not available to the turn-level recorder. This recorder is created at
        task launch — so its latency clock spans the full task duration — and is
        flushed by the task watcher once the outcome (including any error text)
        is known. ``turn_id`` is set to ``task_id`` so the prompt-log event
        correlates with the task surfaced by ``/tasks``.
        """
        config = PromptLogConfig.load()
        if not config.enabled:
            return None
        return cls(
            config=config,
            turn_kind="background_task",
            session_id=_session_id(session),
            turn_id=task_id or str(uuid.uuid4()),
            prompt=_sanitize_text(command, config=config),
            session=session,
        )

    def set_error(self, kind: str, message: str) -> None:
        """Attach a structured turn error emitted as ``$ai_error`` properties.

        The human-readable response text is unaffected; these properties make
        LLM/provider error detection exact instead of a regex over
        ``$ai_output_choices``.
        """
        kind = kind.strip()
        message = message.strip()
        if not (kind or message):
            return
        self._error_kind = kind or "error"
        self._error_message = _sanitize_text(message, config=self._config)
        if self._error_kind in LLM_PROVIDER_FAILURE_KINDS:
            self._llm_attempted = True

    def set_response(self, text: str, run: _RunInfo | None = None) -> None:
        cleaned = _sanitize_text(text, config=self._config)
        if not cleaned.strip():
            cleaned = ""
        self._response = cleaned
        if run is None:
            self._latency_ms = int((time.monotonic() - self._start) * 1000)
            return
        self.set_run(run)
        self._latency_ms = (
            run.latency_ms
            if run.latency_ms is not None
            else int((time.monotonic() - self._start) * 1000)
        )

    def _response_for_emit(self) -> str:
        """Resolve the assistant text written to sinks at flush time."""
        if self._response.strip():
            return self._response
        if self._error_message.strip():
            return self._error_message
        return _fallback_terminal_response(prompt=self._prompt)

    def flush(self) -> None:
        if self._flushed:
            return
        self._flushed = True
        response_text = self._response_for_emit()
        latency_ms = (
            self._latency_ms
            if self._latency_ms is not None
            else int((time.monotonic() - self._start) * 1000)
        )
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "session_id": self._session_id,
            "turn_id": self._turn_id,
            "turn_kind": self._turn_kind,
            "prompt": self._prompt,
            "response": response_text,
            "model": self._model or "",
            "provider": self._provider or "",
            "latency_ms": latency_ms,
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "opensre_version": get_opensre_version(),
        }
        if self._config.local_enabled:
            with contextlib.suppress(OSError):
                append_prompt_log_record(path=self._config.log_path, record=record)

        # Also write enriched turn to the session file so /resume can restore context.
        with contextlib.suppress(Exception):
            session_kind = _TURN_TO_SESSION_KIND.get(self._turn_kind, self._turn_kind)
            self._session.store.append_turn_detail(
                self._session_id,
                session_kind,
                self._prompt,
                response=response_text or None,
                turn_id=self._turn_id,
                model=self._model or None,
                provider=self._provider or None,
                latency_ms=latency_ms,
            )

        if self._config.posthog_enabled:
            with contextlib.suppress(Exception):
                # When the conversational LLM was attempted but the provider
                # failed, the turn is a failed LLM call — never a terminal
                # action. Fall back to "unknown" instead of the terminal
                # sentinel when the attempted model could not be resolved.
                llm_provider_failed = self._error_kind in LLM_PROVIDER_FAILURE_KINDS
                fallback_label = (
                    NO_CONVERSATIONAL_AGENT if self._llm_attempted is False else UNKNOWN_LLM
                )
                response_source = (
                    "captured"
                    if self._response.strip()
                    else "error"
                    if self._error_message.strip()
                    else "synthetic"
                )
                turn_outcome = (
                    "cancelled"
                    if self._error_kind == "cancelled"
                    else "error"
                    if self._error_kind
                    else "completed"
                    if response_source == "captured"
                    else "unknown"
                )
                posthog_properties: dict[str, JsonValue] = {
                    "turn_outcome": turn_outcome,
                    "response_source": response_source,
                    "$ai_is_error": bool(self._error_kind),
                    "$ai_trace_id": self._turn_id,
                    "$ai_session_id": self._session_id,
                    "$ai_span_id": self._turn_id,
                    "$ai_span_name": f"surfaces.{self._surface}.{self._turn_kind}",
                    "$ai_model": self._model or fallback_label,
                    "$ai_provider": self._provider or fallback_label,
                    "$ai_input": [{"role": "user", "content": self._prompt}],
                    "$ai_output_choices": [
                        {
                            "role": "assistant",
                            "content": response_text,
                        }
                    ],
                    "$ai_latency": (round(latency_ms / 1000.0, 3)),
                    "cli_turn_kind": self._turn_kind,
                    "cli_session_id": self._session_id,
                    "cli_turn_id": self._turn_id,
                    "opensre_version": get_opensre_version(),
                    **self._properties,
                }
                if self._llm_attempted is not None:
                    posthog_properties["llm_attempted"] = self._llm_attempted
                reported = 0
                for key, value in (
                    ("$ai_input_tokens", self._input_tokens),
                    ("$ai_output_tokens", self._output_tokens),
                ):
                    if value is not None:
                        posthog_properties[key] = value
                        reported += 1
                posthog_properties["token_usage_status"] = (
                    "complete" if reported == 2 else "partial" if reported else "unavailable"
                )
                slash_outcome = _latest_slash_outcome(self._session, start=self._history_start)
                if slash_outcome:
                    posthog_properties["slash_outcome"] = slash_outcome
                if self._error_kind:
                    posthog_properties["$ai_is_error"] = True
                    posthog_properties["$ai_error"] = self._error_message or self._error_kind
                    posthog_properties["error_kind"] = self._error_kind
                    if llm_provider_failed:
                        posthog_properties["ai_error_kind"] = classify_provider_error_kind(
                            self._error_message or self._error_kind
                        )
                capture_ai_generation(posthog_properties)


def _sanitize_text(text: str, *, config: PromptLogConfig) -> str:
    if config.redact:
        text = redact_text(text)
    return text[: config.max_chars]


def _session_id(session: Any) -> str:
    # Prefer the stable first-class field set at Session construction.
    # Fall back to the legacy side-channel for non-Session callers.
    sid = getattr(session, "session_id", None) or getattr(session, "_prompt_log_session_id", None)
    if isinstance(sid, str) and sid:
        return sid
    sid = str(uuid.uuid4())
    with contextlib.suppress(AttributeError):
        session._prompt_log_session_id = sid
    return sid


current_recorder: ContextVar[PromptRecorder | None] = ContextVar("prompt_recorder", default=None)
