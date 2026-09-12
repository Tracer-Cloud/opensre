"""Tool execution helpers for the shared LLM tool-calling runtime."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import BaseModel

from core.domain.types.tools import ToolRole
from core.llm.types import ToolCall
from core.tool.contracts import AgentTool, AgentToolContext, RuntimeTool
from infrastructure.observability.errors.boundary import report_exception
from infrastructure.observability.trace.redaction import redact_sensitive
from infrastructure.observability.trace.spans import mark_span_outcome, tool_span

logger = logging.getLogger(__name__)
_TOOL_LOGGER = logging.getLogger("tools")

_UNSET: object = object()


def availability_view(resolved_integrations: dict[str, Any]) -> dict[str, Any]:
    """Convert classified integration configs into dicts tools can consume."""
    view: dict[str, Any] = {}
    for key, value in resolved_integrations.items():
        if key.startswith("_"):
            view[key] = value
            continue
        if isinstance(value, BaseModel):
            item = value.model_dump(exclude_none=True)
            item.setdefault("connection_verified", True)
            view[key] = item
        elif isinstance(value, dict) and value:
            item = dict(value)
            item.setdefault("connection_verified", True)
            view[key] = item
        else:
            view[key] = value
    return view


ToolErrorSeverity = Literal["error", "warning"]


def report_run_error(
    exc: BaseException,
    *,
    tool_name: str,
    source: str,
    component: str,
    method: str | None = None,
    severity: ToolErrorSeverity = "error",
    logger: logging.Logger | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    """Log + Sentry-capture an error swallowed by a tool wrapper.

    ``tool_name`` and ``source`` come from the tool's metadata (the
    ``name=``/``source=`` arguments of ``@tool`` or the corresponding
    ``BaseTool`` ClassVars). ``component`` should identify the call site —
    typically ``"<module>.<function_or_class>"`` — so Sentry groups events
    per tool implementation, not per top-level surface tag.
    """
    tags: dict[str, str] = {
        "surface": "tool",
        "tool_name": tool_name,
        "source": source,
        "component": component,
    }
    if method:
        tags["method"] = method
    report_exception(
        exc,
        logger=logger or _TOOL_LOGGER,
        message=f"Tool {tool_name} failed: {type(exc).__name__}",
        severity=severity,
        tags=tags,
        extras=extras,
    )


@dataclass(frozen=True)
class ToolExecutionResult:
    """Structured result from one tool call."""

    content: str | list[dict[str, Any]]
    details: Any = None
    is_error: bool = False
    terminate: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def provider_content(self) -> str | list[dict[str, Any]]:
        """Return the content that should be sent back to the LLM provider."""
        return self.content

    def compat_payload(self) -> Any:
        """Return the historical raw payload shape used by old call sites."""
        if self.is_error:
            return {"error": self.content}
        return self.details if self.details is not None else self.content


@dataclass(frozen=True)
class ToolExecutionRequest:
    """Validated tool-call data passed to execution hooks."""

    tool_call: ToolCall
    tool: RuntimeTool
    arguments: dict[str, Any]
    source: str
    resolved_integrations: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionPatch:
    """Patch object returned by ``after_tool_call`` hooks."""

    content: str | list[dict[str, Any]] | None = None
    details: Any = _UNSET
    is_error: bool | None = None
    terminate: bool | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class BeforeToolCallResult:
    """Decision object returned by ``before_tool_call`` hooks."""

    approved: bool = False
    blocked: bool = False
    reason: str = ""
    details: Any = None
    terminate: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


BeforeToolCallHook = Callable[[ToolExecutionRequest], BeforeToolCallResult | None]
AfterToolCallHook = Callable[[ToolExecutionRequest, ToolExecutionResult], ToolExecutionPatch | None]
ToolUpdateHook = Callable[[ToolExecutionRequest, Any], None]
BeforeToolBatchHook = Callable[[Sequence[ToolCall]], None]


@dataclass(frozen=True)
class ToolExecutionHooks:
    """Lifecycle hooks around validated runtime tool execution."""

    before_tool_call: BeforeToolCallHook | None = None
    after_tool_call: AfterToolCallHook | None = None
    on_tool_update: ToolUpdateHook | None = None
    # Fired once per provider tool-call list before any call runs (batch replay guards).
    before_tool_batch: BeforeToolBatchHook | None = None


def compose_tool_execution_hooks(
    *candidates: ToolExecutionHooks | None,
) -> ToolExecutionHooks:
    """Run multiple hook sets in order without discarding any lifecycle stage."""
    hooks = tuple(candidate for candidate in candidates if candidate is not None)
    if not hooks:
        return ToolExecutionHooks()

    def before_tool_call(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        decision: BeforeToolCallResult | None = None
        for hook_set in hooks:
            callback = hook_set.before_tool_call
            if callback is None:
                continue
            current = callback(request)
            if current is None:
                continue
            decision = current
            if current.blocked:
                return current
        return decision

    def after_tool_call(
        request: ToolExecutionRequest,
        result: ToolExecutionResult,
    ) -> ToolExecutionPatch | None:
        patched = result
        changed = False
        for hook_set in hooks:
            callback = hook_set.after_tool_call
            if callback is None:
                continue
            patch = callback(request, patched)
            if patch is None:
                continue
            patched = _apply_patch(patched, patch)
            changed = True
        if not changed:
            return None
        return ToolExecutionPatch(
            content=patched.content,
            details=patched.details,
            is_error=patched.is_error,
            terminate=patched.terminate,
            metadata=patched.metadata,
        )

    def on_tool_update(request: ToolExecutionRequest, update: Any) -> None:
        for hook_set in hooks:
            callback = hook_set.on_tool_update
            if callback is not None:
                callback(request, update)

    def before_tool_batch(tool_calls: Sequence[ToolCall]) -> None:
        for hook_set in hooks:
            callback = hook_set.before_tool_batch
            if callback is not None:
                callback(tool_calls)

    return ToolExecutionHooks(
        before_tool_call=before_tool_call,
        after_tool_call=after_tool_call,
        on_tool_update=on_tool_update,
        before_tool_batch=before_tool_batch,
    )


def execute_tool_calls(
    tool_calls: list[ToolCall],
    tools: Sequence[RuntimeTool],
    resolved_integrations: dict[str, Any],
    *,
    hooks: ToolExecutionHooks | None = None,
    tool_resources: dict[str, Any] | None = None,
) -> list[ToolExecutionResult]:
    """Execute provider-requested tools sequentially and return structured results.

    A response may carry one ``ACTION`` (or one ``TURN_ENDING`` call alone), plus
    any ``BOOKKEEPING`` calls next to the action. A response that breaks that
    rule executes nothing: every call gets the same error so the model
    re-issues a single action. Permitted calls run in provider order.
    """

    hooks = hooks or ToolExecutionHooks()
    tool_map = {t.name: t for t in tools}
    violation = response_batch_violation(tool_calls, tool_map)
    if violation is not None:
        logger.debug("tool_batch rejected calls=%s", [tc.name for tc in tool_calls])
        return [
            _error_result(violation, metadata={"tool_name": tc.name, "batch_rejected": True})
            for tc in tool_calls
        ]
    if hooks.before_tool_batch is not None:
        hooks.before_tool_batch(tool_calls)
    tool_sources = availability_view(resolved_integrations)
    runtime_resources = dict(tool_resources or {})

    results: list[ToolExecutionResult] = []
    for tc in tool_calls:
        with tool_span(tc.name, tool_call_id=tc.id) as span_attrs:
            results.append(
                _execute_one_tool_call(
                    tc,
                    tool_map=tool_map,
                    tool_sources=tool_sources,
                    resolved_integrations=resolved_integrations,
                    runtime_resources=runtime_resources,
                    hooks=hooks,
                    span_attrs=span_attrs,
                )
            )
    return results


def tool_role(tool: RuntimeTool | None) -> ToolRole:
    """Return the declared role; an unknown tool counts as an action so it still errors alone."""
    if tool is None:
        return ToolRole.ACTION
    return tool.role


def response_batch_violation(
    tool_calls: Sequence[ToolCall],
    tool_map: Mapping[str, RuntimeTool],
) -> str | None:
    """Explain why one response's tool calls break the one-action rule, or ``None``."""
    if len(tool_calls) <= 1:
        return None
    roles = [tool_role(tool_map.get(tc.name)) for tc in tool_calls]
    requested = ", ".join(tc.name for tc in tool_calls)
    turn_ending = [
        tc.name for tc, role in zip(tool_calls, roles, strict=True) if role is ToolRole.TURN_ENDING
    ]
    if turn_ending:
        return (
            f"Nothing ran: {turn_ending[0]} hands the turn to the user and must be the only "
            f"tool call in a response, but this response requested {len(tool_calls)} "
            f"({requested}). Re-issue {turn_ending[0]} alone, after any other work."
        )
    actions = [
        tc.name for tc, role in zip(tool_calls, roles, strict=True) if role is ToolRole.ACTION
    ]
    if len(actions) != 1:
        return (
            f"Nothing ran: one action per response, but this response requested "
            f"{len(actions)} ({', '.join(actions)}). Re-issue exactly one of them; "
            "bookkeeping calls such as update_plan may accompany it."
        )
    return None


def execute_tools(
    tool_calls: list[ToolCall],
    tools: Sequence[RuntimeTool],
    resolved_integrations: dict[str, Any],
    *,
    on_tool_update: Callable[[ToolCall, Any], None] | None = None,
) -> list[Any]:
    """Compatibility wrapper returning historical raw payloads."""

    hooks: ToolExecutionHooks | None = None
    if on_tool_update is not None:

        def _on_update(request: ToolExecutionRequest, update: Any) -> None:
            on_tool_update(request.tool_call, update)

        hooks = ToolExecutionHooks(on_tool_update=_on_update)
    return [
        result.compat_payload()
        for result in execute_tool_calls(
            tool_calls,
            tools,
            resolved_integrations,
            hooks=hooks,
            tool_resources=None,
        )
    ]


def _unavailable_tool_message(name: str, tool_map: Mapping[str, Any]) -> str:
    """Explain a missing tool so the caller can recover on the next iteration.

    A tool absent from the map is usually configured-but-unavailable (its
    integration lacks a credential this session), not nonexistent — so say
    "not available" and name the siblings that are, rather than leaving the
    model to guess and the user to read a bare identifier.
    """
    family = name.split("_", 1)[0]
    siblings = sorted(
        other for other in tool_map if other != name and other.split("_", 1)[0] == family
    )
    if not siblings:
        return f"tool {name!r} is not available in this session"
    return (
        f"tool {name!r} is not available in this session; available instead: {', '.join(siblings)}"
    )


def _execute_one_tool_call(
    tc: ToolCall,
    *,
    tool_map: dict[str, RuntimeTool],
    tool_sources: dict[str, Any],
    resolved_integrations: dict[str, Any],
    runtime_resources: dict[str, Any],
    hooks: ToolExecutionHooks,
    span_attrs: dict[str, Any],
) -> ToolExecutionResult:
    """Run one validated tool call; record outcome on ``span_attrs``."""
    tool = tool_map.get(tc.name)
    if tool is None:
        mark_span_outcome(span_attrs, "unknown_tool", error=True)
        logger.debug("tool_call unknown name=%s id=%s", tc.name, tc.id)
        return _error_result(
            _unavailable_tool_message(tc.name, tool_map), metadata={"tool_name": tc.name}
        )

    request: ToolExecutionRequest | None = None
    try:
        validation_error = tool.validate_public_input(tc.input)
        if validation_error:
            mark_span_outcome(span_attrs, "validation_error", error=True)
            logger.debug("tool_call validation_error name=%s id=%s", tc.name, tc.id)
            return _error_result(validation_error, metadata={"tool_name": tc.name})

        source = str(getattr(tool, "source", "unknown"))
        span_attrs["source"] = source
        request = ToolExecutionRequest(
            tool_call=tc,
            tool=tool,
            arguments=dict(tc.input),
            source=source,
            resolved_integrations=resolved_integrations,
        )
        before = _run_before_hook(hooks, request)
        if before is not None and before.blocked:
            mark_span_outcome(span_attrs, "blocked", error=True)
            logger.debug("tool_call blocked name=%s id=%s", tc.name, tc.id)
            return ToolExecutionResult(
                content=before.reason or f"{tc.name} blocked by before_tool_call hook.",
                details=before.details,
                is_error=True,
                terminate=before.terminate,
                metadata={"tool_name": tc.name, **before.metadata},
            )

        logger.debug("tool_call start name=%s id=%s source=%s", tc.name, tc.id, source)
        raw = _invoke_runtime_tool(
            tool,
            tc,
            request=request,
            tool_sources=tool_sources,
            resolved_integrations=resolved_integrations,
            runtime_resources=runtime_resources,
            hooks=hooks,
        )
        result = _normalize_result(raw, tool_name=tc.name)
        patch = _run_after_hook(hooks, request, result)
        if patch is not None:
            result = _apply_patch(result, patch)
        mark_span_outcome(
            span_attrs,
            "tool_error" if result.is_error else "ok",
            error=result.is_error,
            is_error=result.is_error,
            terminate=result.terminate,
        )
        logger.debug(
            "tool_call done name=%s id=%s outcome=%s",
            tc.name,
            tc.id,
            span_attrs["outcome"],
        )
        return result
    except Exception as exc:
        mark_span_outcome(span_attrs, "exception", error=True)
        logger.warning("[tool:%s] failed: %s", tc.name, exc)
        result = _error_result(str(exc), metadata={"tool_name": tc.name})
        # Raised transport failures must still reach after_tool_call so gather
        # circuit breakers can mark the source (Grafana used to swallow these
        # as empty lists; once re-raised, skipping the hook would hide them).
        if request is not None:
            patch = _run_after_hook(hooks, request, result)
            if patch is not None:
                result = _apply_patch(result, patch)
        return result


def _invoke_runtime_tool(
    tool: RuntimeTool,
    tc: ToolCall,
    *,
    request: ToolExecutionRequest,
    tool_sources: dict[str, Any],
    resolved_integrations: dict[str, Any],
    runtime_resources: dict[str, Any],
    hooks: ToolExecutionHooks,
) -> Any:
    """Dispatch to AgentTool.execute or RegisteredTool.run."""
    if isinstance(tool, AgentTool):
        context = AgentToolContext(
            resolved_integrations=resolved_integrations,
            resources=runtime_resources,
            _emit_update=lambda update: _run_update_hook(hooks, request, update),
        )
        return tool.execute(tc.input, context)

    injected = tool.extract_params(tool_sources)
    kwargs = {**injected, **tc.input}
    # Vendor-agnostic: each tool declares which extract_params keys must win
    # over model input (secrets / connection fields). See ``injected_params``.
    protected = frozenset(getattr(tool, "injected_params", ()) or ())
    for key, value in injected.items():
        if key in protected and value not in (None, "", []):
            kwargs[key] = value
    if getattr(tool, "accepts_runtime_context", False):
        context = AgentToolContext(
            resolved_integrations=resolved_integrations,
            resources=runtime_resources,
            _emit_update=lambda update: _run_update_hook(hooks, request, update),
        )
        return tool.run(**kwargs, context=context)
    return tool.run(**kwargs)


def _normalize_result(raw: Any, *, tool_name: str) -> ToolExecutionResult:
    if isinstance(raw, ToolExecutionResult):
        return raw
    # Flag failure on a truthy "error", not the mere presence of the key: a
    # success payload carrying "error": None must reach the agent, not be
    # replaced with {"error": "None"}. Matches bedrock_converse's convention.
    is_error = isinstance(raw, dict) and bool(raw.get("error"))
    content = _content_from_payload(raw)
    if is_error:
        content = str(raw.get("error", content))
    return ToolExecutionResult(
        content=content,
        details=raw,
        is_error=is_error,
        metadata={"tool_name": tool_name},
    )


def _content_from_payload(raw: Any) -> str | list[dict[str, Any]]:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list) and all(isinstance(item, dict) for item in raw):
        return raw
    return json.dumps(raw, default=str)


def _error_result(message: str, *, metadata: dict[str, Any] | None = None) -> ToolExecutionResult:
    return ToolExecutionResult(
        content=message,
        details={"error": message},
        is_error=True,
        metadata=dict(metadata or {}),
    )


def _run_before_hook(
    hooks: ToolExecutionHooks,
    request: ToolExecutionRequest,
) -> BeforeToolCallResult | None:
    if hooks.before_tool_call is None:
        return None
    try:
        return hooks.before_tool_call(request)
    except Exception as exc:  # noqa: BLE001 - lifecycle hooks should fail closed for the call
        logger.warning("[tool:%s] before_tool_call failed: %s", request.tool_call.name, exc)
        return BeforeToolCallResult(blocked=True, reason=str(exc))


def _run_after_hook(
    hooks: ToolExecutionHooks,
    request: ToolExecutionRequest,
    result: ToolExecutionResult,
) -> ToolExecutionPatch | None:
    if hooks.after_tool_call is None:
        return None
    try:
        return hooks.after_tool_call(request, result)
    except Exception:  # noqa: BLE001 - observer failures must not corrupt the transcript
        logger.debug(
            "[tool:%s] after_tool_call raised; ignoring",
            request.tool_call.name,
            exc_info=True,
        )
        return None


def _run_update_hook(
    hooks: ToolExecutionHooks,
    request: ToolExecutionRequest,
    update: Any,
) -> None:
    if hooks.on_tool_update is None:
        return
    try:
        hooks.on_tool_update(request, update)
    except Exception:  # noqa: BLE001 - partial rendering must not break tool execution
        logger.debug(
            "[tool:%s] on_tool_update raised; ignoring",
            request.tool_call.name,
            exc_info=True,
        )


def _apply_patch(result: ToolExecutionResult, patch: ToolExecutionPatch) -> ToolExecutionResult:
    metadata = dict(result.metadata)
    if patch.metadata:
        metadata.update(patch.metadata)
    kwargs: dict[str, Any] = {"metadata": metadata}
    if patch.content is not None:
        kwargs["content"] = patch.content
    if patch.details is not _UNSET:
        kwargs["details"] = patch.details
    if patch.is_error is not None:
        kwargs["is_error"] = patch.is_error
    if patch.terminate is not None:
        kwargs["terminate"] = patch.terminate
    return replace(result, **kwargs)


def public_tool_input(value: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_sensitive(value)
    return {
        key: item
        for key, item in redacted.items()
        if item != "[runtime object]" and item != "[redacted]"
    }


def tool_source(tools: Mapping[str, RuntimeTool], tool_name: str) -> str:
    tool = tools.get(tool_name)
    return str(getattr(tool, "source", "unknown")) if tool else "unknown"


def summarise(output: Any) -> str:
    if isinstance(output, ToolExecutionResult):
        output = output.compat_payload()
    if isinstance(output, dict) and "error" in output:
        return f"error: {output['error']}"
    text = json.dumps(output, default=str)
    return text[:120] + "..." if len(text) > 120 else text
