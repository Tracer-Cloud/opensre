"""Tests for the pipeline dependency context source."""

from unittest.mock import MagicMock, patch

from app.agent.nodes.build_context.sources.dependency_context import build_context_dependency


def _state(pipeline_name="pipeline_b", org_id="org1", auth_token="tok") -> dict:
    return {
        "pipeline_name": pipeline_name,
        "org_id": org_id,
        "_auth_token": auth_token,
    }


def _service_map_with_feeds_into_edge() -> dict:
    return {
        "enabled": True,
        "last_updated": "2026-01-01T00:00:00Z",
        "assets": [
            {
                "id": "pipeline:pipeline_a",
                "type": "pipeline",
                "name": "pipeline_a",
                "pipeline_context": ["pipeline_a"],
            },
            {
                "id": "pipeline:pipeline_b",
                "type": "pipeline",
                "name": "pipeline_b",
                "pipeline_context": ["pipeline_b"],
            },
        ],
        "edges": [
            {
                "from_asset": "pipeline:pipeline_a",
                "to_asset": "pipeline:pipeline_b",
                "type": "feeds_into",
                "confidence": 0.75,
                "verification_status": "inferred",
                "evidence": "shared_s3_asset:s3_bucket:landing",
                "first_seen": "2026-01-01T00:00:00Z",
                "last_seen": "2026-01-01T00:00:00Z",
            }
        ],
        "history": [],
    }


def test_empty_result_when_service_map_disabled():
    """Returns empty context when service map is disabled."""
    with patch("app.agent.nodes.build_context.sources.dependency_context.is_service_map_enabled", return_value=False):
        result = build_context_dependency(_state())

    assert result.data["upstream_pipelines"] == []
    assert result.data["causal_chain_detected"] is False
    assert result.error is None


def test_empty_result_when_no_pipeline_name():
    """Returns empty context when pipeline_name is missing from state."""
    with patch("app.agent.nodes.build_context.sources.dependency_context.is_service_map_enabled", return_value=True):
        result = build_context_dependency({})

    assert result.data["causal_chain_detected"] is False


def test_empty_result_when_no_feeds_into_edges():
    """Returns empty context when service map has no feeds_into edges for this pipeline."""
    empty_map = {
        "enabled": True,
        "last_updated": "2026-01-01T00:00:00Z",
        "assets": [],
        "edges": [],
        "history": [],
    }
    with (
        patch("app.agent.nodes.build_context.sources.dependency_context.is_service_map_enabled", return_value=True),
        patch("app.agent.nodes.build_context.sources.dependency_context.load_service_map", return_value=empty_map),
    ):
        result = build_context_dependency(_state())

    assert result.data["upstream_pipelines"] == []
    assert result.data["causal_chain_detected"] is False


def test_causal_chain_detected_when_upstream_failed_recently():
    """Detects causal chain when upstream pipeline failed within 2h."""
    smap = _service_map_with_feeds_into_edge()
    mock_run = MagicMock()
    mock_run.status = "failed"
    mock_run.end_time = "2026-01-01T00:30:00+00:00"
    mock_run.start_time = "2026-01-01T00:25:00+00:00"

    mock_client = MagicMock()
    mock_client.get_pipeline_runs.return_value = [mock_run]

    with (
        patch("app.agent.nodes.build_context.sources.dependency_context.is_service_map_enabled", return_value=True),
        patch("app.agent.nodes.build_context.sources.dependency_context.load_service_map", return_value=smap),
        patch("app.agent.nodes.build_context.sources.dependency_context.get_tracer_client_for_org", return_value=mock_client),
        patch("app.agent.nodes.build_context.sources.dependency_context._minutes_ago", return_value=45),
    ):
        result = build_context_dependency(_state())

    assert len(result.data["upstream_pipelines"]) == 1
    assert result.data["upstream_pipelines"][0]["name"] == "pipeline_a"
    assert result.data["causal_chain_detected"] is True
    assert result.data["causal_chain_confidence"] == 0.85


def test_no_causal_chain_when_upstream_ok():
    """No causal chain when upstream pipeline is healthy."""
    smap = _service_map_with_feeds_into_edge()
    mock_run = MagicMock()
    mock_run.status = "completed"
    mock_run.end_time = "2026-01-01T00:30:00+00:00"
    mock_run.start_time = "2026-01-01T00:25:00+00:00"

    mock_client = MagicMock()
    mock_client.get_pipeline_runs.return_value = [mock_run]

    with (
        patch("app.agent.nodes.build_context.sources.dependency_context.is_service_map_enabled", return_value=True),
        patch("app.agent.nodes.build_context.sources.dependency_context.load_service_map", return_value=smap),
        patch("app.agent.nodes.build_context.sources.dependency_context.get_tracer_client_for_org", return_value=mock_client),
        patch("app.agent.nodes.build_context.sources.dependency_context._minutes_ago", return_value=30),
    ):
        result = build_context_dependency(_state())

    assert result.data["causal_chain_detected"] is False
    assert result.data["causal_chain_confidence"] == 0.0
    assert result.data["upstream_pipelines"][0]["status"] == "completed"


def test_graceful_when_tracer_api_unavailable():
    """Returns empty context without crashing when Tracer API is unavailable."""
    smap = _service_map_with_feeds_into_edge()

    with (
        patch("app.agent.nodes.build_context.sources.dependency_context.is_service_map_enabled", return_value=True),
        patch("app.agent.nodes.build_context.sources.dependency_context.load_service_map", return_value=smap),
        patch("app.agent.nodes.build_context.sources.dependency_context.get_tracer_client_for_org", side_effect=RuntimeError("connection refused")),
    ):
        result = build_context_dependency(_state())

    assert result.data["upstream_pipelines"] == []
    assert result.data["causal_chain_detected"] is False
    assert result.error is None


def test_graceful_when_no_org_credentials():
    """Returns empty context when org_id or auth_token is missing."""
    smap = _service_map_with_feeds_into_edge()

    with (
        patch("app.agent.nodes.build_context.sources.dependency_context.is_service_map_enabled", return_value=True),
        patch("app.agent.nodes.build_context.sources.dependency_context.load_service_map", return_value=smap),
    ):
        result = build_context_dependency({"pipeline_name": "pipeline_b"})

    assert result.data["upstream_pipelines"] == []
    assert result.data["causal_chain_detected"] is False
