"""Base class for all investigation tool actions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from app.state import EvidenceSource


class BaseTool(ABC):
    """Abstract base for all investigation tool actions.

    Each subclass declares metadata as ClassVars and implements ``run()``.
    ``is_available()`` and ``extract_params()`` may be overridden to make the
    tool self-describing — the investigation registry calls these instead of
    the old ``availability_check`` / ``parameter_extractor`` lambdas.

    Instances are directly callable; ``tool(**kwargs)`` delegates to ``run()``.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]  # JSON Schema — consumed by LLM planner
    source: ClassVar[EvidenceSource]
    use_cases: ClassVar[list[str]] = []
    requires: ClassVar[list[str]] = []
    outputs: ClassVar[dict[str, str]] = {}  # Output field -> description (optional, for prompting)

    @property
    def inputs(self) -> dict[str, str]:
        """Derived from input_schema for backward-compatibility with build_prompt.py."""
        props = self.input_schema.get("properties", {})
        return {
            param: str(info.get("description", info.get("type", "")))
            for param, info in props.items()
        }

    @abstractmethod
    def run(self, **kwargs: Any) -> dict[str, Any]:
        """Execute the tool and return a result dict."""
        ...

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        return self.run(**kwargs)

    def is_available(self, _sources: dict[str, dict]) -> bool:
        """Return True when required data sources are present.

        Override per tool. Default allows the tool to always run.
        """
        return True

    def extract_params(self, _sources: dict[str, dict]) -> dict[str, Any]:
        """Extract the kwargs to pass to ``run()`` from the available sources.

        Override per tool. Default returns an empty dict.
        """
        return {}
