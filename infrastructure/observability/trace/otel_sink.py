"""OTLP observation backend: export the observation port as OpenTelemetry spans.

Spans carry Langfuse's OTel attribute conventions (``langfuse.observation.*``,
``langfuse.session.id``, ...) so one export stream serves Langfuse's
``/api/public/otel`` endpoint, Tempo, or Jaeger. Where spans are exported is the
standard ``OTEL_EXPORTER_OTLP_*`` environment variables' business; this module
only decides whether the sink is installed (``OPENSRE_OTEL_OBSERVATIONS=1``).

OpenTelemetry is imported lazily so a process with the flag off never pays for
the SDK, matching the observation port's zero-overhead default.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Final

from config.constants.otel import OPENSRE_OTEL_OBSERVATIONS_ENV
from infrastructure.observability.trace.observations import (
    NOOP_OBSERVATION,
    GenerationUsage,
    Observation,
    ObservationKind,
    ObservationLevel,
    TraceAttributes,
    is_observation_sink_active,
    set_observation_sink,
)

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import Span, Tracer

_LOG = logging.getLogger(__name__)

_KIND_TO_OBSERVATION_TYPE: Final[dict[ObservationKind, str]] = {
    ObservationKind.SPAN: "span",
    ObservationKind.AGENT: "agent",
    ObservationKind.GENERATION: "generation",
    ObservationKind.TOOL: "tool",
}

_SESSION_ID_ATTR: Final = "langfuse.session.id"
_USER_ID_ATTR: Final = "langfuse.user.id"
_TRACE_TAGS_ATTR: Final = "langfuse.trace.tags"
_TRACE_METADATA_ATTR_PREFIX: Final = "langfuse.trace.metadata."
_INPUT_ATTR: Final = "langfuse.observation.input"
_OUTPUT_ATTR: Final = "langfuse.observation.output"
_METADATA_ATTR_PREFIX: Final = "langfuse.observation.metadata."
_MODEL_ATTR: Final = "langfuse.observation.model.name"
_USAGE_ATTR: Final = "langfuse.observation.usage_details"
_LEVEL_ATTR: Final = "langfuse.observation.level"
_STATUS_MESSAGE_ATTR: Final = "langfuse.observation.status_message"

#: Trace-wide attributes carried from a root observation to its descendants.
#:
#: The orchestrator passes ``trace=`` only on the root observation, but
#: Langfuse filters by ``langfuse.session.id`` / ``langfuse.user.id`` on every
#: span, so descendants read the attributes stamped by their root here.
_active_trace: ContextVar[TraceAttributes | None] = ContextVar(
    "otel_sink_active_trace", default=None
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _attr_value(value: Any) -> Any:
    """Pass OTel-scalar values through; JSON-encode anything else."""
    if isinstance(value, bool | int | float | str):
        return value
    return _json(value)


def _usage_details(usage: GenerationUsage) -> dict[str, int]:
    details: dict[str, int] = {}
    if usage.input_tokens is not None:
        details["input"] = usage.input_tokens
    if usage.output_tokens is not None:
        details["output"] = usage.output_tokens
    if usage.cache_read_tokens is not None:
        details["cache_read_input_tokens"] = usage.cache_read_tokens
    if usage.cache_creation_tokens is not None:
        details["cache_creation_input_tokens"] = usage.cache_creation_tokens
    return details


def _trace_stamp(trace: TraceAttributes, *, root: bool) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    if trace.session_id:
        attributes[_SESSION_ID_ATTR] = trace.session_id
    if trace.user_id:
        attributes[_USER_ID_ATTR] = trace.user_id
    if trace.tags:
        attributes[_TRACE_TAGS_ATTR] = list(trace.tags)
    if root and trace.metadata:
        for key, value in trace.metadata.items():
            attributes[f"{_TRACE_METADATA_ATTR_PREFIX}{key}"] = _attr_value(value)
    return attributes


class _OtelObservation:
    """Bridge ``Observation.update`` calls onto an open OTel span."""

    def __init__(self, span: Span) -> None:
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
        """Attach output, metadata, usage or a completion level to the span."""
        try:
            if output is not None:
                self._span.set_attribute(_OUTPUT_ATTR, _json(output))
            if metadata:
                for key, value in metadata.items():
                    self._span.set_attribute(f"{_METADATA_ATTR_PREFIX}{key}", _attr_value(value))
            if usage is not None:
                self._span.set_attribute(_USAGE_ATTR, _json(_usage_details(usage)))
            if level is not None:
                self._span.set_attribute(_LEVEL_ATTR, level.value)
            if status_message is not None:
                self._span.set_attribute(_STATUS_MESSAGE_ATTR, status_message)
        except Exception:
            _LOG.debug("OTLP observation update failed", exc_info=True)


class OtelObservationSink:
    """Materialise observations as OpenTelemetry spans on ``tracer_provider``."""

    def __init__(
        self,
        tracer_provider: TracerProvider,
        *,
        tracer: Tracer | None = None,
        owns_provider: bool = True,
    ) -> None:
        if tracer is None:
            from opentelemetry import trace

            tracer = trace.get_tracer("opensre.observations", tracer_provider=tracer_provider)
        self._tracer = tracer
        self._provider = tracer_provider
        self._owns_provider = owns_provider

    def flush(self) -> None:
        """Block until buffered spans have been exported."""
        try:
            self._provider.force_flush()
        except Exception:
            _LOG.warning("OTLP observation flush failed", exc_info=True)

    def shutdown(self) -> None:
        """Flush and release exporter resources; a borrowed provider stays running."""
        if not self._owns_provider:
            return
        try:
            self._provider.shutdown()
        except Exception:
            _LOG.warning("OTLP observation shutdown failed", exc_info=True)

    def observe(
        self,
        kind: ObservationKind,
        name: str,
        *,
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
        model: str | None = None,
        trace: TraceAttributes | None = None,
    ) -> AbstractContextManager[Observation]:
        try:
            attributes = _span_attributes(
                kind, input=input, metadata=metadata, model=model, trace=trace
            )
            span_context_manager = self._tracer.start_as_current_span(name, attributes=attributes)
        except Exception:
            _LOG.warning(
                "OTLP observation %s could not start; observation dropped", name, exc_info=True
            )
            return nullcontext(NOOP_OBSERVATION)

        return _observe_span(span_context_manager, trace)


@contextmanager
def _observe_span(
    span_context_manager: AbstractContextManager[Any],
    trace: TraceAttributes | None,
) -> Iterator[Observation]:
    token = _active_trace.set(trace) if trace is not None else None
    try:
        with span_context_manager as span:
            # ``start_as_current_span`` already records the exception and sets
            # an ERROR status when the body raises — nothing to add here.
            yield _OtelObservation(span)
    finally:
        if token is not None:
            _active_trace.reset(token)


def _span_attributes(
    kind: ObservationKind,
    *,
    input: Any,
    metadata: Mapping[str, Any] | None,
    model: str | None,
    trace: TraceAttributes | None,
) -> dict[str, Any]:
    attributes: dict[str, Any] = {"langfuse.observation.type": _KIND_TO_OBSERVATION_TYPE[kind]}
    if input is not None:
        attributes[_INPUT_ATTR] = _json(input)
    if metadata:
        for key, value in metadata.items():
            attributes[f"{_METADATA_ATTR_PREFIX}{key}"] = _attr_value(value)
    if model is not None:
        attributes[_MODEL_ATTR] = model
    effective_trace = trace if trace is not None else _active_trace.get()
    if effective_trace is not None:
        attributes.update(_trace_stamp(effective_trace, root=trace is not None))
    return attributes


def otel_observations_enabled() -> bool:
    return os.getenv(OPENSRE_OTEL_OBSERVATIONS_ENV) == "1"


def _build_otlp_provider() -> TracerProvider | None:
    """A private provider exporting via the standard ``OTEL_EXPORTER_OTLP_*`` variables."""
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        _LOG.warning(
            "%s=1 but the OpenTelemetry SDK is unavailable; LLM observations stay off",
            OPENSRE_OTEL_OBSERVATIONS_ENV,
        )
        return None

    try:
        provider = TracerProvider(resource=Resource.create({"service.name": "opensre"}))
        # OTLPSpanExporter reads OTEL_EXPORTER_OTLP_ENDPOINT/_HEADERS/_PROTOCOL.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    except Exception:
        _LOG.warning(
            "OTLP observation exporter could not start; LLM observations stay off", exc_info=True
        )
        return None
    return provider


def init_otel_observations(tracer_provider: TracerProvider | None = None) -> bool:
    """Install the OTLP observation sink; return whether it is on.

    Two opt-in paths. With no argument the call is gated on
    ``OPENSRE_OTEL_OBSERVATIONS=1``: the sink builds a private provider that
    exports via the standard ``OTEL_EXPORTER_OTLP_*`` variables and owns it
    (``shutdown`` tears it down). With ``tracer_provider`` the call is an
    explicit programmatic opt-in — the flag is not consulted, no exporter is
    added, spans ride the host's provider and pipeline; ``shutdown`` leaves the
    host's provider running and ``flush`` still force-flushes it.

    Idempotent, never raises, and never clobbers a sink a host already
    installed — a broken tracing backend must not stop the agent from booting.
    """
    if tracer_provider is None and not otel_observations_enabled():
        return False
    if is_observation_sink_active():
        return True

    if tracer_provider is None:
        provider = _build_otlp_provider()
        if provider is None:
            return False
        sink = OtelObservationSink(provider,owns_provider=True)
    else:
        if not hasattr(tracer_provider, "add_span_processor"):
            # A proxy provider (the host never set a real one) records nothing.
            _LOG.warning(
                "tracer_provider has no span processors; observation spans will not be exported"
            )
        sink = OtelObservationSink(tracer_provider, owns_provider=False)

    set_observation_sink(sink)
    _LOG.info("OTLP observation export enabled")
    return True


__all__ = [
    "OtelObservationSink",
    "init_otel_observations",
    "otel_observations_enabled",
]
