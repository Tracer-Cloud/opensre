"""Tool decorator and compatibility helper for lightweight tool registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast, overload

from app.tools.base import BaseTool
from app.tools.registered_tool import REGISTERED_TOOL_ATTR, CostTier, RegisteredTool
from app.types.evidence import EvidenceSource

F = TypeVar("F", bound=Callable[..., Any])


@overload
def tool(
    func: BaseTool,
    *,
    name: str | None = None,
    description: str | None = None,
    input_schema: dict[str, Any] | None = None,
    source: EvidenceSource | None = None,
    surfaces: tuple[str, ...] | None = None,
    use_cases: list[str] | None = None,
    requires: list[str] | None = None,
    outputs: dict[str, str] | None = None,
    is_available: Callable[[dict[str, dict]], bool] | None = None,
    extract_params: Callable[[dict[str, dict]], dict[str, Any]] | None = None,
    tags: tuple[str, ...] | None = None,
    cost_tier: CostTier | None = None,
) -> BaseTool:
    pass


@overload
def tool(  # noqa: UP047
    func: F,
    *,
    name: str | None = None,
    description: str | None = None,
    input_schema: dict[str, Any] | None = None,
    source: EvidenceSource | None = None,
    surfaces: tuple[str, ...] | None = None,
    use_cases: list[str] | None = None,
    requires: list[str] | None = None,
    outputs: dict[str, str] | None = None,
    is_available: Callable[[dict[str, dict]], bool] | None = None,
    extract_params: Callable[[dict[str, dict]], dict[str, Any]] | None = None,
    tags: tuple[str, ...] | None = None,
    cost_tier: CostTier | None = None,
) -> F:
    pass


@overload
def tool(  # noqa: UP047
    func: None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    input_schema: dict[str, Any] | None = None,
    source: EvidenceSource | None = None,
    surfaces: tuple[str, ...] | None = None,
    use_cases: list[str] | None = None,
    requires: list[str] | None = None,
    outputs: dict[str, str] | None = None,
    is_available: Callable[[dict[str, dict]], bool] | None = None,
    extract_params: Callable[[dict[str, dict]], dict[str, Any]] | None = None,
    tags: tuple[str, ...] | None = None,
    cost_tier: CostTier | None = None,
) -> Callable[[F], F]:
    pass


def tool(  # noqa: UP047
    func: F | BaseTool | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    input_schema: dict[str, Any] | None = None,
    source: EvidenceSource | None = None,
    surfaces: tuple[str, ...] | None = None,
    use_cases: list[str] | None = None,
    requires: list[str] | None = None,
    outputs: dict[str, str] | None = None,
    is_available: Callable[[dict[str, dict]], bool] | None = None,
    extract_params: Callable[[dict[str, dict]], dict[str, Any]] | None = None,
    tags: tuple[str, ...] | None = None,
    cost_tier: CostTier | None = None,
) -> Any:
    """Register a lightweight function tool or annotate an existing BaseTool.

    Backward compatibility:
    - ``tool(existing_base_tool)`` keeps working as a no-op.
    - ``tool(plain_function)`` with no metadata remains a no-op.
    """

    def should_register_function() -> bool:
        return any([
            name is not None,
            description is not None,
            input_schema is not None,
            source is not None,
            surfaces is not None,
            bool(use_cases),
            bool(requires),
            bool(outputs),
            is_available is not None,
            extract_params is not None,
            bool(tags),
            cost_tier is not None,
        ])

    def attach(target: F | BaseTool) -> F | BaseTool:
        if isinstance(target, BaseTool):
            if surfaces is not None or tags is not None or cost_tier is not None:
                setattr(
                    target,
                    REGISTERED_TOOL_ATTR,
                    RegisteredTool.from_base_tool(
                        target,
                        surfaces=surfaces,
                        tags=tags,
                        cost_tier=cost_tier,
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
                    input_schema=input_schema,
                    source=source,
                    surfaces=surfaces,
                    use_cases=use_cases,
                    requires=requires,
                    outputs=outputs,
                    is_available=is_available,
                    extract_params=extract_params,
                    tags=tags,
                    cost_tier=cost_tier,
                ),
            )
        return target

    if func is None:
        def wrapper(inner: F) -> F:
            return cast(F, attach(inner))

        return wrapper
    return attach(func)
