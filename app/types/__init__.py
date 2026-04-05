"""Shared domain types — decoupled from any single module."""

from app.types.evidence import EvidenceSource
from app.types.tools import ToolSurface

__all__ = [
    "EvidenceSource",
    "ToolSurface",
]
