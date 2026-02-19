"""Tests for pipeline-to-pipeline dependency edge inference."""

from app.agent.memory.service_map.pipeline_edges import (
    _CONFIDENCE_INITIAL,
    _CONFIDENCE_MAX,
    infer_feeds_into_edges,
)
from app.agent.memory.service_map.types import ServiceMap


def _make_map(assets: list, edges: list | None = None) -> ServiceMap:
    return {
        "enabled": True,
        "last_updated": "2026-01-01T00:00:00Z",
        "assets": assets,
        "edges": edges or [],
        "history": [],
    }


def test_no_edges_when_single_pipeline_on_asset():
    """S3 bucket used by only one pipeline — no feeds_into edge."""
    smap = _make_map(assets=[
        {
            "id": "s3_bucket:landing",
            "type": "s3_bucket",
            "name": "landing",
            "pipeline_context": ["pipeline_a"],
        }
    ])
    assert infer_feeds_into_edges(smap) == []


def test_no_edges_when_no_s3_assets():
    """Non-S3 shared asset does not produce pipeline edges."""
    smap = _make_map(assets=[
        {
            "id": "ecs_cluster:my-cluster",
            "type": "ecs_cluster",
            "name": "my-cluster",
            "pipeline_context": ["pipeline_a", "pipeline_b"],
        }
    ])
    assert infer_feeds_into_edges(smap) == []


def test_creates_feeds_into_edge_for_shared_s3_bucket():
    """Two pipelines sharing an S3 bucket → feeds_into edge created."""
    smap = _make_map(assets=[
        {
            "id": "s3_bucket:landing",
            "type": "s3_bucket",
            "name": "landing",
            "pipeline_context": ["pipeline_a", "pipeline_b"],
        }
    ])
    edges = infer_feeds_into_edges(smap)
    assert len(edges) == 1
    assert edges[0]["type"] == "feeds_into"
    assert edges[0]["confidence"] == _CONFIDENCE_INITIAL
    assert edges[0]["verification_status"] == "inferred"
    assert "pipeline_a" in edges[0]["from_asset"] or "pipeline_b" in edges[0]["from_asset"]
    assert "pipeline_a" in edges[0]["to_asset"] or "pipeline_b" in edges[0]["to_asset"]
    assert edges[0]["from_asset"] != edges[0]["to_asset"]


def test_direction_from_writes_to_edge():
    """Pipeline with Lambda writing to shared bucket is upstream."""
    smap = _make_map(
        assets=[
            {
                "id": "s3_bucket:landing",
                "type": "s3_bucket",
                "name": "landing",
                "pipeline_context": ["pipeline_a", "pipeline_b"],
            },
            {
                "id": "lambda:trigger_lambda",
                "type": "lambda",
                "name": "trigger_lambda",
                "pipeline_context": ["pipeline_a"],
            },
        ],
        edges=[
            {
                "from_asset": "lambda:trigger_lambda",
                "to_asset": "s3_bucket:landing",
                "type": "writes_to",
                "confidence": 1.0,
                "verification_status": "verified",
                "evidence": "s3_metadata.source",
                "first_seen": "2026-01-01T00:00:00Z",
                "last_seen": "2026-01-01T00:00:00Z",
            }
        ],
    )
    edges = infer_feeds_into_edges(smap)
    assert len(edges) == 1
    assert edges[0]["from_asset"] == "pipeline:pipeline_a"
    assert edges[0]["to_asset"] == "pipeline:pipeline_b"


def test_direction_heuristic_lambda_upstream_ecs_downstream():
    """Lambda pipeline is upstream, ECS pipeline is downstream when no writes_to edge."""
    smap = _make_map(assets=[
        {
            "id": "s3_bucket:landing",
            "type": "s3_bucket",
            "name": "landing",
            "pipeline_context": ["trigger_pipeline", "prefect_pipeline"],
        },
        {
            "id": "lambda:trigger",
            "type": "lambda",
            "name": "trigger",
            "pipeline_context": ["trigger_pipeline"],
        },
        {
            "id": "ecs_cluster:prefect-cluster",
            "type": "ecs_cluster",
            "name": "prefect-cluster",
            "pipeline_context": ["prefect_pipeline"],
        },
    ])
    edges = infer_feeds_into_edges(smap)
    assert len(edges) == 1
    assert edges[0]["from_asset"] == "pipeline:trigger_pipeline"
    assert edges[0]["to_asset"] == "pipeline:prefect_pipeline"


def test_confidence_bumped_on_existing_edge():
    """Re-confirmed edge gets confidence bump toward 0.9."""
    existing_edge = {
        "from_asset": "pipeline:pipeline_a",
        "to_asset": "pipeline:pipeline_b",
        "type": "feeds_into",
        "confidence": 0.6,
        "verification_status": "inferred",
        "evidence": "shared_s3_asset:s3_bucket:landing",
        "first_seen": "2026-01-01T00:00:00Z",
        "last_seen": "2026-01-01T00:00:00Z",
    }
    smap = _make_map(
        assets=[
            {
                "id": "s3_bucket:landing",
                "type": "s3_bucket",
                "name": "landing",
                "pipeline_context": ["pipeline_a", "pipeline_b"],
            },
            {
                "id": "lambda:trigger",
                "type": "lambda",
                "name": "trigger",
                "pipeline_context": ["pipeline_a"],
            },
        ],
        edges=[
            existing_edge,
            {
                "from_asset": "lambda:trigger",
                "to_asset": "s3_bucket:landing",
                "type": "writes_to",
                "confidence": 1.0,
                "verification_status": "verified",
                "evidence": "s3_metadata.source",
                "first_seen": "2026-01-01T00:00:00Z",
                "last_seen": "2026-01-01T00:00:00Z",
            },
        ],
    )
    edges = infer_feeds_into_edges(smap)
    assert len(edges) == 1
    assert edges[0]["confidence"] == round(0.6 + 0.15, 10)
    assert edges[0]["verification_status"] == "inferred"


def test_confidence_caps_at_max_and_marks_verified():
    """Edge at 0.75 gets bumped to 0.9 and marked verified."""
    existing_edge = {
        "from_asset": "pipeline:pipeline_a",
        "to_asset": "pipeline:pipeline_b",
        "type": "feeds_into",
        "confidence": 0.75,
        "verification_status": "inferred",
        "evidence": "shared_s3_asset:s3_bucket:landing",
        "first_seen": "2026-01-01T00:00:00Z",
        "last_seen": "2026-01-01T00:00:00Z",
    }
    smap = _make_map(
        assets=[
            {
                "id": "s3_bucket:landing",
                "type": "s3_bucket",
                "name": "landing",
                "pipeline_context": ["pipeline_a", "pipeline_b"],
            },
            {
                "id": "lambda:trigger",
                "type": "lambda",
                "name": "trigger",
                "pipeline_context": ["pipeline_a"],
            },
        ],
        edges=[
            existing_edge,
            {
                "from_asset": "lambda:trigger",
                "to_asset": "s3_bucket:landing",
                "type": "writes_to",
                "confidence": 1.0,
                "verification_status": "verified",
                "evidence": "s3_metadata.source",
                "first_seen": "2026-01-01T00:00:00Z",
                "last_seen": "2026-01-01T00:00:00Z",
            },
        ],
    )
    edges = infer_feeds_into_edges(smap)
    assert len(edges) == 1
    assert edges[0]["confidence"] == _CONFIDENCE_MAX
    assert edges[0]["verification_status"] == "verified"


def test_no_duplicate_edges_for_same_bucket():
    """Multiple assets for same pipeline pair produce only one feeds_into edge."""
    smap = _make_map(assets=[
        {
            "id": "s3_bucket:landing",
            "type": "s3_bucket",
            "name": "landing",
            "pipeline_context": ["pipeline_a", "pipeline_b"],
        },
        {
            "id": "s3_bucket:processed",
            "type": "s3_bucket",
            "name": "processed",
            "pipeline_context": ["pipeline_a", "pipeline_b"],
        },
    ])
    edges = infer_feeds_into_edges(smap)
    # Should only produce one edge per pipeline pair, not one per shared bucket
    feeds_into = [e for e in edges if e["type"] == "feeds_into"]
    assert len(feeds_into) == 1
