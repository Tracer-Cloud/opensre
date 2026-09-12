"""Tool decorator and compatibility helper for lightweight tool registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast, overload

from pydantic import BaseModel

from core.domain.types.evidence import EvidenceMapper, EvidenceSource
from core.domain.types.retrieval import RetrievalControls
from core.domain.types.tools import ToolRole, ToolSurface
from core.tool.contracts import (
    REGISTERED_TOOL_ATTR,
    BaseTool,
    EvidenceType,
    RegisteredTool,
    SideEffectLevel,
)


@overload
def tool(
    func: BaseTool,
    *,
    name: str | None = None,
    description: str | None = None,
    display_name: str | None = None,
    input_schema: dict[str, Any] | None = None,
    input_model: type[BaseModel] | None = None,
    source: EvidenceSource | None = None,
    source_id: str | None = None,
    evidence_type: EvidenceType | None = None,
    side_effect_level: SideEffectLevel | None = None,
    surfaces: tuple[ToolSurface, ...] | None = None,
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
) -> BaseTool:
    pass


@overload
def tool[F: Callable[..., Any]](
    func: F,
    *,
    name: str | None = None,
    description: str | None = None,
    display_name: str | None = None,
    input_schema: dict[str, Any] | None = None,
    input_model: type[BaseModel] | None = None,
    source: EvidenceSource | None = None,
    source_id: str | None = None,
    evidence_type: EvidenceType | None = None,
    side_effect_level: SideEffectLevel | None = None,
    surfaces: tuple[ToolSurface, ...] | None = None,
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
) -> F:
    pass


@overload
def tool[F: Callable[..., Any]](
    func: None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    display_name: str | None = None,
    input_schema: dict[str, Any] | None = None,
    input_model: type[BaseModel] | None = None,
    source: EvidenceSource | None = None,
    source_id: str | None = None,
    evidence_type: EvidenceType | None = None,
    side_effect_level: SideEffectLevel | None = None,
    surfaces: tuple[ToolSurface, ...] | None = None,
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
) -> Callable[[F], F]:
    pass


def tool[F: Callable[..., Any]](
    func: F | BaseTool | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    display_name: str | None = None,
    input_schema: dict[str, Any] | None = None,
    input_model: type[BaseModel] | None = None,
    source: EvidenceSource | None = None,
    source_id: str | None = None,
    evidence_type: EvidenceType | None = None,
    side_effect_level: SideEffectLevel | None = None,
    surfaces: tuple[ToolSurface, ...] | None = None,
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
) -> Any:
    """Register a lightweight function tool or annotate an existing BaseTool.

    Backward compatibility:
    - ``tool(existing_base_tool)`` keeps working as a no-op.
    - ``tool(plain_function)`` with no metadata remains a no-op.
    """

    def should_register_function() -> bool:
        return any(
            [
                name is not None,
                description is not None,
                display_name is not None,
                input_schema is not None,
                input_model is not None,
                source is not None,
                source_id is not None,
                evidence_type is not None,
                side_effect_level is not None,
                surfaces is not None,
                bool(use_cases),
                bool(examples),
                bool(anti_examples),
                bool(requires),
                bool(outputs),
                output_schema is not None,
                output_model is not None,
                evidence_mapper is not None,
                bool(injected_params),
                retrieval_controls is not None,
                is_available is not None,
                extract_params is not None,
                bool(tags),
                requires_approval is not None,
                approval_reason is not None,
                approval_expiry_seconds is not None,
                role is not None,
                accepts_runtime_context is not None,
            ]
        )

    def attach(target: F | BaseTool) -> F | BaseTool:
        if isinstance(target, BaseTool):
            if (
                surfaces is not None
                or retrieval_controls is not None
                or tags is not None
                or requires_approval is not None
                or approval_reason is not None
                or approval_expiry_seconds is not None
                or role is not None
                or accepts_runtime_context is not None
                or evidence_mapper is not None
            ):
                setattr(
                    target,
                    REGISTERED_TOOL_ATTR,
                    RegisteredTool.from_base_tool(
                        target,
                        surfaces=surfaces,
                        retrieval_controls=retrieval_controls,
                        tags=tags,
                        requires_approval=requires_approval,
                        approval_reason=approval_reason,
                        approval_expiry_seconds=approval_expiry_seconds,
                        role=role,
                        accepts_runtime_context=accepts_runtime_context,
                        evidence_mapper=evidence_mapper,
                    ),
                )
            return target

        if should_register_function():
            setattr(
                target,
                REGISTERED_TOOL_ATTR,
                RegisteredTool.from_function(
                    target,
                    name=name,
                    description=description,
                    display_name=display_name,
                    input_schema=input_schema,
                    input_model=input_model,
                    source=source,
                    source_id=source_id,
                    evidence_type=evidence_type,
                    side_effect_level=side_effect_level,
                    surfaces=surfaces,
                    use_cases=use_cases,
                    examples=examples,
                    anti_examples=anti_examples,
                    requires=requires,
                    outputs=outputs,
                    output_schema=output_schema,
                    output_model=output_model,
                    evidence_mapper=evidence_mapper,
                    injected_params=injected_params,
                    retrieval_controls=retrieval_controls,
                    is_available=is_available,
                    extract_params=extract_params,
                    tags=tags,
                    requires_approval=requires_approval,
                    approval_reason=approval_reason,
                    approval_expiry_seconds=approval_expiry_seconds,
                    role=role,
                    accepts_runtime_context=accepts_runtime_context,
                ),
            )
        return target

    if func is None:

        def wrapper(inner: F) -> F:
            return cast(F, attach(inner))

        return wrapper
    return attach(func)
