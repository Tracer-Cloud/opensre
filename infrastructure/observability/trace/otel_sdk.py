"""OpenTelemetry SDK wiring: one tracer provider plus outbound auto-instrumentation.

Integration clients reach Grafana, AWS, GitHub, Kubernetes … through ``requests``
and ``boto3``. Instrumenting those two libraries once at boot gives every one of
them a client span (duration, target, status) without a single integration
hand-rolling its own — so ``requests``/``botocore`` are instrumented here and
nowhere else.

Opt-in by configuration: nothing is imported or installed unless an OTLP
endpoint is configured (``OTEL_EXPORTER_OTLP_ENDPOINT`` or
``GCLOUD_OTLP_ENDPOINT``), so a CLI run without one pays a single env read.
Only the ``http/protobuf`` exporter ships with OpenSRE, which is what
:func:`config.grafana_cloud.apply_otel_env_defaults` defaults the protocol to.

This is the outbound-span path. Session trace spans (JSONL / ATM) are a separate
product surface and stay in :mod:`infrastructure.observability.trace.spans`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import SpanProcessor, TracerProvider

_LOG = logging.getLogger(__name__)

#: SDK fallback prefix when neither OTEL_SERVICE_NAME nor OTEL_RESOURCE_ATTRIBUTES names the service.
_UNKNOWN_SERVICE_NAME = "unknown_service"


class _OtelState:
    """Holder for the boot-once guard.

    An attribute on a stable container rather than a module ``global``, matching
    ``infrastructure.observability.errors.sentry._ScopeTagsState``.
    """

    configured: bool = False


def _resolved_otlp_endpoint() -> str:
    """The OTLP traces endpoint after Grafana Cloud defaults are applied."""
    from config.grafana_cloud import apply_otel_env_defaults, get_effective_otlp_endpoint

    apply_otel_env_defaults()
    return get_effective_otlp_endpoint()


def _build_span_processor() -> SpanProcessor:
    """Batch spans to the configured OTLP endpoint.

    A seam: tests replace this with an in-memory processor so no exporter
    thread reaches for the network.
    """
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    return BatchSpanProcessor(OTLPSpanExporter())


def _install_tracer_provider() -> TracerProvider:
    """Return the SDK tracer provider, installing one if the host has not."""
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.semconv.attributes.service_attributes import (
        SERVICE_NAME,
        SERVICE_VERSION,
    )

    from config.constants import PRODUCT_NAME
    from config.version import get_opensre_version

    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        # An embedding host already wired its own SDK provider — export through it.
        return existing

    # ``Resource.create`` reads OTEL_SERVICE_NAME / OTEL_RESOURCE_ATTRIBUTES and
    # falls back to ``unknown_service`` — name ourselves only in that fallback,
    # so an operator's own service name still wins.
    resource = Resource.create({SERVICE_VERSION: get_opensre_version()})
    service_name = str(resource.attributes.get(SERVICE_NAME, ""))
    if service_name.startswith(_UNKNOWN_SERVICE_NAME):
        resource = resource.merge(Resource({SERVICE_NAME: PRODUCT_NAME}))
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(_build_span_processor())
    trace.set_tracer_provider(provider)
    return provider


def _instrument_outbound_clients(provider: TracerProvider) -> None:
    from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor

    RequestsInstrumentor().instrument(tracer_provider=provider)
    BotocoreInstrumentor().instrument(tracer_provider=provider)


def init_otel_tracing() -> bool:
    """Wire the tracer provider and instrument ``requests``/``boto3``. Idempotent.

    Returns whether outbound tracing is active after the call. Never raises:
    telemetry must not take a host down, and a missing or broken exporter leaves
    integration calls behaving exactly as before.
    """
    if _OtelState.configured:
        return True
    if not _resolved_otlp_endpoint():
        return False
    try:
        _instrument_outbound_clients(_install_tracer_provider())
    except Exception:
        _LOG.debug(
            "OpenTelemetry tracing setup failed; outbound calls stay untraced",
            exc_info=True,
        )
        return False
    _OtelState.configured = True
    return True


def _reset_otel_state_for_tests() -> None:
    """Clear the boot-once guard. Test-only helper."""
    _OtelState.configured = False


__all__ = ["init_otel_tracing"]
