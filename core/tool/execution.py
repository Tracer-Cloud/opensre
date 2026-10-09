"""Tool execution helpers for the shared LLM tool-calling runtime."""

from __future__ import annotations

import contextvars
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from pydantic import BaseModel

from config.constants.tool_params import config_only_params
from config.constants.tooling import (
    MAX_PARALLEL_TOOL_CALLS,
    OPENSRE_PARALLEL_TOOL_CALLS_ENV,
    ToolBlockedBy,
    ToolSkippedBy,
)
from core.domain.types.tools import ToolRole
from core.llm.types import ToolCall
from core.tool.contracts import AgentTool, AgentToolContext, RuntimeTool, SideEffectLevel
from infrastructure.observability.errors.boundary import report_exception
from infrastructure.observability.errors.service import is_service_unreachable
from infrastructure.observability.trace.observations import (
    Observation,
    ObservationLevel,
    is_observation_sink_active,
    observe_tool,
)
from infrastructure.observability.trace.redaction import redact_sensitive
from infrastructure.observability.trace.spans import (
    is_session_trace_active,
    mark_span_outcome,
    tool_span,
)

logger = logging.getLogger(__name__)
_TOOL_LOGGER = logging.getLogger("tools")

_UNSET: object = object()
_EXECUTED_TOOL_OUTCOMES = frozenset({"ok", "tool_error", "exception"})
# Result metadata flags: a before_tool_call hook refused the call, or the call
# was skipped without running because the turn ended or was cancelled first.
_BLOCKED_METADATA_KEY = "blocked"
_SKIPPED_METADATA_KEY = "skipped"
# Side-effect levels whose calls may run at the same time as each other.
_PARALLEL_SIDE_EFFECT_LEVELS = frozenset({SideEffectLevel.NONE, SideEffectLevel.READ_ONLY})
_FALSY_ENV_VALUES = frozenset({"0", "false", "no", "off"})


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
    include_traceback: bool | None = None,
) -> None:
    """Log + Sentry-capture an error swallowed by a tool wrapper.

    ``tool_name`` and ``source`` come from the tool's metadata (the
    ``name=``/``source=`` arguments of ``@tool`` or the corresponding
    ``BaseTool`` ClassVars). ``component`` should identify the call site —
    typically ``"<module>.<function_or_class>"`` — so Sentry groups events
    per tool implementation, not per top-level surface tag.

    A failure whose cause chain ends in an unreachable service (refused, DNS,
    timeout) is a warning without a traceback, as in ``capture_service_error``:
    the shell prints ERROR records, and that stack is only HTTP client internals.
    ``include_traceback`` overrides that default for a failure the caller already
    classified, such as a vendor's own "service unavailable" answer.
    """
    tags: dict[str, str] = {
        "surface": "tool",
        "tool_name": tool_name,
        "source": source,
        "component": component,
    }
    if method:
        tags["method"] = method
    unreachable = is_service_unreachable(exc)
    report_exception(
        exc,
        logger=logger or _TOOL_LOGGER,
        message=f"Tool {tool_name} failed: {type(exc).__name__}",
        severity="warning" if unreachable else severity,
        tags=tags,
        extras=extras,
        include_traceback=not unreachable if include_traceback is None else include_traceback,
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
    should_stop: Callable[[], bool] | None = None,
    on_call_start: Callable[[ToolCall], None] | None = None,
    iteration: int | None = None,
) -> list[ToolExecutionResult]:
    """Execute provider-requested tools and return structured results in provider order.

    A response may carry several calls; they run in provider order. Consecutive
    read-only ``ACTION`` calls (side-effect level ``none`` or ``read_only``)
    form a group whose tool bodies run at the same time, up to
    ``MAX_PARALLEL_TOOL_CALLS`` per group; every other call runs alone.
    ``OPENSRE_PARALLEL_TOOL_CALLS=0`` runs every call alone. Hooks never run
    concurrently and always run on the calling thread: a group runs each
    call's ``before_tool_call`` in provider order, then its bodies at once,
    then each ``after_tool_call`` in provider order. So inside a group a
    ``before_tool_call`` sees the earlier calls of the group as admitted, not
    finished; a guard that counts results must also count the calls it let
    through (see ``core.agent_harness.turns.plan_hooks``).

    A ``TURN_ENDING`` call hands control to the user; only
    ``BOOKKEEPING`` calls may share its response, and they run before it so a
    plan write lands before the menu ends the turn. A response that breaks
    that rule executes nothing: every call gets the same error so the model
    re-issues the menu. Once a result terminates the turn, or ``should_stop``
    reports a host cancel, the calls that have not started are skipped: each
    still gets an error result (providers require one per tool-call id) that
    says it did not run, marked ``metadata.skipped``. Calls of a group whose
    bodies already started keep their real results.

    ``on_call_start`` fires immediately before a call executes — never for a
    skipped call or a rejected batch — so a host can write a durable per-call
    intent record that only ever covers work that actually started.

    ``iteration`` is the loop iteration that requested the calls; analytics
    records it beside each call's position in the response.
    """

    hooks = hooks or ToolExecutionHooks()
    tool_map = {t.name: t for t in tools}
    violation = response_batch_violation(tool_calls, tool_map)
    if violation is not None:
        logger.debug("tool_batch rejected calls=%s", [tc.name for tc in tool_calls])
        for index, tc in enumerate(tool_calls):
            _capture_tool_call_analytics(
                tc,
                tool=tool_map.get(tc.name),
                outcome="batch_rejected",
                is_error=True,
                terminate=False,
                duration_ms=0,
                error_message=violation,
                iteration=iteration,
                tool_call_index=index,
            )
        return [
            _error_result(violation, metadata={"tool_name": tc.name, "batch_rejected": True})
            for tc in tool_calls
        ]
    if hooks.before_tool_batch is not None:
        hooks.before_tool_batch(tool_calls)
    batch = _Batch(
        tool_calls=tool_calls,
        tool_map=tool_map,
        tool_sources=availability_view(resolved_integrations),
        resolved_integrations=resolved_integrations,
        runtime_resources=dict(tool_resources or {}),
        hooks=hooks,
        on_call_start=on_call_start,
        iteration=iteration,
    )
    stop = _BatchStop(should_stop)
    results: dict[int, ToolExecutionResult] = {}
    for group in _execution_groups(tool_calls, tool_map):
        if len(group) > 1:
            results.update(_run_parallel_group(batch, group, stop))
            continue
        index = group[0]
        if stop.check():
            results[index] = _skip_call(batch, index, stop)
            continue
        result = _run_call(batch, index)
        results[index] = result
        stop.note(tool_calls[index], result)
    return [results[index] for index in range(len(tool_calls))]


@dataclass(frozen=True, slots=True)
class _Batch:
    """What every call of one provider response shares while it executes."""

    tool_calls: Sequence[ToolCall]
    tool_map: dict[str, RuntimeTool]
    tool_sources: dict[str, Any]
    resolved_integrations: dict[str, Any]
    runtime_resources: dict[str, Any]
    hooks: ToolExecutionHooks
    on_call_start: Callable[[ToolCall], None] | None
    iteration: int | None


class _BatchStop:
    """Why the calls that have not started are skipped, once one is."""

    def __init__(self, should_stop: Callable[[], bool] | None) -> None:
        self._should_stop = should_stop
        self.reason: str | None = None
        self.skipped_by: ToolSkippedBy | None = None

    def check(self) -> bool:
        """True when the next call must be skipped; polls the host cancel."""
        if self.reason is None and self._should_stop is not None and self._should_stop():
            self.reason = "the turn was cancelled"
            self.skipped_by = ToolSkippedBy.HOST_CANCEL
        return self.reason is not None

    def note(self, tool_call: ToolCall, result: ToolExecutionResult) -> None:
        """Stop the rest of the batch when ``result`` ended the turn."""
        if result.terminate and self.reason is None:
            self.reason = f"{tool_call.name} ended the turn"
            self.skipped_by = ToolSkippedBy.TURN_TERMINATED


def parallel_tool_calls_enabled() -> bool:
    """Whether read-only calls of one response may run at once (on unless switched off)."""
    return os.getenv(OPENSRE_PARALLEL_TOOL_CALLS_ENV, "").strip().lower() not in _FALSY_ENV_VALUES


def _runs_in_parallel(tool: RuntimeTool | None) -> bool:
    """A known ``ACTION`` tool that declares it changes nothing."""
    if tool is None or tool_role(tool) is not ToolRole.ACTION:
        return False
    return getattr(tool, "side_effect_level", None) in _PARALLEL_SIDE_EFFECT_LEVELS


def _execution_groups(
    tool_calls: Sequence[ToolCall], tool_map: Mapping[str, RuntimeTool]
) -> list[list[int]]:
    """Run order split into groups; a group of several calls runs its bodies at once."""
    order = _execution_order(tool_calls, tool_map)
    if not parallel_tool_calls_enabled():
        return [[index] for index in order]
    groups: list[list[int]] = []
    open_group: list[int] | None = None
    for index in order:
        if not _runs_in_parallel(tool_map.get(tool_calls[index].name)):
            open_group = None
            groups.append([index])
            continue
        if open_group is None or len(open_group) >= MAX_PARALLEL_TOOL_CALLS:
            open_group = []
            groups.append(open_group)
        open_group.append(index)
    return groups


def _execution_order(
    tool_calls: Sequence[ToolCall], tool_map: Mapping[str, RuntimeTool]
) -> list[int]:
    """Call indexes in run order: provider order, with a turn-ending call moved last."""
    ending = {
        index
        for index, tc in enumerate(tool_calls)
        if tool_role(tool_map.get(tc.name)) is ToolRole.TURN_ENDING
    }
    rest = [index for index in range(len(tool_calls)) if index not in ending]
    return [*rest, *sorted(ending)]


@contextmanager
def _call_trace(tc: ToolCall) -> Iterator[tuple[Observation, dict[str, Any]]]:
    """The observation and local trace span that cover one call."""
    with (
        observe_tool(
            tc.name,
            input=public_tool_input(tc.input) if is_observation_sink_active() else None,
            metadata={"tool_call_id": tc.id},
        ) as observation,
        tool_span(tc.name, tool_call_id=tc.id) as span_attrs,
    ):
        yield observation, span_attrs


def _close_call_trace(
    observation: Observation | None, span_attrs: dict[str, Any], result: ToolExecutionResult
) -> None:
    """Put a finished call's outcome on its trace before the trace closes."""
    _trace_error_message(span_attrs, result)
    if observation is None:
        return
    observation.update(
        output=result.compat_payload(),
        level=ObservationLevel.ERROR if result.is_error else None,
        metadata={"is_error": result.is_error, "terminate": result.terminate},
    )


def _run_call(batch: _Batch, index: int) -> ToolExecutionResult:
    """Run one call start to finish on the calling thread."""
    tc = batch.tool_calls[index]
    if batch.on_call_start is not None:
        batch.on_call_start(tc)
    started = time.monotonic()
    with _call_trace(tc) as (observation, span_attrs):
        result = _execute_one_tool_call(
            tc,
            tool_map=batch.tool_map,
            tool_sources=batch.tool_sources,
            resolved_integrations=batch.resolved_integrations,
            runtime_resources=batch.runtime_resources,
            hooks=batch.hooks,
            span_attrs=span_attrs,
        )
        _close_call_trace(observation, span_attrs, result)
    _capture_finished_call(batch, index, result, span_attrs, time.monotonic() - started)
    return result


def _capture_finished_call(
    batch: _Batch,
    index: int,
    result: ToolExecutionResult,
    span_attrs: Mapping[str, Any],
    elapsed_seconds: float,
) -> None:
    tc = batch.tool_calls[index]
    _capture_tool_call_analytics(
        tc,
        tool=batch.tool_map.get(tc.name),
        outcome=str(span_attrs.get("outcome", "unknown")),
        is_error=result.is_error,
        terminate=result.terminate,
        duration_ms=max(0, round(elapsed_seconds * 1000)),
        details=result.details,
        error_message=_descriptive_tool_error(result),
        span_attrs=span_attrs,
        iteration=batch.iteration,
        tool_call_index=index,
    )


def _skip_call(batch: _Batch, index: int, stop: _BatchStop) -> ToolExecutionResult:
    """Answer a call that never started because the turn ended or was cancelled."""
    tc = batch.tool_calls[index]
    skipped = _skipped_result(tc.name, stop.reason or "the turn ended")
    _capture_tool_call_analytics(
        tc,
        tool=batch.tool_map.get(tc.name),
        outcome="skipped",
        is_error=True,
        terminate=False,
        duration_ms=0,
        error_message=str(skipped.content),
        skipped_by=stop.skipped_by,
        iteration=batch.iteration,
        tool_call_index=index,
    )
    return skipped


@dataclass(slots=True)
class _ParallelCall:
    """One call of a parallel group, handed between the caller and its worker thread.

    The worker holds the call's trace open (so the observation's parent is the
    generation that asked for it) and runs only the tool body. The caller runs
    the hooks, in provider order, against the ``span_attrs`` the worker opened.
    """

    index: int
    trace_open: threading.Event = field(default_factory=threading.Event)
    admitted: threading.Event = field(default_factory=threading.Event)
    body_done: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    observation: Observation | None = None
    span_attrs: dict[str, Any] = field(default_factory=dict)
    request: ToolExecutionRequest | None = None
    raw: Any = None
    error: Exception | None = None
    result: ToolExecutionResult | None = None
    started: float = 0.0
    ended: float = 0.0


def _serialized_updates(hooks: ToolExecutionHooks) -> ToolExecutionHooks:
    """``hooks`` whose ``on_tool_update`` never runs from two bodies at once."""
    update = hooks.on_tool_update
    if update is None:
        return hooks
    lock = threading.Lock()

    def on_tool_update(request: ToolExecutionRequest, value: Any) -> None:
        with lock:
            update(request, value)

    return replace(hooks, on_tool_update=on_tool_update)


def _hold_call_trace(batch: _Batch, call: _ParallelCall, hooks: ToolExecutionHooks) -> None:
    """Worker side of one parallel call: open its trace, run its body, wait to close."""
    tc = batch.tool_calls[call.index]
    try:
        with _call_trace(tc) as (observation, span_attrs):
            call.observation = observation
            call.span_attrs = span_attrs
            call.trace_open.set()
            call.admitted.wait()
            request = call.request
            if request is not None:
                try:
                    call.raw = _invoke_runtime_tool(
                        request.tool,
                        tc,
                        request=request,
                        tool_sources=batch.tool_sources,
                        resolved_integrations=batch.resolved_integrations,
                        runtime_resources=batch.runtime_resources,
                        hooks=hooks,
                    )
                except Exception as exc:  # noqa: BLE001 - reported as the call's result
                    call.error = exc
            call.ended = time.monotonic()
            call.body_done.set()
            call.finished.wait()
    except Exception as exc:  # noqa: BLE001 - the trace failed; the call must still answer
        logger.warning("[tool:%s] parallel call trace failed: %s", tc.name, exc)
        if not call.body_done.is_set():
            call.error = exc
    finally:
        call.ended = call.ended or time.monotonic()
        call.trace_open.set()
        call.body_done.set()


def _run_parallel_group(
    batch: _Batch, group: Sequence[int], stop: _BatchStop
) -> dict[int, ToolExecutionResult]:
    """Run a group's bodies at once; its hooks run here, in provider order."""
    hooks = _serialized_updates(batch.hooks)
    calls = [_ParallelCall(index) for index in group]
    results: dict[int, ToolExecutionResult] = {}
    with ThreadPoolExecutor(max_workers=len(calls), thread_name_prefix="opensre-tool") as pool:
        try:
            for call in calls:
                if stop.check():
                    continue
                tc = batch.tool_calls[call.index]
                if batch.on_call_start is not None:
                    batch.on_call_start(tc)
                call.started = time.monotonic()
                pool.submit(contextvars.copy_context().run, _hold_call_trace, batch, call, hooks)
                call.trace_open.wait()
                prepared = _prepare_tool_call(
                    tc,
                    tool_map=batch.tool_map,
                    resolved_integrations=batch.resolved_integrations,
                    hooks=hooks,
                    span_attrs=call.span_attrs,
                )
                if isinstance(prepared, ToolExecutionResult):
                    call.result = prepared
                    stop.note(tc, prepared)
                else:
                    call.request = prepared
                call.admitted.set()
            for call in calls:
                if not call.admitted.is_set():
                    results[call.index] = _skip_call(batch, call.index, stop)
                    continue
                call.body_done.wait()
                result = call.result
                if call.request is not None:
                    result = _finish_tool_call(
                        call.request,
                        raw=call.raw,
                        error=call.error,
                        hooks=hooks,
                        span_attrs=call.span_attrs,
                    )
                if result is None:
                    result = _error_result(
                        f"{batch.tool_calls[call.index].name} did not run.",
                        metadata={"tool_name": batch.tool_calls[call.index].name},
                    )
                _close_call_trace(call.observation, call.span_attrs, result)
                call.finished.set()
                results[call.index] = result
                _capture_finished_call(
                    batch, call.index, result, call.span_attrs, call.ended - call.started
                )
                stop.note(batch.tool_calls[call.index], result)
        finally:
            # Never leave a worker waiting: a call not yet admitted runs nothing.
            for call in calls:
                if not call.admitted.is_set():
                    call.request = None
                    call.admitted.set()
                call.finished.set()
    return results


def _descriptive_tool_error(result: ToolExecutionResult) -> str:
    """The tool's own account of a failure, never its arguments or evidence."""
    if not result.is_error:
        return ""
    content = result.content
    if isinstance(content, str) and content.strip():
        return content.strip()
    details = result.details
    if isinstance(details, dict):
        error = details.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()
    return ""


def _trace_error_message(span_attrs: dict[str, Any], result: ToolExecutionResult) -> None:
    """Put a failed call's redacted, capped error text on its local trace span."""
    if not result.is_error or not is_session_trace_active():
        return
    description = _descriptive_tool_error(result)
    if not description:
        return
    # Deferred like the analytics capture: importing core.tool must not load
    # the analytics provider stack.
    from infrastructure.analytics.event_properties import bounded_error_message

    span_attrs["error_message"] = bounded_error_message(description)


def _blocked_call_facts(metadata: Mapping[str, Any]) -> dict[str, str]:
    """Which hook refused a call, from the metadata its decision carried."""
    blocker = next((hook for hook in ToolBlockedBy if metadata.get(hook)), None)
    if blocker is None:
        return {}
    if blocker is ToolBlockedBy.HOOK_EXCEPTION:
        return {"blocked_by": blocker.value, "exception_type": str(metadata[blocker])}
    return {"blocked_by": blocker.value}


def _exception_error_class(exc: Exception) -> str | None:
    """The stable failure ``kind`` an expected tool exception declares, if any."""
    kind = getattr(exc, "kind", None)
    return kind if isinstance(kind, str) and kind.strip() else None


def _payload_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


@dataclass(frozen=True, slots=True)
class _FailureFacts:
    """Why one call failed, as analytics records it; never arguments or evidence."""

    blocked_by: str = ""
    exception_type: str = ""
    error_class: str = ""
    unavailable: bool = False
    error_source: str = ""
    setup_command: str = ""


_NO_FAILURE = _FailureFacts()


def _failure_facts(details: Any, span_attrs: Mapping[str, Any]) -> _FailureFacts:
    """Why a call failed, from its span and the tool's own payload.

    The span holds what the executor saw (blocking hook, exception type and
    class). The payload adds the tool's ``error_kind`` and, for the
    ``tool_unavailable`` envelope, its source and setup command.
    """
    # Deferred like the analytics capture: importing the contract (core.tool)
    # must not load the authoring package (core.tool_framework) with it.
    from core.tool_framework.utils.tool_availability import (
        envelope_source_id,
        is_tool_unavailable_envelope,
    )

    payload: Mapping[str, Any] = details if isinstance(details, dict) else {}
    facts = _FailureFacts(
        blocked_by=str(span_attrs.get("blocked_by") or ""),
        exception_type=str(span_attrs.get("exception_type") or ""),
        error_class=str(span_attrs.get("error_class") or _payload_text(payload, "error_kind")),
    )
    if not is_tool_unavailable_envelope(details):
        return facts
    return replace(
        facts,
        unavailable=True,
        error_source=envelope_source_id(details) or "",
        setup_command=_payload_text(details, "setup_command"),
    )


def _capture_tool_call_analytics(
    tool_call: ToolCall,
    *,
    tool: RuntimeTool | None,
    outcome: str,
    is_error: bool,
    terminate: bool,
    duration_ms: int,
    details: Any = None,
    error_message: str = "",
    span_attrs: Mapping[str, Any] | None = None,
    skipped_by: ToolSkippedBy | None = None,
    iteration: int | None = None,
    tool_call_index: int | None = None,
) -> None:
    """Emit product analytics without tool arguments or result evidence.

    A failure includes the tool's descriptive error and why it failed, as far
    as the executor and the tool's payload say. Evidence payloads stay off the
    event; they can contain private data.
    """
    from infrastructure.analytics.capture import capture_agent_tool_call_completed

    work = details.get("work_outcome") if isinstance(details, dict) else None
    status = work.get("status") if isinstance(work, dict) else None
    work_status = (
        status if status in ("noop", "blocked", "failed", "incomplete", "succeeded") else ""
    )
    failure = _failure_facts(details, span_attrs or {}) if is_error else _NO_FAILURE
    recorded_error = error_message.strip() if is_error else ""
    if is_error and not recorded_error:
        # A failure with no text of its own is still recorded as one, so an
        # undescribed error path shows up by name instead of as a blank.
        recorded_error = f"{tool_call.name} failed ({outcome}) without an error message."
    capture_agent_tool_call_completed(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        source=str(getattr(tool, "source", "unknown")),
        role=tool_role(tool).value,
        outcome=outcome,
        executed=outcome in _EXECUTED_TOOL_OUTCOMES,
        is_error=is_error,
        terminate=terminate,
        duration_ms=duration_ms,
        work_status=work_status,
        error_message=recorded_error,
        blocked_by=failure.blocked_by,
        skipped_by=skipped_by.value if skipped_by is not None else "",
        exception_type=failure.exception_type,
        error_class=failure.error_class,
        unavailable=failure.unavailable,
        setup_command=failure.setup_command,
        error_source=failure.error_source,
        iteration=iteration,
        tool_call_index=tool_call_index,
    )


def tool_role(tool: RuntimeTool | None) -> ToolRole:
    """Return the declared role; unknown and legacy tools default to action."""
    if tool is None:
        return ToolRole.ACTION
    role = getattr(tool, "role", None)
    return role if isinstance(role, ToolRole) else ToolRole.ACTION


def response_batch_violation(
    tool_calls: Sequence[ToolCall],
    tool_map: Mapping[str, RuntimeTool],
) -> str | None:
    """Explain why one response's tool calls break the lone-menu rule, or ``None``.

    A turn-ending call may share its response only with bookkeeping calls.
    """
    if len(tool_calls) <= 1:
        return None
    roles = [tool_role(tool_map.get(tc.name)) for tc in tool_calls]
    requested = ", ".join(tc.name for tc in tool_calls)
    turn_ending = [
        tc.name for tc, role in zip(tool_calls, roles, strict=True) if role is ToolRole.TURN_ENDING
    ]
    if not turn_ending:
        return None
    others = [role for role in roles if role is not ToolRole.TURN_ENDING]
    if len(turn_ending) == 1 and all(role is ToolRole.BOOKKEEPING for role in others):
        return None
    return (
        f"Nothing ran: {turn_ending[0]} hands the turn to the user and only bookkeeping "
        f"such as update_plan may share its response, but this response requested "
        f"{len(tool_calls)} ({requested}). Re-issue {turn_ending[0]} after any other work, "
        "with at most the update_plan that marks its step."
    )


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
    prepared = _prepare_tool_call(
        tc,
        tool_map=tool_map,
        resolved_integrations=resolved_integrations,
        hooks=hooks,
        span_attrs=span_attrs,
    )
    if isinstance(prepared, ToolExecutionResult):
        return prepared
    try:
        raw = _invoke_runtime_tool(
            prepared.tool,
            tc,
            request=prepared,
            tool_sources=tool_sources,
            resolved_integrations=resolved_integrations,
            runtime_resources=runtime_resources,
            hooks=hooks,
        )
    except Exception as exc:  # noqa: BLE001 - reported as the call's result
        return _finish_tool_call(prepared, raw=None, error=exc, hooks=hooks, span_attrs=span_attrs)
    return _finish_tool_call(prepared, raw=raw, error=None, hooks=hooks, span_attrs=span_attrs)


def _prepare_tool_call(
    tc: ToolCall,
    *,
    tool_map: Mapping[str, RuntimeTool],
    resolved_integrations: dict[str, Any],
    hooks: ToolExecutionHooks,
    span_attrs: dict[str, Any],
) -> ToolExecutionRequest | ToolExecutionResult:
    """Validate a call and run ``before_tool_call``: the request to run, or its final result."""
    tool = tool_map.get(tc.name)
    if tool is None:
        mark_span_outcome(span_attrs, "unknown_tool", error=True)
        logger.debug("tool_call unknown name=%s id=%s", tc.name, tc.id)
        return _error_result(
            _unavailable_tool_message(tc.name, tool_map), metadata={"tool_name": tc.name}
        )
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
    except Exception as exc:  # noqa: BLE001 - reported as the call's result
        _mark_exception(span_attrs, tc, exc)
        return _error_result(str(exc), metadata={"tool_name": tc.name})

    before = _run_before_hook(hooks, request)
    if before is not None and before.blocked:
        mark_span_outcome(span_attrs, "blocked", error=True, **_blocked_call_facts(before.metadata))
        logger.debug("tool_call blocked name=%s id=%s", tc.name, tc.id)
        return ToolExecutionResult(
            content=before.reason or f"{tc.name} blocked by before_tool_call hook.",
            details=before.details,
            is_error=True,
            terminate=before.terminate,
            metadata={"tool_name": tc.name, **before.metadata, _BLOCKED_METADATA_KEY: True},
        )
    logger.debug("tool_call start name=%s id=%s source=%s", tc.name, tc.id, request.source)
    return request


def _finish_tool_call(
    request: ToolExecutionRequest,
    *,
    raw: Any,
    error: Exception | None,
    hooks: ToolExecutionHooks,
    span_attrs: dict[str, Any],
) -> ToolExecutionResult:
    """Turn a body's return value or exception into the call's result via ``after_tool_call``."""
    tc = request.tool_call
    if error is None:
        try:
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
                "tool_call done name=%s id=%s outcome=%s", tc.name, tc.id, span_attrs["outcome"]
            )
            return result
        except Exception as exc:  # noqa: BLE001 - reported as the call's result
            error = exc
    _mark_exception(span_attrs, tc, error)
    result = _error_result(str(error), metadata={"tool_name": tc.name})
    # Raised transport failures must still reach after_tool_call so gather
    # circuit breakers can mark the source (Grafana used to swallow these
    # as empty lists; once re-raised, skipping the hook would hide them).
    patch = _run_after_hook(hooks, request, result)
    if patch is not None:
        result = _apply_patch(result, patch)
    return result


def _mark_exception(span_attrs: dict[str, Any], tc: ToolCall, exc: Exception) -> None:
    mark_span_outcome(
        span_attrs,
        "exception",
        error=True,
        exception_type=type(exc).__name__,
        error_class=_exception_error_class(exc),
    )
    logger.warning("[tool:%s] failed: %s", tc.name, exc)


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

    kwargs = _model_arguments(tool, tool.extract_params(tool_sources), tc.input)
    if getattr(tool, "accepts_runtime_context", False):
        context = AgentToolContext(
            resolved_integrations=resolved_integrations,
            resources=runtime_resources,
            _emit_update=lambda update: _run_update_hook(hooks, request, update),
        )
        return tool.run(**kwargs, context=context)
    return tool.run(**kwargs)


def _model_arguments(
    tool: Any, injected: Mapping[str, Any], model_input: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge configured values with model input; config-only names come from config alone.

    Other ``extract_params`` keys stay defaults the model may override.
    """
    hidden = config_only_params(
        str(getattr(tool, "name", "")),
        tuple(getattr(tool, "injected_params", ()) or ()),
    )
    allowed_input = {key: value for key, value in model_input.items() if key not in hidden}
    return {**injected, **allowed_input}


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


def _skipped_result(tool_name: str, reason: str) -> ToolExecutionResult:
    """Error result for a call skipped because an earlier call ended the turn."""
    return _error_result(
        f"Not run: {reason}, so this call was skipped. "
        "Re-issue it next turn if it is still needed.",
        metadata={"tool_name": tool_name, _SKIPPED_METADATA_KEY: True},
    )


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
        return BeforeToolCallResult(
            blocked=True,
            reason=str(exc),
            metadata={ToolBlockedBy.HOOK_EXCEPTION: type(exc).__name__},
        )


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


@dataclass(frozen=True, slots=True)
class ToolFailureSummary:
    """How a run's tool calls failed: counts and the last failure's own account.

    Every failed call counts, blocked ones included; a call skipped without
    running is not a failure.
    """

    tool_error_count: int = 0
    blocked_tool_calls: int = 0
    last_failed_tool: str = ""
    last_tool_error: str = ""


def summarize_tool_failures(
    tool_results: Sequence[tuple[ToolCall, ToolExecutionResult]],
) -> ToolFailureSummary:
    """Summarize the failed calls in ``tool_results``, in the order they ran."""
    errors = 0
    blocked = 0
    last_failure: tuple[ToolCall, ToolExecutionResult] | None = None
    for tool_call, result in tool_results:
        if not result.is_error or result.metadata.get(_SKIPPED_METADATA_KEY):
            continue
        errors += 1
        if result.metadata.get(_BLOCKED_METADATA_KEY):
            blocked += 1
        last_failure = (tool_call, result)
    if last_failure is None:
        return ToolFailureSummary()
    tool_call, result = last_failure
    return ToolFailureSummary(
        tool_error_count=errors,
        blocked_tool_calls=blocked,
        last_failed_tool=tool_call.name,
        last_tool_error=_descriptive_tool_error(result),
    )
