"""Typed shapes for service map assets, edges, and history."""

from typing import Any, TypedDict


class Asset(TypedDict, total=False):
    """Service map asset with AWS-native ID and metadata."""

    id: str
    type: str
    name: str
    aws_arn: str | None
    pipeline_context: list[str]
    alert_context: list[str]
    investigation_count: int
    last_investigated: str
    confidence: float
    verification_status: str
    metadata: dict[str, Any]


class Edge(TypedDict, total=False):
    """Directed edge between assets."""

    from_asset: str
    to_asset: str
    type: str
    confidence: float
    verification_status: str
    evidence: str
    first_seen: str
    last_seen: str


class HistoryEntry(TypedDict):
    """Change history entry."""

    timestamp: str
    change_type: str
    asset_id: str | None
    edge_id: str | None
    details: str


class ServiceMap(TypedDict):
    """Complete service map snapshot."""

    enabled: bool
    last_updated: str
    assets: list[Asset]
    edges: list[Edge]
    history: list[HistoryEntry]
