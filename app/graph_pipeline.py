"""Backward-compatibility shim — canonical location is app.pipeline.graph."""

from app.pipeline.graph import agent, build_graph, graph

__all__ = ["agent", "build_graph", "graph"]
