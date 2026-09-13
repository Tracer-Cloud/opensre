"""``ObservationSink`` backed by the Langfuse Python SDK (v4, OpenTelemetry-based).

Nesting comes from the OpenTelemetry context the SDK manages, so a
``generation`` opened inside an ``agent`` inside the turn root lands in the
right place without callers passing parents around. Every SDK call is guarded:
a tracing failure degrades to a missing observation, never a failed turn.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager
from typing import Any, Final, Literal

from langfuse import Langfuse, propagate_attributes

from infrastructure.observability.trace.observations import (
    NOOP_OBSERVATION,
    GenerationUsage,
    Observation,
    ObservationKind,
    ObservationLevel,
    TraceAttributes,
)
from infrastructure.observability.trace.redaction import redact_sensitive
from infrastructure.safety.secret_redaction import redact_text

log = logging.getLogger(__name__)

#: Exception text is useful for debugging a failed step but is bounded so a
#: provider error that echoes a whole request does not become the observation.
_STATUS_MESSAGE_MAX_CHARS = 400


def _usage_kwargs(usage: GenerationUsage) -> dict[str, Any]:
    """Exclusive ``input`` / ``output`` buckets; cache counters go to metadata.

    Providers disagree on whether ``input_tokens`` already includes cache reads
    (OpenAI: yes, Anthropic: no), so cache counts are recorded but never added
    as a second billing bucket — that would double-count on one of them.
    """
    details: dict[str, int] = {}
    if usage.input_tokens is not None:
        details["input"] = usage.input_tokens
    if usage.output_tokens is not None:
        details["output"] = usage.output_tokens
    kwargs: dict[str, Any] = {}
    if details:
        kwargs["usage_details"] = details
    cache_metadata = {
        key: value
        for key, value in (
            ("cache_read_tokens", usage.cache_read_tokens),
            ("cache_creation_tokens", usage.cache_creation_tokens),
        )
        if value is not None
    }
    if cache_metadata:
        kwargs["metadata"] = cache_metadata
    return kwargs


class _LangfuseObservation:
    """Adapter from the port's ``update`` vocabulary to the SDK span wrapper."""

    def __init__(self, span: Any) -> None:
        self._span = span

    def update(
        self,
        *,
        output: Any = None,
        metadata: Mapping[str, Any] | None = None,
        usage: GenerationUsage | None = None,
        level: ObservationLevel | None = None,
        status_message: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if output is not None:
            kwargs["output"] = redact_sensitive(output)
        merged_metadata: dict[str, Any] = dict(redact_sensitive(dict(metadata))) if metadata else {}
        if usage is not None:
            usage_kwargs = _usage_kwargs(usage)
            merged_metadata.update(usage_kwargs.pop("metadata", {}))
            kwargs.update(usage_kwargs)
        if merged_metadata:
            kwargs["metadata"] = merged_metadata
        if level is not None:
            kwargs["level"] = level.value
        if status_message:
            kwargs["status_message"] = status_message
        if not kwargs:
            return
        try:
            self._span.update(**kwargs)
        except Exception:  # noqa: BLE001 - tracing must never break the turn
            log.debug("langfuse observation update failed", exc_info=True)

    def mark_error(self, exc: BaseException) -> None:
        text = redact_text(f"{type(exc).__name__}: {exc}")
        self.update(
            level=ObservationLevel.ERROR,
            status_message=text[:_STATUS_MESSAGE_MAX_CHARS],
        )


def _trace_kwargs(trace: TraceAttributes) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if trace.session_id:
        kwargs["session_id"] = trace.session_id
    if trace.user_id:
        kwargs["user_id"] = trace.user_id
    if trace.tags:
        kwargs["tags"] = list(trace.tags)
    if trace.metadata:
        kwargs["metadata"] = {key: str(value) for key, value in trace.metadata.items()}
    return kwargs


_NON_GENERATION_KINDS: Final[Mapping[ObservationKind, Literal["span", "agent", "tool"]]] = {
    ObservationKind.SPAN: "span",
    ObservationKind.AGENT: "agent",
    ObservationKind.TOOL: "tool",
}


class LangfuseObservationSink:
    """Materialise port observations as Langfuse observations."""

    def __init__(self, client: Langfuse) -> None:
        self._client = client

    @property
    def client(self) -> Langfuse:
        return self._client

    def _start(
        self,
        kind: ObservationKind,
        name: str,
        *,
        input: Any,
        metadata: Mapping[str, Any] | None,
        model: str | None,
    ) -> AbstractContextManager[Any]:
        redacted_input = None if input is None else redact_sensitive(input)
        redacted_metadata = None if metadata is None else redact_sensitive(dict(metadata))
        if kind is ObservationKind.GENERATION:
            # Only generations carry ``model``; the SDK overloads reject it elsewhere.
            return self._client.start_as_current_observation(
                as_type="generation",
                name=name,
                input=redacted_input,
                metadata=redacted_metadata,
                model=model,
            )
        return self._client.start_as_current_observation(
            as_type=_NON_GENERATION_KINDS[kind],
            name=name,
            input=redacted_input,
            metadata=redacted_metadata,
        )

    @contextmanager
    def observe(
        self,
        kind: ObservationKind,
        name: str,
        *,
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
        model: str | None = None,
        trace: TraceAttributes | None = None,
    ) -> Iterator[Observation]:
        with ExitStack() as stack:
            try:
                span = stack.enter_context(
                    self._start(kind, name, input=input, metadata=metadata, model=model)
                )
                trace_kwargs = _trace_kwargs(trace) if trace is not None else {}
                if trace_kwargs:
                    stack.enter_context(propagate_attributes(**trace_kwargs))
            except Exception:  # noqa: BLE001 - tracing must never break the turn
                log.debug(
                    "langfuse observation %s/%s could not start", kind.value, name, exc_info=True
                )
                yield NOOP_OBSERVATION
                return
            observation = _LangfuseObservation(span)
            try:
                yield observation
            except Exception as exc:
                observation.mark_error(exc)
                raise

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:  # noqa: BLE001 - shutdown paths must not raise
            log.debug("langfuse flush failed", exc_info=True)

    def shutdown(self) -> None:
        try:
            self._client.shutdown()
        except Exception:  # noqa: BLE001 - shutdown paths must not raise
            log.debug("langfuse shutdown failed", exc_info=True)


__all__ = ["LangfuseObservationSink"]
