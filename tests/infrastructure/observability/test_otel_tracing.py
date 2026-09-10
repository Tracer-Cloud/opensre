"""Outbound OTLP tracing: integration ``requests``/``boto3`` calls get client spans."""

from __future__ import annotations

import pytest

from infrastructure.observability.trace import otel_sdk


@pytest.fixture(autouse=True)
def _isolated_otel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the boot-once guard and keep the exporter off the network."""
    otel_sdk._reset_otel_state_for_tests()
    monkeypatch.delenv("GCLOUD_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("GRAFANA_CONFIG_SKIP_ENV_FILE", "1")


def test_requests_call_produces_a_client_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Grafana/Tempo-style ``requests`` GET is traced without per-client wiring."""
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(
        otel_sdk,
        "_build_span_processor",
        lambda: SimpleSpanProcessor(exporter),
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    assert otel_sdk.init_otel_tracing() is True
    try:
        import requests

        with pytest.raises(requests.RequestException):
            # Port 1 refuses the connection; the span is emitted either way.
            requests.get("http://127.0.0.1:1/api/search", timeout=0.5)

        spans = exporter.get_finished_spans()
        assert [span.name for span in spans] == ["GET"]
    finally:
        RequestsInstrumentor().uninstrument()


def test_boot_is_a_noop_without_a_configured_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """No OTLP endpoint means no provider, no instrumentation, no import cost."""

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("tracer provider installed without an OTLP endpoint")

    monkeypatch.setattr(otel_sdk, "_install_tracer_provider", _fail)

    assert otel_sdk.init_otel_tracing() is False


def test_setup_failure_does_not_reach_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken exporter or missing SDK must not take a host down at boot."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    def _raise() -> None:
        raise RuntimeError("no exporter")

    monkeypatch.setattr(otel_sdk, "_install_tracer_provider", _raise)

    assert otel_sdk.init_otel_tracing() is False
