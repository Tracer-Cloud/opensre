"""In-memory ``ObservationSink`` that records nesting the way Langfuse would.

Parentage follows a ``contextvars`` stack so tests can assert that worker
threads inherit the caller's observation context, not only that observations
were emitted.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from infrastructure.observability.trace.observations import (
    GenerationUsage,
    Observation,
    ObservationKind,
    ObservationLevel,
    TraceAttributes,
)


@dataclass
class RecordedObservation:
    kind: ObservationKind
    name: str
    input: Any
    metadata: dict[str, Any]
    model: str | None
    trace: TraceAttributes | None
    parent: RecordedObservation | None
    thread_name: str
    output: Any = None
    usage: GenerationUsage | None = None
    level: ObservationLevel | None = None
    status_message: str | None = None
    closed: bool = False
    updates: list[dict[str, Any]] = field(default_factory=list)

    def update(
        self,
        *,
        output: Any = None,
        metadata: Mapping[str, Any] | None = None,
        usage: GenerationUsage | None = None,
        level: ObservationLevel | None = None,
        status_message: str | None = None,
    ) -> None:
        self.updates.append(
            {
                "output": output,
                "metadata": metadata,
                "usage": usage,
                "level": level,
                "status_message": status_message,
            }
        )
        if output is not None:
            self.output = output
        if metadata:
            self.metadata.update(metadata)
        if usage is not None:
            self.usage = usage
        if level is not None:
            self.level = level
        if status_message is not None:
            self.status_message = status_message


_current: contextvars.ContextVar[RecordedObservation | None] = contextvars.ContextVar(
    "recording_observation_current", default=None
)


class RecordingObservationSink:
    """Collects every observation in open order; ``flushed``/``shut_down`` count calls."""

    def __init__(self) -> None:
        self.observations: list[RecordedObservation] = []
        self.flushed = 0
        self.shut_down = 0

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
        record = RecordedObservation(
            kind=kind,
            name=name,
            input=input,
            metadata=dict(metadata or {}),
            model=model,
            trace=trace,
            parent=_current.get(),
            thread_name=threading.current_thread().name,
        )
        self.observations.append(record)
        token = _current.set(record)
        try:
            yield record
        finally:
            record.closed = True
            _current.reset(token)

    def flush(self) -> None:
        self.flushed += 1

    def shutdown(self) -> None:
        self.shut_down += 1

    def of_kind(self, kind: ObservationKind) -> list[RecordedObservation]:
        return [record for record in self.observations if record.kind is kind]


__all__ = ["RecordedObservation", "RecordingObservationSink"]
