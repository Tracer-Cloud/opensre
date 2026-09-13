"""Shared contracts for defining and executing tools."""

from __future__ import annotations

import inspect
import logging
from abc import ABC
from collections.abc import Callable, Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property
from types import NoneType, UnionType
from typing import Any, ClassVar, TypeAlias, Union, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel, Field, field_validator

from config.constants.tooling import DEFAULT_APPROVAL_EXPIRY_SECONDS
from config.strict_config import StrictConfigModel
from core.domain.types.evidence import EvidenceMapper, EvidenceSource
from core.domain.types.retrieval import RetrievalControls
from core.domain.types.tools import ToolRole, ToolSurface
from core.tool.registry import BaseToolRegistryMetadata, normalize_surfaces
from infrastructure.observability.errors.boundary import report_exception


def _strip_optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin is None:
        return annotation, False

    args = tuple(arg for arg in get_args(annotation) if arg is not NoneType)
    if len(args) != len(get_args(annotation)):
        if len(args) == 1:
            return args[0], True
        return args, True

    return annotation, False


def _annotation_to_json_schema(annotation: Any) -> dict[str, Any]:
    base_annotation, is_optional = _strip_optional(annotation)
    origin = get_origin(base_annotation)

    if base_annotation in (inspect.Signature.empty, Any):
        schema: dict[str, Any] = {}
    elif base_annotation is str:
        schema = {"type": "string"}
    elif base_annotation is int:
        schema = {"type": "integer"}
    elif base_annotation is float:
        schema = {"type": "number"}
    elif base_annotation is bool:
        schema = {"type": "boolean"}
    elif base_annotation is dict or origin is dict:
        schema = {"type": "object"}
    elif base_annotation is list or origin in (list, set, tuple):
        schema = {"type": "array"}
    elif origin is Union or isinstance(base_annotation, UnionType):  # noqa: UP007
        # Non-optional union (e.g. int | str): emit oneOf over each member.
        schema = {"oneOf": [_annotation_to_json_schema(a) for a in get_args(base_annotation)]}
    elif isinstance(base_annotation, tuple):
        # Residual tuple produced by _strip_optional for X | Y | None with >1 non-None arm.
        schema = {"oneOf": [_annotation_to_json_schema(a) for a in base_annotation]}
    else:
        schema = {"type": "string"}

    if is_optional:
        schema["nullable"] = True
    return schema


def infer_input_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Infer a minimal JSON schema from a function signature."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    type_hints = get_type_hints(func)

    for param in inspect.signature(func).parameters.values():
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        if param.name.startswith("_"):
            continue

        resolved_annotation = type_hints.get(param.name, param.annotation)
        schema = _annotation_to_json_schema(resolved_annotation)
        properties[param.name] = schema

        _, is_optional = _strip_optional(resolved_annotation)
        if param.default is inspect.Signature.empty and not is_optional:
            required.append(param.name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def model_to_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic model to a JSON object schema for tools."""
    schema = model.model_json_schema()
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    schema.setdefault("type", "object")
    if schema.get("type") == "object":
        schema.setdefault("properties", {})
        schema.setdefault("required", [])
        schema.setdefault("additionalProperties", False)
    return schema


def _json_type_matches(value: Any, schema_type: str) -> bool:
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "object":
        return isinstance(value, dict)
    return True


def _value_matches_schema(value: Any, schema: dict[str, Any]) -> bool:
    if value is None and bool(schema.get("nullable")):
        return True

    if "enum" in schema and value not in schema.get("enum", []):
        return False

    one_of = schema.get("oneOf")
    if isinstance(one_of, list) and one_of:
        return any(
            isinstance(option, dict) and _value_matches_schema(value, option) for option in one_of
        )

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        return any(
            isinstance(option, dict) and _value_matches_schema(value, option) for option in any_of
        )

    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return _json_type_matches(value, schema_type)
    if isinstance(schema_type, list):
        return any(
            isinstance(item, str) and _json_type_matches(value, item) for item in schema_type
        )
    return True


_TOOL_LOGGER = logging.getLogger("tools")


def _invoke_tool(
    run_fn: Callable[..., Any],
    *,
    name: str,
    source: str,
    kwargs: dict[str, Any],
) -> Any:
    """Call a tool and convert failures to the shared structured error shape."""
    try:
        return run_fn(**kwargs)
    except Exception as exc:
        report_exception(
            exc,
            logger=_TOOL_LOGGER,
            message=f"Tool {name} failed: {type(exc).__name__}",
            severity="error",
            tags={"surface": "tool", "tool_name": name, "source": source},
        )
        return {"error": str(exc), "exception_type": type(exc).__name__}


class EvidenceType(StrEnum):
    """Closed set of evidence categories a tool result can contribute."""

    LOGS = "logs"
    METRICS = "metrics"
    TRACES = "traces"
    EVENTS = "events"
    TOPOLOGY = "topology"
    DEPLOYMENT_METADATA = "deployment_metadata"
    QUERY_STATS = "query_stats"
    ARTIFACT = "artifact"
    OTHER = "other"


class SideEffectLevel(StrEnum):
    """Closed set of side-effect levels a tool declares."""

    NONE = "none"
    READ_ONLY = "read_only"
    MUTATING = "mutating"
    EXTERNAL = "external"


class ToolMetadata(StrictConfigModel):
    """Strict schema for tool metadata declared on BaseTool subclasses."""

    name: str
    description: str
    display_name: str | None = None
    input_schema: dict[str, Any]
    source: EvidenceSource
    source_id: str | None = None
    evidence_type: EvidenceType | None = None
    side_effect_level: SideEffectLevel | None = None
    use_cases: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    anti_examples: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    outputs: dict[str, str] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    injected_params: list[str] = Field(default_factory=list)
    retrieval_controls: RetrievalControls = Field(
        default_factory=RetrievalControls,
        description="Declares which structured retrieval controls this tool supports",
    )

    @field_validator("name", "description", "display_name")
    @classmethod
    def _require_non_empty_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be a non-empty string")
        return normalized


class BaseTool(ABC):
    """Abstract base class for every registered tool.

    Subclass contract
    -----------------
    * Declare all metadata as **ClassVars** (``name``, ``description``,
      ``input_schema``, ``source``, etc.).  ``__init_subclass__`` validates
      them through ``ToolMetadata`` on class creation, so missing or
      ill-typed declarations fail at import time rather than at runtime.
    * Implement ``run(**kwargs)`` — *not* declared here to avoid forcing a
      fixed signature on every subclass.  The planner invokes the tool
      through ``__call__``, which delegates to ``run`` via
      ``telemetry.invoke_tool`` so exceptions are always captured and
      converted to a structured ``{"error": ..., "exception_type": ...}``
      dict rather than propagating to the agent loop.
    * Override ``is_available`` and ``extract_params`` when the tool
      requires specific data-source checks or needs to pull kwargs from the
      resolved-integration sources dict.
    * Do **not** declare ``run`` with positional arguments — the call site
      always uses keyword arguments: ``tool_instance.run(**kwargs)``.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    display_name: ClassVar[str | None] = None
    input_schema: ClassVar[dict[str, Any]]  # JSON Schema — consumed by LLM planner
    input_model: ClassVar[type[BaseModel] | None] = None
    source: ClassVar[EvidenceSource]
    source_id: ClassVar[str | None] = None
    evidence_type: ClassVar[EvidenceType | None] = None
    side_effect_level: ClassVar[SideEffectLevel | None] = None
    use_cases: ClassVar[Sequence[str]] = ()
    examples: ClassVar[Sequence[str]] = ()
    anti_examples: ClassVar[Sequence[str]] = ()
    requires: ClassVar[Sequence[str]] = ()
    outputs: ClassVar[dict[str, str]] = {}  # Output field -> description (optional, for prompting)
    output_schema: ClassVar[dict[str, Any] | None] = None
    output_model: ClassVar[type[BaseModel] | None] = None
    #: Optional per-tool output→evidence mapper; assign a module-level function.
    evidence_mapper: ClassVar[EvidenceMapper | None] = None
    injected_params: ClassVar[Sequence[str]] = ()
    retrieval_controls: ClassVar[RetrievalControls] = (
        RetrievalControls()
    )  # Declares supported controls
    surfaces: ClassVar[tuple[ToolSurface | str, ...]] = (ToolSurface.CHAT,)
    tags: ClassVar[Sequence[str]] = ()
    role: ClassVar[ToolRole] = ToolRole.ACTION
    requires_approval: ClassVar[bool] = False  # Whether this tool needs approval from messaging
    approval_reason: ClassVar[str] = ""  # Human-readable reason for requiring approval
    approval_expiry_seconds: ClassVar[int] = DEFAULT_APPROVAL_EXPIRY_SECONDS
    accepts_runtime_context: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        metadata = cls.metadata()
        cls.name = metadata.name
        cls.description = metadata.description
        cls.display_name = metadata.display_name
        cls.input_schema = metadata.input_schema
        cls.source = metadata.source
        cls.source_id = metadata.source_id
        cls.evidence_type = metadata.evidence_type
        cls.side_effect_level = metadata.side_effect_level
        cls.use_cases = tuple(metadata.use_cases)
        cls.examples = tuple(metadata.examples)
        cls.anti_examples = tuple(metadata.anti_examples)
        cls.requires = tuple(metadata.requires)
        cls.outputs = metadata.outputs
        cls.output_schema = metadata.output_schema
        cls.injected_params = tuple(metadata.injected_params)
        cls.retrieval_controls = metadata.retrieval_controls
        registry = cls.registry_metadata()
        cls.surfaces = registry.surfaces
        cls.tags = registry.tags
        cls.role = registry.role

    @classmethod
    def metadata(cls) -> ToolMetadata:
        """Return validated tool metadata for this subclass."""
        return ToolMetadata.model_validate(
            {
                "name": getattr(cls, "name", ""),
                "description": getattr(cls, "description", ""),
                "display_name": getattr(cls, "display_name", None),
                "input_schema": getattr(cls, "input_schema", {}),
                "source_id": getattr(cls, "source_id", None),
                "source": getattr(cls, "source", ""),
                "evidence_type": getattr(cls, "evidence_type", None),
                "side_effect_level": getattr(cls, "side_effect_level", None),
                "use_cases": list(getattr(cls, "use_cases", [])),
                "examples": list(getattr(cls, "examples", [])),
                "anti_examples": list(getattr(cls, "anti_examples", [])),
                "requires": list(getattr(cls, "requires", [])),
                "outputs": dict(getattr(cls, "outputs", {})),
                "output_schema": getattr(cls, "output_schema", None),
                "injected_params": list(getattr(cls, "injected_params", [])),
                "retrieval_controls": getattr(cls, "retrieval_controls", RetrievalControls()),
            }
        )

    @classmethod
    def registry_metadata(cls) -> BaseToolRegistryMetadata:
        """Return validated registry/runtime metadata for this subclass."""
        return BaseToolRegistryMetadata.model_validate(
            {
                "surfaces": getattr(cls, "surfaces", ("chat",)),
                "tags": tuple(getattr(cls, "tags", ())),
                "role": getattr(cls, "role", ToolRole.ACTION),
            }
        )

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        return _invoke_tool(self.run, name=self.name, source=str(self.source), kwargs=kwargs)  # type: ignore[attr-defined, no-any-return]

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


REGISTERED_TOOL_ATTR = "__opensre_registered_tool__"

_DEFAULT_SURFACES: tuple[ToolSurface, ...] = (ToolSurface.CHAT,)


def _always_available(_sources: dict[str, dict]) -> bool:
    return True


def _extract_no_params(_sources: dict[str, dict]) -> dict[str, Any]:
    return {}


def _normalize_surfaces(surfaces: Iterable[ToolSurface] | None) -> tuple[ToolSurface, ...]:
    """Backward-compatible alias for registry surface normalization."""
    return normalize_surfaces(surfaces)


@dataclass
class RegisteredTool:
    """Uniform runtime representation shared by all registered tools."""

    name: str
    description: str
    input_schema: dict[str, Any]
    source: EvidenceSource
    run: Callable[..., Any] = field(repr=False)
    display_name: str | None = None
    source_id: str | None = None
    evidence_type: EvidenceType | None = None
    side_effect_level: SideEffectLevel | None = None
    surfaces: tuple[ToolSurface, ...] = _DEFAULT_SURFACES
    use_cases: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    anti_examples: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    evidence_mapper: EvidenceMapper | None = field(default=None, repr=False)
    injected_params: tuple[str, ...] = ()
    retrieval_controls: RetrievalControls = field(
        default_factory=RetrievalControls,
    )
    is_available: Callable[[dict[str, dict]], bool] = field(
        default=_always_available,
        repr=False,
    )
    extract_params: Callable[[dict[str, dict]], dict[str, Any]] = field(
        default=_extract_no_params,
        repr=False,
    )
    tags: tuple[str, ...] = ()
    requires_approval: bool = False
    approval_reason: str = ""
    approval_expiry_seconds: int = DEFAULT_APPROVAL_EXPIRY_SECONDS
    role: ToolRole = ToolRole.ACTION
    accepts_runtime_context: bool = False
    origin_module: str = ""
    origin_name: str = ""
    skill_guidance: str = ""

    def __post_init__(self) -> None:
        metadata = ToolMetadata.model_validate(
            {
                "name": self.name,
                "description": self.description,
                "display_name": self.display_name,
                "input_schema": self.input_schema,
                "source": self.source,
                "source_id": self.source_id,
                "evidence_type": self.evidence_type,
                "side_effect_level": self.side_effect_level,
                "use_cases": self.use_cases,
                "examples": self.examples,
                "anti_examples": self.anti_examples,
                "requires": self.requires,
                "outputs": self.outputs,
                "output_schema": self.output_schema,
                "injected_params": list(self.injected_params),
                "retrieval_controls": self.retrieval_controls,
            }
        )
        self.name = metadata.name
        self.description = metadata.description
        self.display_name = metadata.display_name
        self.input_schema = metadata.input_schema
        self.source = metadata.source
        self.source_id = metadata.source_id
        self.evidence_type = metadata.evidence_type
        self.side_effect_level = metadata.side_effect_level
        self.use_cases = metadata.use_cases
        self.examples = metadata.examples
        self.anti_examples = metadata.anti_examples
        self.requires = metadata.requires
        self.outputs = metadata.outputs
        self.output_schema = metadata.output_schema
        self.injected_params = tuple(metadata.injected_params)
        self.retrieval_controls = metadata.retrieval_controls
        self.surfaces = _normalize_surfaces(self.surfaces)

        if not callable(self.run):
            raise TypeError("run must be callable")
        if not callable(self.is_available):
            raise TypeError("is_available must be callable")
        if not callable(self.extract_params):
            raise TypeError("extract_params must be callable")

    @property
    def inputs(self) -> dict[str, str]:
        props = self.input_schema.get("properties", {})
        return {
            param: str(info.get("description", info.get("type", "")))
            for param, info in props.items()
        }

    @cached_property
    def _public_input_schema(self) -> dict[str, Any]:
        """Deepcopy + prune injected params once; the shared cache behind
        ``public_input_schema``. ``input_schema`` / ``injected_params`` are fixed
        at construction, so this is invariant."""
        schema = deepcopy(self.input_schema)
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return schema
        for injected in self.injected_params:
            properties.pop(injected, None)
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [name for name in required if name not in self.injected_params]
        return schema

    @property
    def public_input_schema(self) -> dict[str, Any]:
        """Return the schema exposed to the model (without injected params).

        The pruned schema is computed once (the expensive recursive deepcopy is
        cached); a shallow copy is returned so callers may reassign or pop
        top-level keys without corrupting the shared cache.
        """
        return dict(self._public_input_schema)

    def validate_public_input(self, payload: dict[str, Any]) -> str | None:
        """Validate model-provided input against this tool's public schema."""
        schema = self.public_input_schema
        if schema.get("type") != "object":
            return f"{self.name} exposes a non-object input schema."
        if not isinstance(payload, dict):
            return f"{self.name} expected object input."

        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        required = schema.get("required")
        if not isinstance(required, list):
            required = []

        missing = [name for name in required if name not in payload]
        if missing:
            return f"{self.name} missing required args: {', '.join(sorted(missing))}."

        if schema.get("additionalProperties") is False:
            extra = sorted(name for name in payload if name not in properties)
            if extra:
                return f"{self.name} got unexpected args: {', '.join(extra)}."

        for key, value in payload.items():
            prop_schema = properties.get(key)
            if not isinstance(prop_schema, dict):
                continue
            if not _value_matches_schema(value, prop_schema):
                return f"{self.name}.{key} has invalid type/value."
        return None

    def __call__(self, **kwargs: Any) -> Any:
        return _invoke_tool(self.run, name=self.name, source=str(self.source), kwargs=kwargs)

    @classmethod
    def from_base_tool(
        cls,
        tool: BaseTool,
        *,
        surfaces: Iterable[ToolSurface] | None = None,
        retrieval_controls: RetrievalControls | None = None,
        tags: tuple[str, ...] | None = None,
        requires_approval: bool | None = None,
        approval_reason: str | None = None,
        approval_expiry_seconds: int | None = None,
        role: ToolRole | None = None,
        accepts_runtime_context: bool | None = None,
        evidence_mapper: EvidenceMapper | None = None,
    ) -> RegisteredTool:
        metadata = tool.metadata()
        input_model = cast(type[BaseModel] | None, getattr(tool, "input_model", None))
        output_model = cast(type[BaseModel] | None, getattr(tool, "output_model", None))
        resolved_input_schema = (
            model_to_json_schema(input_model) if input_model else metadata.input_schema
        )
        resolved_output_schema = (
            model_to_json_schema(output_model) if output_model else metadata.output_schema
        )
        registry = tool.registry_metadata()
        resolved_surfaces = (
            normalize_surfaces(surfaces) if surfaces is not None else registry.surfaces
        )
        resolved_tags = tags if tags is not None else registry.tags
        return cls(
            name=metadata.name,
            description=metadata.description,
            display_name=metadata.display_name,
            input_schema=resolved_input_schema,
            source=metadata.source,
            source_id=metadata.source_id,
            evidence_type=metadata.evidence_type,
            side_effect_level=metadata.side_effect_level,
            use_cases=metadata.use_cases,
            examples=metadata.examples,
            anti_examples=metadata.anti_examples,
            requires=metadata.requires,
            outputs=metadata.outputs,
            output_schema=resolved_output_schema,
            injected_params=tuple(metadata.injected_params),
            retrieval_controls=retrieval_controls or metadata.retrieval_controls,
            surfaces=resolved_surfaces,
            run=tool.run,  # type: ignore[attr-defined]
            evidence_mapper=(
                evidence_mapper
                if evidence_mapper is not None
                else getattr(tool.__class__, "evidence_mapper", None)
            ),
            is_available=tool.is_available,
            extract_params=tool.extract_params,
            tags=resolved_tags,
            requires_approval=bool(
                requires_approval
                if requires_approval is not None
                else tool.__class__.requires_approval
            ),
            approval_reason=str(
                approval_reason if approval_reason is not None else tool.__class__.approval_reason
            ),
            approval_expiry_seconds=int(
                approval_expiry_seconds
                if approval_expiry_seconds is not None
                else tool.__class__.approval_expiry_seconds
            ),
            role=role if role is not None else tool.__class__.role,
            accepts_runtime_context=bool(
                accepts_runtime_context
                if accepts_runtime_context is not None
                else tool.__class__.accepts_runtime_context
            ),
            origin_module=tool.__class__.__module__,
            origin_name=tool.__class__.__name__,
        )

    @classmethod
    def from_function(
        cls,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        input_schema: dict[str, Any] | None = None,
        input_model: type[BaseModel] | None = None,
        source: EvidenceSource | None,
        source_id: str | None = None,
        evidence_type: EvidenceType | None = None,
        side_effect_level: SideEffectLevel | None = None,
        surfaces: Iterable[ToolSurface] | None = None,
        use_cases: list[str] | None = None,
        examples: list[str] | None = None,
        anti_examples: list[str] | None = None,
        requires: list[str] | None = None,
        outputs: dict[str, str] | None = None,
        output_schema: dict[str, Any] | None = None,
        output_model: type[BaseModel] | None = None,
        evidence_mapper: EvidenceMapper | None = None,
        injected_params: tuple[str, ...] | None = None,
        retrieval_controls: RetrievalControls | None = None,
        is_available: Callable[[dict[str, dict]], bool] | None = None,
        extract_params: Callable[[dict[str, dict]], dict[str, Any]] | None = None,
        tags: tuple[str, ...] | None = None,
        requires_approval: bool | None = None,
        approval_reason: str | None = None,
        approval_expiry_seconds: int | None = None,
        role: ToolRole | None = None,
        accepts_runtime_context: bool | None = None,
    ) -> RegisteredTool:
        if source is None:
            raise ValueError("Function tools must declare a source.")

        resolved_input_schema = (
            input_schema
            or (model_to_json_schema(input_model) if input_model is not None else None)
            or infer_input_schema(func)
        )
        resolved_output_schema = output_schema or (
            model_to_json_schema(output_model) if output_model is not None else None
        )
        inferred_description = inspect.getdoc(func) or func.__name__.replace("_", " ")
        return cls(
            name=name or func.__name__,
            description=description or inferred_description,
            display_name=display_name,
            input_schema=resolved_input_schema,
            source=source,
            source_id=source_id,
            evidence_type=evidence_type,
            side_effect_level=side_effect_level,
            surfaces=_normalize_surfaces(surfaces),
            use_cases=list(use_cases or []),
            examples=list(examples or []),
            anti_examples=list(anti_examples or []),
            requires=list(requires or []),
            outputs=dict(outputs or {}),
            output_schema=resolved_output_schema,
            evidence_mapper=evidence_mapper,
            injected_params=tuple(injected_params or ()),
            retrieval_controls=retrieval_controls or RetrievalControls(),
            run=func,
            is_available=is_available or _always_available,
            extract_params=extract_params or _extract_no_params,
            tags=tags or (),
            requires_approval=bool(requires_approval),
            approval_reason=approval_reason or "",
            approval_expiry_seconds=(
                approval_expiry_seconds
                if approval_expiry_seconds is not None
                else DEFAULT_APPROVAL_EXPIRY_SECONDS
            ),
            role=ToolRole.ACTION if role is None else role,
            accepts_runtime_context=bool(accepts_runtime_context),
            origin_module=func.__module__,
            origin_name=func.__name__,
        )


@dataclass(frozen=True)
class AgentToolContext:
    """Resources available while a first-class agent tool executes."""

    resolved_integrations: dict[str, Any]
    resources: dict[str, Any] = field(default_factory=dict)
    _emit_update: Callable[[Any], None] | None = field(default=None, repr=False, compare=False)

    @property
    def on_update(self) -> Callable[[Any], None] | None:
        """Compatibility accessor for older AgentTool implementations."""
        return self._emit_update

    def emit_update(self, update: Any) -> None:
        """Publish a partial tool update to the runtime observer, if one is attached."""
        if self._emit_update is not None:
            self._emit_update(update)


# CodeQL currently misses PEP 695 ``type`` aliases in ``__all__`` export checks.
AgentToolExecutor: TypeAlias = Callable[[dict[str, Any], AgentToolContext], Any]  # noqa: UP040


@dataclass(frozen=True)
class AgentTool:
    """Tool contract executed directly by the shared agent runtime."""

    name: str
    description: str
    input_schema: dict[str, Any]
    execute: AgentToolExecutor
    source: str = "agent"
    role: ToolRole = ToolRole.ACTION
    requires_approval: bool = False
    approval_reason: str = ""
    approval_expiry_seconds: int = DEFAULT_APPROVAL_EXPIRY_SECONDS

    @property
    def public_input_schema(self) -> dict[str, Any]:
        return self.input_schema

    def validate_public_input(self, payload: dict[str, Any]) -> str | None:
        schema = self.public_input_schema
        if schema.get("type") != "object":
            return f"{self.name} exposes a non-object input schema."
        if not isinstance(payload, dict):
            return f"{self.name} expected object input."

        properties = schema.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        required = schema.get("required")
        if not isinstance(required, list):
            required = []

        missing = [name for name in required if name not in payload]
        if missing:
            return f"{self.name} missing required args: {', '.join(sorted(missing))}."

        if schema.get("additionalProperties") is False:
            extra = sorted(name for name in payload if name not in properties)
            if extra:
                return f"{self.name} got unexpected args: {', '.join(extra)}."

        for key, value in payload.items():
            prop_schema = properties.get(key)
            if not isinstance(prop_schema, dict):
                continue
            if not _value_matches_schema(value, prop_schema):
                return f"{self.name}.{key} has invalid type/value."
        return None


# Keep this as an assignment-style alias for the same CodeQL export check.
RuntimeTool: TypeAlias = AgentTool | RegisteredTool  # noqa: UP040

__all__ = [
    "AgentTool",
    "AgentToolContext",
    "AgentToolExecutor",
    "BaseTool",
    "EvidenceType",
    "REGISTERED_TOOL_ATTR",
    "RegisteredTool",
    "RuntimeTool",
    "SideEffectLevel",
    "ToolMetadata",
    "ToolRole",
    "infer_input_schema",
    "model_to_json_schema",
]
