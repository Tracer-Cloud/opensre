"""Base class for all investigation tool actions."""

from __future__ import annotations

from abc import ABC
from typing import Any, ClassVar

from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel
from app.types.evidence import EvidenceSource


class ToolMetadata(StrictConfigModel):
    """Strict schema for tool metadata declared on BaseTool subclasses."""

    name: str
    description: str
    input_schema: dict[str, Any]
    source: EvidenceSource
    use_cases: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    outputs: dict[str, str] = Field(default_factory=dict)

    @field_validator("name", "description")
    @classmethod
    def _require_non_empty_strings(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be a non-empty string")
        return normalized


class BaseTool(ABC):
    """Abstract base for all investigation tool actions.

    Each subclass declares metadata as ClassVars and implements ``run()``.
    ``is_available()`` and ``extract_params()`` may be overridden to make the
    tool self-describing — the investigation registry calls these instead of
    the old ``availability_check`` / ``parameter_extractor`` lambdas.

    Instances are directly callable; ``tool(**kwargs)`` delegates to ``run()``.

    Subclasses define ``run()`` with their own explicit signatures for type
    safety and readability.  The method is **not** declared here to avoid
    forcing every subclass into a single ``**kwargs`` signature — the
    ``__call__`` protocol provides the uniform dispatch contract instead.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]  # JSON Schema — consumed by LLM planner
    source: ClassVar[EvidenceSource]
    use_cases: ClassVar[list[str]] = []
    requires: ClassVar[list[str]] = []
    outputs: ClassVar[dict[str, str]] = {}  # Output field -> description (optional, for prompting)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        metadata = cls.metadata()
        cls.name = metadata.name
        cls.description = metadata.description
        cls.input_schema = metadata.input_schema
        cls.source = metadata.source
        cls.use_cases = metadata.use_cases
        cls.requires = metadata.requires
        cls.outputs = metadata.outputs

    @classmethod
    def metadata(cls) -> ToolMetadata:
        """Return validated tool metadata for this subclass."""
        return ToolMetadata.model_validate(
            {
                "name": getattr(cls, "name", ""),
                "description": getattr(cls, "description", ""),
                "input_schema": getattr(cls, "input_schema", {}),
                "source": getattr(cls, "source", ""),
                "use_cases": list(getattr(cls, "use_cases", [])),
                "requires": list(getattr(cls, "requires", [])),
                "outputs": dict(getattr(cls, "outputs", {})),
            }
        )

    @property
    def inputs(self) -> dict[str, str]:
        """Derived from input_schema for backward-compatibility with build_prompt.py."""
        props = self.metadata().input_schema.get("properties", {})
        return {
            param: str(info.get("description", info.get("type", "")))
            for param, info in props.items()
        }

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        return self.run(**kwargs)  # type: ignore[attr-defined, no-any-return]

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
