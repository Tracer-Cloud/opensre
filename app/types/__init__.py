"""Shared domain types — decoupled from any single module."""

from app.types.evidence import EvidenceSource
from app.types.retrieval import (
    AggregationSpec,
    FieldSelection,
    FilterCondition,
    RetrievalControls,
    RetrievalControlsMap,
    RetrievalIntent,
    TimeBounds,
)
from app.types.tools import ToolSurface

__all__ = [
    "EvidenceSource",
    "ToolSurface",
    "RetrievalIntent",
    "RetrievalControls",
    "RetrievalControlsMap",
    "TimeBounds",
    "FilterCondition",
    "FieldSelection",
    "AggregationSpec",
]
