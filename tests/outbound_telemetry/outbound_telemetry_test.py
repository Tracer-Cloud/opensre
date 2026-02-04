from app.outbound_telemetry import get_metrics, get_tracer, init_telemetry


def test_init_telemetry_exposes_tracer_and_metrics():
    telemetry = init_telemetry(service_name="outbound-telemetry-test")
    assert telemetry.tracer is not None
    assert telemetry.metrics is not None
    assert get_tracer() is not None
    assert get_metrics() is not None
