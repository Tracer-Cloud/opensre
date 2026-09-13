"""LLM provider switch tool."""

from __future__ import annotations

from typing import Any

from rich.markup import escape

from config.llm_auth.provider_catalog import PROVIDER_BY_VALUE
from core.agent_harness.tools import (
    ActionToolScope,
    capability_available_from_sources,
    execute_with_action_context,
)
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema
from tools.interactive_shell.shared import allow_tool


def _provider_values() -> tuple[str, ...]:
    return tuple(sorted(PROVIDER_BY_VALUE.keys()))


def _target_property_schema() -> dict[str, Any]:
    provider_values = _provider_values()
    provider_list = ", ".join(provider_values)
    return {
        "description": (
            "Target passed to `/model set <target>`. Use one of the provider names "
            f"({provider_list}) to switch providers, or pass a valid reasoning model "
            "name for the active provider."
        ),
        "oneOf": [
            {"type": "string", "enum": list(provider_values)},
            {"type": "string", "minLength": 1},
        ],
    }


def _apply_model_set_target(target: str, ctx: ActionToolScope) -> bool:
    if ctx.llm_provider_ports is None:
        raise RuntimeError("LLM provider tool requires provider runtime ports")
    return bool(ctx.llm_provider_ports.apply_target(target, ctx.console))


def execute_llm_provider_tool(args: dict[str, Any], ctx: ActionToolScope) -> bool:
    target = str(args.get("target", args.get("provider", ""))).strip()
    if not target:
        return False
    if ctx.llm_provider_ports is None:
        raise RuntimeError("LLM provider tool requires provider runtime ports")
    policy = allow_tool("switch_llm_provider")
    if not ctx.llm_provider_ports.execution_allowed(
        policy=policy,
        session=ctx.session,
        console=ctx.console,
        action_summary=f"/model set {target}",
        confirm_fn=ctx.confirm_fn,
        is_tty=ctx.is_tty,
        action_already_listed=ctx.action_already_listed,
    ):
        return True
    ctx.console.print(f"[bold]$ /model set {escape(target)}[/bold]")
    ok = _apply_model_set_target(target, ctx)
    ctx.session.record("slash", f"/model set {target}", ok=ok)
    return True


def run_llm_provider(*, target: str, context: Any) -> dict[str, Any]:
    return execute_with_action_context({"target": target}, context, execute_llm_provider_tool)


llm_set_provider_tool = RegisteredTool(
    name="llm_set_provider",
    description="Switch the active LLM provider or reasoning model.",
    input_schema=object_schema(
        properties={"target": _target_property_schema()},
        required=("target",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    accepts_runtime_context=True,
    run=run_llm_provider,
    is_available=lambda sources: capability_available_from_sources(sources, "llm_provider"),
)


__all__ = ["execute_llm_provider_tool", "llm_set_provider_tool"]
