"""Shared runtime tool definition for class-based and function-based tools."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from types import NoneType
from typing import Any, cast, get_args, get_origin, get_type_hints

from app.tools.base import BaseTool, ToolMetadata
from app.types.evidence import EvidenceSource
from app.types.tools import ToolSurface

REGISTERED_TOOL_ATTR = "__opensre_registered_tool__"

_DEFAULT_SURFACES: tuple[ToolSurface, ...] = ("investigation",)
_VALID_SURFACES = set(get_args(ToolSurface))


def _always_available(_sources: dict[str, dict]) -> bool:
    return True


def _extract_no_params(_sources: dict[str, dict]) -> dict[str, Any]:
    return {}


def _normalize_surfaces(surfaces: Iterable[str] | None) -> tuple[ToolSurface, ...]:
    if surfaces is None:
        return _DEFAULT_SURFACES

    normalized: list[ToolSurface] = []
    for raw_surface in surfaces:
        surface = str(raw_surface).strip().lower()
        if surface not in _VALID_SURFACES:
            valid = ", ".join(sorted(_VALID_SURFACES))
            raise ValueError(f"Unsupported tool surface '{surface}'. Expected one of: {valid}.")
        typed_surface = cast(ToolSurface, surface)
        if typed_surface not in normalized:
            normalized.append(typed_surface)

    return tuple(normalized) or _DEFAULT_SURFACES


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


@dataclass
class RegisteredTool:
    """Uniform runtime representation shared by all registered tools."""

    name: str
    description: str
    input_schema: dict[str, Any]
    source: EvidenceSource
    run: Callable[..., Any] = field(repr=False)
    surfaces: tuple[ToolSurface, ...] = _DEFAULT_SURFACES
    use_cases: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    is_available: Callable[[dict[str, dict]], bool] = field(
        default=_always_available,
        repr=False,
    )
    extract_params: Callable[[dict[str, dict]], dict[str, Any]] = field(
        default=_extract_no_params,
        repr=False,
    )
    origin_module: str = ""
    origin_name: str = ""

    def __post_init__(self) -> None:
        metadata = ToolMetadata.model_validate({
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "source": self.source,
            "use_cases": self.use_cases,
            "requires": self.requires,
            "outputs": self.outputs,
        })
        self.name = metadata.name
        self.description = metadata.description
        self.input_schema = metadata.input_schema
        self.source = metadata.source
        self.use_cases = metadata.use_cases
        self.requires = metadata.requires
        self.outputs = metadata.outputs
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

    def __call__(self, **kwargs: Any) -> Any:
        return self.run(**kwargs)

    @classmethod
    def from_base_tool(
        cls,
        tool: BaseTool,
        *,
        surfaces: Iterable[str] | None = None,
    ) -> RegisteredTool:
        metadata = tool.metadata()
        resolved_surfaces = surfaces or getattr(tool, "surfaces", None) or getattr(
            tool.__class__, "surfaces", None
        )
        return cls(
            name=metadata.name,
            description=metadata.description,
            input_schema=metadata.input_schema,
            source=metadata.source,
            use_cases=metadata.use_cases,
            requires=metadata.requires,
            outputs=metadata.outputs,
            surfaces=_normalize_surfaces(resolved_surfaces),
            run=tool.run,  # type: ignore[attr-defined]
            is_available=tool.is_available,
            extract_params=tool.extract_params,
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
        input_schema: dict[str, Any] | None = None,
        source: EvidenceSource | None,
        surfaces: Iterable[str] | None = None,
        use_cases: list[str] | None = None,
        requires: list[str] | None = None,
        outputs: dict[str, str] | None = None,
        is_available: Callable[[dict[str, dict]], bool] | None = None,
        extract_params: Callable[[dict[str, dict]], dict[str, Any]] | None = None,
    ) -> RegisteredTool:
        if source is None:
            raise ValueError("Function tools must declare a source.")

        inferred_description = inspect.getdoc(func) or func.__name__.replace("_", " ")
        return cls(
            name=name or func.__name__,
            description=description or inferred_description,
            input_schema=input_schema or infer_input_schema(func),
            source=source,
            surfaces=_normalize_surfaces(surfaces),
            use_cases=list(use_cases or []),
            requires=list(requires or []),
            outputs=dict(outputs or {}),
            run=func,
            is_available=is_available or _always_available,
            extract_params=extract_params or _extract_no_params,
            origin_module=func.__module__,
            origin_name=func.__name__,
        )

