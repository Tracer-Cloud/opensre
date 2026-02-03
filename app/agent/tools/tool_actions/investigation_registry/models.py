"""Investigation action data models."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.agent.state import EvidenceSource


@dataclass
class InvestigationAction:
    """Metadata for an investigation action."""

    name: str
    description: str
    inputs: dict[str, str]  # Parameter name -> description
    outputs: dict[str, str]  # Output field -> description
    use_cases: list[str]  # When to use this action
    requires: list[str]  # Required inputs (e.g., trace_id)
    source: EvidenceSource  # Which source category this belongs to
    function: Callable[..., dict[str, Any]]  # The actual function to call
    availability_check: Callable[[dict[str, dict]], bool] | None = None
    parameter_extractor: Callable[[dict[str, dict]], dict[str, Any]] | None = None
