"""Memory configuration and feature flags."""

import os


def is_memory_enabled() -> bool:
    """Check if memory system is enabled via env var."""
    return bool(os.getenv("TRACER_MEMORY_ENABLED"))


def get_quality_gate_threshold() -> tuple[float, float]:
    """Get confidence and validity thresholds for memory persistence."""
    return 0.7, 0.7  # confidence_threshold, validity_threshold
