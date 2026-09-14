"""LLM-observability port: typed observations that carry input and output.

``spans.py`` records duration-only spans for the local JSONL session trace.
This is the second, richer seam: an *observation* is a typed region
(``span`` / ``agent`` / ``generation`` / ``tool``) with input, output, token
usage and metadata, nested by the active execution context and exported by an
installed backend such as Langfuse (``infrastructure.observability.langfuse``).

Design for production safety
----------------------------
* Default sink is :class:`NoopObservationSink`; the helpers hand back one
  shared inert observation, so an uninstrumented process pays an ``isinstance``
  check and nothing else.
* Call sites gate payload construction on :func:`is_observation_sink_active`
  so message copies and redaction run only when a backend is listening.
* A sink must never raise into product code; adapters guard their own calls.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ObservationKind(StrEnum):
    """Langfuse-compatible observation types this codebase emits."""

    SPAN = "span"
    AGENT = "agent"
    GENERATION = "generation"
    TOOL = "tool"


class ObservationLevel(StrEnum):
    """Severity attached to an observation on completion."""

    DEFAULT = "DEFAULT"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class TraceAttributes:
    """Trace-wide attributes applied to a root observation and everything under it."""

    session_id: str | None = None
    user_id: str | None = None
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class GenerationUsage:
    """Provider-reported token counts for one model invocation.

    ``input_tokens`` / ``output_tokens`` are passed through as the provider
    reported them. Cache counters travel separately because providers disagree
    on whether ``input_tokens`` already includes cache reads; a backend must
    not add them into a second billing bucket.
    """

    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


class Observation(Protocol):
    """One open observation; ``update`` may be called any number of times."""

    def update(
        self,
        *,
        output: Any = None,
        metadata: Mapping[str, Any] | None = None,
        usage: GenerationUsage | None = None,
        level: ObservationLevel | None = None,
        status_message: str | None = None,
    ) -> None:
        """Attach output, metadata, usage or a completion level to the observation."""


class ObservationSink(Protocol):
    """Backend that materialises observations (Langfuse, a test recorder, ...)."""

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
        """Open ``name`` nested under the currently active observation.

        ``trace`` is only meaningful on the root observation of a trace; it is
        propagated to every observation opened inside the returned context.
        """

    def flush(self) -> None:
        """Block until buffered observations have been exported."""

    def shutdown(self) -> None:
        """Flush and release exporter resources."""


class _NoopObservation:
    def update(
        self,
        *,
        output: Any = None,
        metadata: Mapping[str, Any] | None = None,
        usage: GenerationUsage | None = None,
        level: ObservationLevel | None = None,
        status_message: str | None = None,
    ) -> None:
        del output, metadata, usage, level, status_message


#: Shared inert observation; sinks hand it back when a backend call fails.
NOOP_OBSERVATION: Observation = _NoopObservation()


class NoopObservationSink:
    """Default sink before a host installs a backend."""

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
        del kind, name, input, metadata, model, trace
        return nullcontext(NOOP_OBSERVATION)

    def flush(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


_sink: ObservationSink = NoopObservationSink()


def get_observation_sink() -> ObservationSink:
    return _sink


def set_observation_sink(sink: ObservationSink | None) -> None:
    """Install ``sink`` process-wide; ``None`` restores the noop default."""
    global _sink
    _sink = sink if sink is not None else NoopObservationSink()


def is_observation_sink_active() -> bool:
    """True when a real backend is installed; gate payload construction on this."""
    return not isinstance(_sink, NoopObservationSink)


def flush_observations() -> None:
    _sink.flush()


def shutdown_observations() -> None:
    _sink.shutdown()


# ---------------------------------------------------------------------------
# Semantic helpers — call sites use these, not ``observe`` with a kind kwarg
# ---------------------------------------------------------------------------


def observe_span(
    name: str,
    *,
    input: Any = None,
    metadata: Mapping[str, Any] | None = None,
    trace: TraceAttributes | None = None,
) -> AbstractContextManager[Observation]:
    """A generic unit of work; pass ``trace`` only on the root of a trace."""
    return _sink.observe(ObservationKind.SPAN, name, input=input, metadata=metadata, trace=trace)


def observe_agent(
    name: str,
    *,
    input: Any = None,
    metadata: Mapping[str, Any] | None = None,
) -> AbstractContextManager[Observation]:
    """A reasoning loop that decides control flow and may call tools."""
    return _sink.observe(ObservationKind.AGENT, name, input=input, metadata=metadata)


def observe_generation(
    name: str,
    *,
    model: str | None,
    input: Any = None,
    metadata: Mapping[str, Any] | None = None,
) -> AbstractContextManager[Observation]:
    """One model invocation; ``update(usage=...)`` once the provider reports tokens."""
    return _sink.observe(
        ObservationKind.GENERATION, name, input=input, metadata=metadata, model=model
    )


def observe_tool(
    name: str,
    *,
    input: Any = None,
    metadata: Mapping[str, Any] | None = None,
) -> AbstractContextManager[Observation]:
    """One tool execution, a sibling of the generation that requested it."""
    return _sink.observe(ObservationKind.TOOL, name, input=input, metadata=metadata)


__all__ = [
    "NOOP_OBSERVATION",
    "GenerationUsage",
    "NoopObservationSink",
    "Observation",
    "ObservationKind",
    "ObservationLevel",
    "ObservationSink",
    "TraceAttributes",
    "flush_observations",
    "get_observation_sink",
    "is_observation_sink_active",
    "observe_agent",
    "observe_generation",
    "observe_span",
    "observe_tool",
    "set_observation_sink",
    "shutdown_observations",
]
