"""Shared domain types and contracts used across orchestration, tools, and state."""

from core.domain.types.config import Configurable, NodeConfig, get_configurable
from core.domain.types.evidence import EvidenceSource
from core.domain.types.retrieval import (
    AggregationSpec,
    FieldSelection,
    FilterCondition,
    RetrievalControls,
    RetrievalControlsMap,
    RetrievalIntent,
    TimeBounds,
)
from core.domain.types.tools import ToolRole, ToolSurface

__all__ = [
    "AggregationSpec",
    "Configurable",
    "EvidenceSource",
    "FieldSelection",
    "FilterCondition",
    "NodeConfig",
    "RetrievalControls",
    "RetrievalControlsMap",
    "RetrievalIntent",
    "TimeBounds",
    "ToolRole",
    "ToolSurface",
    "get_configurable",
]
