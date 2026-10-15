"""Tests for the OTLP observation backend and its opt-in installer.

The sink is exercised against a real OTel SDK ``TracerProvider`` with an
in-memory exporter, so the assertions pin the Langfuse attribute conventions
actually exported, not just that some span was created.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from config.constants.otel import OPENSRE_OTEL_OBSERVATIONS_ENV
from infrastructure.observability.trace.observations import (
    GenerationUsage,
    ObservationKind,
    ObservationLevel,
    TraceAttributes,
    get_observation_sink,
    is_observation_sink_active,
    set_observation_sink,
)
from infrastructure.observability.trace.otel_sink import (
    OtelObservationSink,
    init_otel_observations,
)
from tests.utils.observations import RecordingObservationSink


@pytest.fixture(autouse=True)
def _reset_observation_sink() -> Any:
    set_observation_sink(None)
    yield
    set_observation_sink(None)


def _memory_sink() -> tuple[OtelObservationSink, InMemorySpanExporter, TracerProvider]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return OtelObservationSink(provider), exporter, provider


def _spans_by_name(exporter: InMemorySpanExporter) -> dict[str, Any]:
    return {span.name: span for span in exporter.get_finished_spans()}


def _attrs(span: Any) -> Any:
    attributes = span.attributes
    assert attributes is not None
    return attributes


def test_sink_maps_kinds_inputs_models_and_usage_to_langfuse_attributes() -> None:
    sink, exporter, _ = _memory_sink()
    with (
        sink.observe(
            ObservationKind.AGENT,
            "react_loop",
            input="check alerts",
            metadata={"max_iterations": 5},
        ),
        sink.observe(
            ObservationKind.GENERATION,
            "llm.invoke",
            model="glm-5.3-flash",
            input=[{"role": "user", "content": "hi"}],
        ) as generation,
    ):
        generation.update(
            usage=GenerationUsage(input_tokens=10, output_tokens=4, cache_read_tokens=2)
        )
        with sink.observe(ObservationKind.TOOL, "grafana.alerts", input={"query": "up"}):
            pass

    spans = _spans_by_name(exporter)
    agent = spans["react_loop"]
    generation_span = spans["llm.invoke"]
    tool = spans["grafana.alerts"]

    assert agent.attributes["langfuse.observation.type"] == "agent"
    assert json.loads(agent.attributes["langfuse.observation.input"]) == "check alerts"
    assert agent.attributes["langfuse.observation.metadata.max_iterations"] == 5

    assert generation_span.attributes["langfuse.observation.type"] == "generation"
    assert generation_span.attributes["langfuse.observation.model.name"] == "glm-5.3-flash"
    assert json.loads(generation_span.attributes["langfuse.observation.input"]) == [
        {"role": "user", "content": "hi"}
    ]
    assert json.loads(generation_span.attributes["langfuse.observation.usage_details"]) == {
        "input": 10,
        "output": 4,
        "cache_read_input_tokens": 2,
    }

    assert tool.attributes["langfuse.observation.type"] == "tool"

    # OTel current-context parenting mirrors the observation nesting.
    assert generation_span.parent is not None
    assert tool.parent is not None
    assert generation_span.parent.span_id == agent.context.span_id
    assert tool.parent.span_id == generation_span.context.span_id
    assert agent.parent is None


def test_sink_updates_output_level_and_status_message() -> None:
    sink, exporter, _ = _memory_sink()
    with sink.observe(ObservationKind.SPAN, "turn", input="hello") as observation:
        observation.update(
            output="done",
            metadata={"stop_reason": "end_turn"},
            level=ObservationLevel.WARNING,
            status_message="degraded",
        )

    attrs = _attrs(exporter.get_finished_spans()[0])
    assert json.loads(attrs["langfuse.observation.output"]) == "done"
    assert attrs["langfuse.observation.metadata.stop_reason"] == "end_turn"
    assert attrs["langfuse.observation.level"] == "WARNING"
    assert attrs["langfuse.observation.status_message"] == "degraded"


def test_trace_attributes_stamp_root_and_descendants_only() -> None:
    sink, exporter, _ = _memory_sink()
    trace = TraceAttributes(
        session_id="sess-1",
        user_id="user-9",
        tags=("cli", "scheduled"),
        metadata={"surface": "headless_cli"},
    )
    with (
        sink.observe(ObservationKind.SPAN, "turn", input="hello", trace=trace),
        sink.observe(ObservationKind.TOOL, "tool_a"),
    ):
        pass
    with sink.observe(ObservationKind.TOOL, "outside"):
        pass

    spans = _spans_by_name(exporter)
    for name in ("turn", "tool_a"):
        attrs = spans[name].attributes
        assert attrs["langfuse.session.id"] == "sess-1"
        assert attrs["langfuse.user.id"] == "user-9"
        assert attrs["langfuse.trace.tags"] == ("cli", "scheduled")
        # Trace-level metadata belongs to the root span; descendants carry
        # only the identity attributes Langfuse filters on.
        if name == "turn":
            assert attrs["langfuse.trace.metadata.surface"] == "headless_cli"
        else:
            assert "langfuse.trace.metadata.surface" not in attrs
    assert "langfuse.session.id" not in spans["outside"].attributes


def test_sink_marks_span_error_when_body_raises() -> None:
    sink, exporter, _ = _memory_sink()
    with (
        pytest.raises(RuntimeError, match="boom"),
        sink.observe(ObservationKind.SPAN, "boom_span"),
    ):
        raise RuntimeError("boom")

    span = exporter.get_finished_spans()[0]
    assert span.status.status_code is StatusCode.ERROR
    assert len(span.events) == 1


def test_install_requires_opt_in_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENSRE_OTEL_OBSERVATIONS_ENV, raising=False)
    assert init_otel_observations() is False
    assert not is_observation_sink_active()


def test_install_is_idempotent_and_never_clobbers_active_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENSRE_OTEL_OBSERVATIONS_ENV, "1")
    try:
        assert init_otel_observations() is True
        installed = get_observation_sink()
        assert is_observation_sink_active()

        assert init_otel_observations() is True
        assert get_observation_sink() is installed

        host_sink = RecordingObservationSink()
        set_observation_sink(host_sink)
        assert init_otel_observations() is True
        assert get_observation_sink() is host_sink

        installed.shutdown()
    finally:
        set_observation_sink(None)


def test_init_with_explicit_provider_skips_env_and_never_shuts_the_host_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OPENSRE_OTEL_OBSERVATIONS_ENV, raising=False)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    host_shutdowns: list[bool] = []
    real_shutdown = provider.shutdown

    def _counting_shutdown() -> None:
        host_shutdowns.append(True)
        real_shutdown()

    monkeypatch.setattr(provider, "shutdown", _counting_shutdown)

    # An explicit provider is the opt-in itself: the env flag is not consulted.
    assert init_otel_observations(tracer_provider=provider) is True
    assert is_observation_sink_active()
    with get_observation_sink().observe(ObservationKind.SPAN, "borrowed") as observation:
        observation.update(output="done")
    attrs = _attrs(_spans_by_name(exporter)["borrowed"])
    assert json.loads(attrs["langfuse.observation.output"]) == "done"

    # Borrowed provider: sink shutdown must leave the host's pipeline running.
    get_observation_sink().shutdown()
    assert host_shutdowns == []


def test_cli_profile_boots_otel_observations_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bootstrap import process

    process.reset_process_runtime_for_tests()
    monkeypatch.setenv(OPENSRE_OTEL_OBSERVATIONS_ENV, "1")
    monkeypatch.setattr(process, "bootstrap_opensre_env_once", lambda **_kw: None)
    monkeypatch.setattr("config.skills_auto_update.skills_auto_update_enabled", lambda: False)
    try:
        process.configure_process(process.CLI_PROFILE)
        assert is_observation_sink_active()
    finally:
        sink = get_observation_sink()
        if is_observation_sink_active():
            sink.shutdown()
        set_observation_sink(None)
        process.reset_process_runtime_for_tests()
