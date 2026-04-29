"""Adaptive-window node — widens the incident window between investigation
iterations when prior tool calls came back empty.

The package is split into two modules:

- ``rules.py`` — pure, side-effect-free decision logic. Operates on plain
  dicts so tests can drive it without spinning up LangGraph. Today it
  contains a single rule: expand-on-empty-deploy-timeline.

- ``node.py`` — the LangGraph entry point ``node_adapt_window``. Wraps
  the rule in ``@traceable`` and adapts the state-delta to LangGraph's
  reducer.
"""

from app.nodes.adapt_window.node import node_adapt_window
from app.nodes.adapt_window.rules import adapt_incident_window

__all__ = ["adapt_incident_window", "node_adapt_window"]
