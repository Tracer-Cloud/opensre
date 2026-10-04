"""Shared tool-tag constants (vendor-agnostic).

Tools opt into harness behaviors by declaring these tags — core must not
hardcode vendor tool names.
"""

from __future__ import annotations

# Marks a discovery tool whose structured JSON result belongs in the reply as
# prose, not raw. Declarative only: no harness route branches on it.
SUMMARIZE_OBSERVATION_TAG = "summarize_observation"

# Marks a tool as a deterministic last-resort action: eligible
# for selection only when no other tool scores positively for the request.
FALLBACK_PLANNING_TAG = "fallback_planning"

__all__ = ["FALLBACK_PLANNING_TAG", "SUMMARIZE_OBSERVATION_TAG"]
