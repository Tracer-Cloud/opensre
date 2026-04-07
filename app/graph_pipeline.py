"""Compatibility shim for deployed LangGraph entrypoints.

Production deployment config and older internal docs still reference
``app.graph_pipeline:build_graph``. The actual implementation now lives in
``app.pipeline.graph``, so this module preserves that import path.
"""

from __future__ import annotations

from app.pipeline.graph import build_graph

__all__ = ["build_graph"]
