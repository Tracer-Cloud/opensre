from unittest.mock import MagicMock

from app.services.tracer_client.tracer_logs import TracerLogsMixin


class DummyTracerClient(TracerLogsMixin):
    """Dummy client to isolate and test the TracerLogsMixin."""

    def __init__(self):
        self.org_id = "test_org_123"
        self._get = MagicMock(return_value={"success": True, "data": []})


def test_get_logs_default_size():
    """Cover default size."""
    client = DummyTracerClient()
    client.get_logs()

    client._get.assert_called_once_with(
        "/api/opensearch/logs", {"orgId": "test_org_123", "size": 100}
    )


def test_get_logs_trace_id_precedence():
    """Cover trace_id precedence."""
    client = DummyTracerClient()
    client.get_logs(trace_id="trace_abc", run_id="run_xyz")

    client._get.assert_called_once_with(
        "/api/opensearch/logs", {"orgId": "test_org_123", "size": 100, "runId": "trace_abc"}
    )


def test_get_logs_run_id_fallback():
    """Cover run_id fallback."""
    client = DummyTracerClient()
    client.get_logs(run_id="run_xyz")

    client._get.assert_called_once_with(
        "/api/opensearch/logs", {"orgId": "test_org_123", "size": 100, "runId": "run_xyz"}
    )
