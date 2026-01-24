"""Hypothesis execution node - gather evidence to prove/disprove hypotheses."""

from src.agent.nodes.hypothesis_execution.hypothesis_execution import (
    node_hypothesis_execution,
    node_hypothesis_investigation,  # Backward compatibility alias
)

__all__ = [
    "node_hypothesis_execution",
    "node_hypothesis_investigation",  # Backward compatibility
]
