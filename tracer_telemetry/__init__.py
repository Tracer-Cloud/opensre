"""Deprecated tracer_telemetry package.

The outbound telemetry implementation has been removed from this project.
This shim exists only so tests and demos that import ``tracer_telemetry``
can still run without failing imports. It provides no-op telemetry objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class _NoopTracer:
    def start_as_current_span(self, *_args: Any, **_kwargs: Any):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            yield self

        return _cm()


@dataclass
class _NoopMetrics:
    def __getattr__(self, _name: str) -> Any:
        def _noop(*_args: Any, **_kwargs: Any) -> None:  # noqa: D401
            """No-op metric method."""

        return _noop


@dataclass
class _NoopTelemetry:
    tracer: _NoopTracer
    metrics: _NoopMetrics

    def flush(self) -> None:
        return None


def init_telemetry(*_args: Any, **_kwargs: Any) -> _NoopTelemetry:
    """Return a no-op telemetry object."""
    return _NoopTelemetry(tracer=_NoopTracer(), metrics=_NoopMetrics())


def get_tracer(*_args: Any, **_kwargs: Any) -> _NoopTracer:
    """Return a no-op tracer."""
    return _NoopTracer()


__all__ = ["init_telemetry", "get_tracer", "_NoopTelemetry", "_NoopTracer", "_NoopMetrics"]
