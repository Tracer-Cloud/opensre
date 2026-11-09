"""The provider-validation result type.

Leaf module — a plain data type with no wizard or SDK imports, so any module
(``validation``, ``azure_openai``, …) can depend on it without forming an import
cycle.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating a provider key."""

    ok: bool
    detail: str
    sample_response: str = ""
