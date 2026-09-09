"""The ReAct loop: reason, act (call tools), observe results, repeat.

``ReactLoop`` runs the loop. Each pass asks the LLM what to do (reason); if it
requests tools, they run and their results are fed back in (act + observe); this
repeats until the LLM answers with no tool calls or a safety limit is hit. The
loop knows nothing about ``Agent`` — it takes an ``AgentRunInput``
(``core.agent.run_io``, the resolved inputs) and a ``LoopHost``
(``core.agent.loop_host``, the callbacks it needs), so anything implementing that
host can drive it. ``run_react_loop`` is the one-line functional entry.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any

from core.agent.cancel import tool_resources_cancel_requested
from core.agent.loop_host import LoopHost
from core.agent.run_io import AgentRunInput, AgentRunResult
from core.context_budget import (
    context_budget_ceiling_for_model,
    enforce_context_budget,
    system_and_tools_overhead,
)
from core.events import (
    AgentEndEvent,
    AgentStartEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ProviderRequestEndEvent,
    ProviderRequestStartEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from core.llm.types import ToolCall
from core.messages import AssistantRuntimeMessage, MessageMapper, UserRuntimeMessage
from core.provider import ProviderRequest
from core.tool.contracts import RuntimeTool
from core.tool.execution import (
    ToolExecutionHooks,
    ToolExecutionRequest,
    ToolExecutionResult,
    execute_tool_calls,
    public_tool_input,
)
from infrastructure.observability.operations_log import record_operation
from infrastructure.observability.trace.redaction import redact_sensitive
from infrastructure.observability.trace.spans import (
    llm_span,
    loop_iteration_span,
    loop_span,
    mark_span_outcome,
)

logger = logging.getLogger(__name__)

_SAFETY_HANDOFF_PROMPT = """\
The tool loop has stopped for safety ({reason}). Tools are disabled for this response.
Give the user a concise final handoff based only on the work and tool results above:
summarize useful partial results, state what prevented completion, and give the next
practical step. Do not claim the task completed and do not request or describe another
tool call.
"""
_ITERATION_CAP_FALLBACK = (
    "I reached the emergency tool-iteration ceiling. Partial results are preserved, "
    "but I could not complete the request. Continue with a narrower next step."
)
_STAGNATION_FALLBACK = (
    "I stopped after repeated tool calls produced no new result. Partial results are "
    "preserved, but I could not complete the request. Change the inputs or tool strategy "
    "before continuing."
)


def _update_fingerprint(digest: Any, value: Any) -> None:
    """Feed a stable, bounded-memory representation of a tool value into ``digest``."""
    if isinstance(value, dict):
        digest.update(b"{")
        for key in sorted(value, key=repr):
            _update_fingerprint(digest, key)
            _update_fingerprint(digest, value[key])
        digest.update(b"}")
        return
    if isinstance(value, (list, tuple)):
        digest.update(b"[")
        for item in value:
            _update_fingerprint(digest, item)
        digest.update(b"]")
        return
    if isinstance(value, (set, frozenset)):
        digest.update(b"<")
        for item in sorted(value, key=repr):
            _update_fingerprint(digest, item)
        digest.update(b">")
        return
    digest.update(type(value).__qualname__.encode("utf-8", errors="replace"))
    digest.update(b":")
    digest.update(repr(value).encode("utf-8", errors="replace"))
    digest.update(b";")


def _observation_fingerprint(
    tool_calls: list[ToolCall],
    results: list[ToolExecutionResult],
) -> bytes:
    """Identify one tool batch by public inputs and the observations it produced."""
    digest = sha256()
    for tool_call, result in zip(tool_calls, results, strict=True):
        _update_fingerprint(digest, tool_call.name)
        _update_fingerprint(digest, public_tool_input(tool_call.input))
        _update_fingerprint(digest, result.is_error)
        # Repeating a failed call with identical input is not progress even when
        # the provider decorates each error with a fresh request id or timestamp.
        if not result.is_error:
            _update_fingerprint(digest, result.provider_content())
    return digest.digest()


@dataclass(frozen=True, slots=True)
class _IterationResult:
    """Outcome summary for one loop iteration."""

    should_stop: bool
    outcome: str
    requested_tool_count: int = 0
    tool_error_count: int = 0
    terminated_tool_count: int = 0


class ReactLoop[RuntimeToolT: RuntimeTool]:
    """Runs one ReAct loop over a single ``AgentRunInput``.

    The per-run state — the running message list, the tool results, whether a
    tool ended the turn — lives in the instance fields; ``run()`` drives it to
    completion. The loop never decides things like which tools to expose or when
    to stop; it asks the ``LoopHost`` at each of those points.
    """

    def __init__(
        self,
        run_input: AgentRunInput[RuntimeToolT],
        host: LoopHost[RuntimeToolT],
    ) -> None:
        self._host = host
        self._llm = run_input.llm
        self._system = run_input.system
        self._resolved = run_input.resolved
        self._tool_resources = run_input.tool_resources
        self._max_iterations = run_input.max_iterations
        self._max_stagnant_iterations = run_input.max_stagnant_iterations
        self._messages = run_input.messages
        self._msg_formatter = MessageMapper(self._llm)
        self._runtime_tools = list(host._filter_tools(run_input.tools))
        self._tool_schemas = self._llm.tool_schemas(self._runtime_tools)
        self._ceiling = context_budget_ceiling_for_model(getattr(self._llm, "_model", None))
        # System prompt and tool schemas are fixed for the run; serialize once.
        self._fixed_overhead_tokens = system_and_tools_overhead(self._system, self._tool_schemas)
        self._executed: list[tuple[ToolCall, Any]] = []
        self._tool_results: list[tuple[ToolCall, ToolExecutionResult]] = []
        self._final_text = ""
        self._final_system_prompt = self._system
        self._hit_cap = True
        self._terminated_by_tool = False
        self._cancelled = False
        self._iterations_used = 0
        # Provider-reported usage summed over every model call of this run.
        self._input_tokens = 0
        self._output_tokens = 0
        self._stop_reason = "iteration_cap"
        self._seen_observations: set[bytes] = set()
        self._stagnant_iterations = 0
        self._safety_handoff_attempted = False
        self._operation_run_id = uuid.uuid4().hex[:12]

    def run(self) -> AgentRunResult:
        """Drive the loop to completion and return its outcome."""
        self._host._emit_runtime(
            AgentStartEvent(
                data={
                    "tool_count": len(self._runtime_tools),
                    "max_iterations": self._max_iterations,
                    "message_count": len(self._messages),
                }
            )
        )
        self._record_loop_operation("agent_loop_started")
        with loop_span(
            "react_loop",
            attributes={
                "max_iterations": self._max_iterations,
                "initial_message_count": len(self._messages),
                "tool_count": len(self._runtime_tools),
                "tool_schema_count": len(self._tool_schemas),
            },
        ) as loop_attrs:
            try:
                if self._cancel_requested():
                    self._mark_cancelled()
                else:
                    for iteration in range(self._max_iterations):
                        if self._cancel_requested():
                            self._mark_cancelled()
                            break
                        iteration_result = self._run_iteration(iteration)
                        if iteration_result.should_stop:
                            break
                if self._hit_cap and not self._cancelled and not self._terminated_by_tool:
                    if self._cancel_requested():
                        self._mark_cancelled()
                    else:
                        self._run_safety_handoff()
                run_result = self._finalize()
                self._mark_loop_span(loop_attrs)
                self._record_loop_finished()
                return run_result
            except KeyboardInterrupt as exc:
                self._mark_cancelled()
                self._mark_loop_error(loop_attrs, exc)
                self._record_loop_finished(error=exc)
                raise
            except BaseException as exc:
                self._hit_cap = False
                self._stop_reason = "error"
                self._mark_loop_error(loop_attrs, exc)
                self._record_loop_finished(error=exc)
                raise
            finally:
                note_progress = getattr(self._host, "_note_react_run_progress", None)
                if callable(note_progress):
                    note_progress(
                        iterations_used=self._iterations_used,
                        executed=list(self._executed),
                        hit_iteration_cap=self._hit_cap,
                    )

    def _run_iteration(self, iteration: int) -> _IterationResult:
        """Run one think -> observe step."""
        with loop_iteration_span(
            "react_iteration",
            iteration=iteration,
            attributes={
                "max_iterations": self._max_iterations,
                "message_count_start": len(self._messages),
                "executed_count_start": len(self._executed),
            },
        ) as span_attrs:
            try:
                result = self._run_iteration_body(iteration)
            except BaseException as exc:
                mark_span_outcome(
                    span_attrs,
                    "error",
                    error=True,
                    exception_type=type(exc).__name__,
                    message_count=len(self._messages),
                    executed_count=len(self._executed),
                )
                self._record_loop_operation(
                    "agent_loop_iteration_failed",
                    {
                        "iteration": iteration,
                        "exception_type": type(exc).__name__,
                        "message_count": len(self._messages),
                        "executed_count": len(self._executed),
                    },
                )
                raise
            self._mark_iteration_span(span_attrs, result)
            self._record_loop_operation(
                "agent_loop_iteration",
                {
                    "iteration": iteration,
                    "outcome": result.outcome,
                    "should_stop": result.should_stop,
                    "requested_tool_count": result.requested_tool_count,
                    "tool_error_count": result.tool_error_count,
                    "terminated_tool_count": result.terminated_tool_count,
                    "message_count": len(self._messages),
                    "executed_count": len(self._executed),
                },
            )
            return result

    def _run_iteration_body(self, iteration: int) -> _IterationResult:
        """Run one loop body after the tracing envelope has been opened."""
        if self._cancel_requested():
            self._mark_cancelled()
            return _IterationResult(should_stop=True, outcome="cancelled")
        self._iterations_used = iteration + 1
        self._host._drain_steering_messages(self._messages)
        self._host._emit_runtime(
            TurnStartEvent(
                iteration=iteration,
                data={
                    "message_count": len(self._messages),
                    "tool_count": len(self._runtime_tools),
                    "max_iterations": self._max_iterations,
                    "executed_count": len(self._executed),
                },
            )
        )
        response = self._think(iteration)
        assistant_message = self._msg_formatter.to_assistant_runtime_message(response)
        self._host._emit_runtime(MessageStartEvent(message=assistant_message, iteration=iteration))
        if response.content:
            # ``has_tool_calls`` lets renderers distinguish intermediate
            # commentary preceding this iteration's tool calls (render live)
            # from the final no-tool-call answer (streamed as final_text).
            self._host._emit_runtime(
                MessageUpdateEvent(
                    message=assistant_message,
                    delta=response.content,
                    iteration=iteration,
                    data={"has_tool_calls": response.has_tool_calls},
                )
            )
        self._messages.append(assistant_message)

        if not response.has_tool_calls:
            return self._handle_conclusion(response, assistant_message, iteration)
        return self._observe(response, assistant_message, iteration)

    def _think(
        self,
        iteration: int,
        *,
        tool_schemas: list[dict[str, Any]] | None = None,
        request_kind: str = "work",
    ) -> Any:
        """Build the request, apply the provider hooks, and call the LLM."""
        request_tools = self._tool_schemas if tool_schemas is None else tool_schemas
        transformed_messages = self._host._transform_messages(self._messages)
        llm_messages = self._host._convert_to_llm(self._llm, transformed_messages)
        enforce_context_budget(
            llm_messages,
            fixed_overhead_tokens=self._fixed_overhead_tokens,
            ceiling=self._ceiling,
        )
        provider_request = ProviderRequest(
            messages=llm_messages,
            system=self._system,
            tools=request_tools,
            metadata={"iteration": iteration, "request_kind": request_kind},
        )
        provider_request = self._host._before_request(provider_request)
        if tool_schemas is not None:
            # A provider hook may normally add tools. The safety handoff is the
            # one exception: it must be incapable of starting more work.
            provider_request = replace(provider_request, tools=list(tool_schemas))
        self._final_system_prompt = provider_request.system or self._system
        self._host._emit_runtime(
            ProviderRequestStartEvent(
                iteration=iteration,
                message_count=len(provider_request.messages),
                data={
                    "tool_schema_count": len(provider_request.tools or []),
                    "fixed_overhead_tokens": self._fixed_overhead_tokens,
                },
            )
        )
        model_name = str(getattr(self._llm, "model_id", None) or "invoke")
        with llm_span(
            model_name,
            iteration=iteration,
            attributes={
                "message_count": len(provider_request.messages),
                "tool_schema_count": len(provider_request.tools or []),
            },
        ) as span_attrs:
            response = self._llm.invoke(
                provider_request.messages,
                system=provider_request.system,
                tools=provider_request.tools,
            )
            span_attrs["has_tool_calls"] = response.has_tool_calls
            span_attrs["tool_call_count"] = len(response.tool_calls)
            span_attrs["content_chars"] = len(response.content or "")
        input_tokens = int(getattr(response, "input_tokens", 0) or 0)
        output_tokens = int(getattr(response, "output_tokens", 0) or 0)
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        response = self._host._after_response(provider_request, response)
        self._host._emit_runtime(
            ProviderRequestEndEvent(
                iteration=iteration,
                has_tool_calls=response.has_tool_calls,
                data={
                    "tool_call_count": len(response.tool_calls),
                    "content_chars": len(response.content or ""),
                    # Per call, so a run that raises later still reported this spend.
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            )
        )
        return response

    def _handle_conclusion(
        self, response: Any, assistant_message: Any, iteration: int
    ) -> _IterationResult:
        """Accept a no-tool reply, or nudge and continue when the host rejects it."""
        follow_up = self._host._pop_follow_up_message()
        if follow_up is not None:
            self._messages.append(UserRuntimeMessage(content=follow_up))
            self._host._emit_runtime(
                TurnEndEvent(
                    iteration=iteration,
                    message=assistant_message,
                    data={"accepted": False, "queued_follow_up": True},
                )
            )
            return _IterationResult(should_stop=False, outcome="conclusion_deferred")

        accept, nudge = self._host._should_accept_conclusion(
            evidence_count=len(self._executed),
            iteration=iteration,
            final_text=response.content or "",
        )
        if not accept:
            nudge_text = (nudge or "").strip() or (
                "Continue working toward the goal; do not end the turn yet."
            )
            self._messages.append(UserRuntimeMessage(content=nudge_text))
            self._host._emit_runtime(
                TurnEndEvent(
                    iteration=iteration,
                    message=assistant_message,
                    data={"accepted": False, "goal_nudge": True},
                )
            )
            return _IterationResult(should_stop=False, outcome="conclusion_deferred")

        self._host._emit_runtime(
            TurnEndEvent(
                iteration=iteration,
                message=assistant_message,
                data={"accepted": True},
            )
        )
        self._final_text = response.content or ""
        self._hit_cap = False
        self._stop_reason = "no_tools_needed" if not self._executed else "completed"
        return _IterationResult(should_stop=True, outcome=self._stop_reason)

    def _observe(self, response: Any, assistant_message: Any, iteration: int) -> _IterationResult:
        """Execute the requested tools, record results, and emit events."""
        requested_tool_count = len(response.tool_calls)
        for index, tc in enumerate(response.tool_calls):
            self._host._emit_runtime(
                ToolExecutionStartEvent(
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    args=public_tool_input(tc.input),
                    iteration=iteration,
                    data={
                        "tool_call_index": index,
                        "tool_call_count": requested_tool_count,
                    },
                )
            )

        def on_tool_update(
            request: ToolExecutionRequest,
            update: Any,
            *,
            event_iteration: int = iteration,
        ) -> None:
            self._emit_tool_update(request, update, event_iteration=event_iteration)

        hooks = ToolExecutionHooks(
            before_tool_call=self._host._tool_hooks.before_tool_call,
            after_tool_call=self._host._tool_hooks.after_tool_call,
            on_tool_update=on_tool_update,
            before_tool_batch=self._host._tool_hooks.before_tool_batch,
        )
        results = execute_tool_calls(
            response.tool_calls,
            self._runtime_tools,
            self._resolved,
            hooks=hooks,
            tool_resources=self._tool_resources,
        )
        provider_results = [result.provider_content() for result in results]
        tool_result_message = self._msg_formatter.to_tool_result_runtime_message(
            response.tool_calls, provider_results
        )
        self._messages.append(tool_result_message)

        tool_error_count = sum(1 for result in results if result.is_error)
        terminated_tool_count = sum(1 for result in results if result.terminate)
        for index, (tc, result) in enumerate(zip(response.tool_calls, results, strict=True)):
            compat_payload = result.compat_payload()
            self._executed.append((tc, compat_payload))
            self._tool_results.append((tc, result))
            self._host._emit_runtime(
                ToolExecutionEndEvent(
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    args=public_tool_input(tc.input),
                    result=redact_sensitive(compat_payload),
                    is_error=result.is_error,
                    iteration=iteration,
                    data={
                        "terminate": result.terminate,
                        "tool_call_index": index,
                        "tool_call_count": requested_tool_count,
                    },
                )
            )
        self._host._emit_runtime(
            TurnEndEvent(
                iteration=iteration,
                message=assistant_message,
                tool_results=tuple(result.compat_payload() for result in results),
                data={
                    "accepted": False,
                    "tool_call_count": requested_tool_count,
                    "tool_error_count": tool_error_count,
                    "terminated_tool_count": terminated_tool_count,
                },
            )
        )
        if terminated_tool_count:
            self._terminated_by_tool = True
            self._hit_cap = False
            self._stop_reason = "tool_terminated"
            return _IterationResult(
                should_stop=True,
                outcome="tool_terminated",
                requested_tool_count=requested_tool_count,
                tool_error_count=tool_error_count,
                terminated_tool_count=terminated_tool_count,
            )
        fingerprint = _observation_fingerprint(response.tool_calls, results)
        if fingerprint in self._seen_observations:
            self._stagnant_iterations += 1
        else:
            self._seen_observations.add(fingerprint)
            self._stagnant_iterations = 0
        if (
            self._max_stagnant_iterations is not None
            and self._stagnant_iterations >= self._max_stagnant_iterations
        ):
            self._stop_reason = "stagnation_limit"
            return _IterationResult(
                should_stop=True,
                outcome=self._stop_reason,
                requested_tool_count=requested_tool_count,
                tool_error_count=tool_error_count,
                terminated_tool_count=0,
            )
        return _IterationResult(
            should_stop=False,
            outcome="tool_observed",
            requested_tool_count=requested_tool_count,
            tool_error_count=tool_error_count,
            terminated_tool_count=0,
        )

    def _run_safety_handoff(self) -> None:
        """Make one tool-disabled call that turns a hard stop into a visible handoff."""
        self._safety_handoff_attempted = True
        iteration = self._iterations_used
        reason = self._stop_reason.replace("_", " ")
        self._messages.append(
            UserRuntimeMessage(content=_SAFETY_HANDOFF_PROMPT.format(reason=reason))
        )
        self._host._emit_runtime(
            TurnStartEvent(
                iteration=iteration,
                data={"safety_handoff": True, "stop_reason": self._stop_reason},
            )
        )
        try:
            response = self._think(
                iteration,
                tool_schemas=[],
                request_kind="safety_handoff",
            )
            content = str(response.content or "").strip()
            if content:
                # Do not retain tool calls a non-conforming provider may emit
                # despite the empty schema: the handoff never executes them.
                assistant_message = AssistantRuntimeMessage(
                    content=content,
                    metadata={"safety_handoff": True, "stop_reason": self._stop_reason},
                )
            else:
                assistant_message = self._fallback_handoff_message()
        except Exception:  # noqa: BLE001 - the deterministic handoff must survive provider failure
            logger.debug(
                "Safety handoff request failed; using deterministic fallback", exc_info=True
            )
            assistant_message = self._fallback_handoff_message()

        self._messages.append(assistant_message)
        self._final_text = str(assistant_message.content or "").strip()
        self._host._emit_runtime(MessageStartEvent(message=assistant_message, iteration=iteration))
        self._host._emit_runtime(
            MessageUpdateEvent(
                message=assistant_message,
                delta=self._final_text,
                iteration=iteration,
                data={"has_tool_calls": False, "safety_handoff": True},
            )
        )
        self._host._emit_runtime(
            TurnEndEvent(
                iteration=iteration,
                message=assistant_message,
                data={
                    "accepted": True,
                    "safety_handoff": True,
                    "stop_reason": self._stop_reason,
                },
            )
        )

    def _fallback_handoff_message(self) -> AssistantRuntimeMessage:
        """Build the deterministic handoff used when final sampling cannot answer."""
        content = (
            _STAGNATION_FALLBACK
            if self._stop_reason == "stagnation_limit"
            else _ITERATION_CAP_FALLBACK
        )
        return AssistantRuntimeMessage(
            content=content,
            metadata={"safety_handoff": True, "stop_reason": self._stop_reason},
        )

    def _emit_tool_update(
        self, request: ToolExecutionRequest, update: Any, *, event_iteration: int
    ) -> None:
        if self._host._tool_hooks.on_tool_update is not None:
            try:
                self._host._tool_hooks.on_tool_update(request, update)
            except Exception:  # noqa: BLE001 - observer failures must not break execution
                logger.debug(
                    "[runtime] on_tool_update(%s) raised; ignoring",
                    request.tool_call.name,
                    exc_info=True,
                )
        self._host._emit_runtime(
            ToolExecutionUpdateEvent(
                tool_call_id=request.tool_call.id,
                tool_name=request.tool_call.name,
                args=public_tool_input(request.tool_call.input),
                partial_result=redact_sensitive(update),
                iteration=event_iteration,
            )
        )

    def _finalize(self) -> AgentRunResult:
        """Build the run result, emit the end-of-run event, and return the result."""
        run_result = AgentRunResult(
            messages=self._messages,
            final_text=self._final_text,
            executed=self._executed,
            tool_results=self._tool_results,
            terminated_by_tool=self._terminated_by_tool,
            cancelled=self._cancelled,
            hit_iteration_cap=self._hit_cap,
            llm_iterations_used=self._iterations_used,
            final_system_prompt=self._final_system_prompt,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )
        self._host._emit_runtime(
            AgentEndEvent(
                messages=tuple(self._messages),
                data={
                    "final_text": self._final_text,
                    "stop_reason": self._stop_reason,
                    "hit_iteration_cap": self._hit_cap,
                    "safety_handoff_attempted": self._safety_handoff_attempted,
                    "terminated_by_tool": self._terminated_by_tool,
                    "cancelled": self._cancelled,
                    "llm_iterations_used": self._iterations_used,
                    "max_iterations": self._max_iterations,
                    "message_count": len(self._messages),
                    "executed_count": len(self._executed),
                    "tool_result_count": len(self._tool_results),
                },
            )
        )
        return run_result

    def _cancel_requested(self) -> bool:
        return bool(tool_resources_cancel_requested(self._tool_resources))

    def _mark_cancelled(self) -> None:
        self._hit_cap = False
        self._cancelled = True
        self._stop_reason = "cancelled"

    def _mark_iteration_span(
        self,
        span_attrs: dict[str, Any],
        result: _IterationResult,
    ) -> None:
        mark_span_outcome(
            span_attrs,
            result.outcome,
            should_stop=result.should_stop,
            stop_reason=self._stop_reason if result.should_stop else None,
            requested_tool_count=result.requested_tool_count,
            tool_error_count=result.tool_error_count,
            terminated_tool_count=result.terminated_tool_count,
            message_count=len(self._messages),
            executed_count=len(self._executed),
        )

    def _mark_loop_span(self, span_attrs: dict[str, Any]) -> None:
        mark_span_outcome(
            span_attrs,
            self._stop_reason,
            stop_reason=self._stop_reason,
            hit_iteration_cap=self._hit_cap,
            terminated_by_tool=self._terminated_by_tool,
            cancelled=self._cancelled,
            iterations_used=self._iterations_used,
            max_iterations=self._max_iterations,
            message_count=len(self._messages),
            executed_count=len(self._executed),
            tool_result_count=len(self._tool_results),
            final_text_chars=len(self._final_text),
        )

    def _mark_loop_error(self, span_attrs: dict[str, Any], exc: BaseException) -> None:
        mark_span_outcome(
            span_attrs,
            self._stop_reason,
            error=True,
            stop_reason=self._stop_reason,
            exception_type=type(exc).__name__,
            iterations_used=self._iterations_used,
            max_iterations=self._max_iterations,
            message_count=len(self._messages),
            executed_count=len(self._executed),
            tool_result_count=len(self._tool_results),
        )

    def _record_loop_operation(
        self,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        record_operation(event, {**self._operation_base(), **(data or {})})

    def _record_loop_finished(self, *, error: BaseException | None = None) -> None:
        data: dict[str, Any] = {
            "stop_reason": self._stop_reason,
            "hit_iteration_cap": self._hit_cap,
            "terminated_by_tool": self._terminated_by_tool,
            "cancelled": self._cancelled,
            "iterations_used": self._iterations_used,
            "message_count": len(self._messages),
            "executed_count": len(self._executed),
            "tool_result_count": len(self._tool_results),
            "final_text_chars": len(self._final_text),
        }
        if error is not None:
            data["exception_type"] = type(error).__name__
        self._record_loop_operation("agent_loop_finished", data)

    def _operation_base(self) -> dict[str, Any]:
        model = getattr(self._llm, "model_id", None) or getattr(self._llm, "_model", None)
        provider = getattr(self._llm, "_provider_label", None) or getattr(
            self._llm, "provider", None
        )
        tool_names = [str(getattr(tool, "name", "tool")) for tool in self._runtime_tools]
        return {
            "run_id": self._operation_run_id,
            "component": "react_loop",
            "model": str(model or "unknown"),
            "provider": str(provider or "unknown"),
            "max_iterations": self._max_iterations,
            "tool_count": len(self._runtime_tools),
            "tool_names": tool_names,
            "tool_schema_count": len(self._tool_schemas),
        }


def run_react_loop[RuntimeToolT: RuntimeTool](
    run_input: AgentRunInput[RuntimeToolT],
    host: LoopHost[RuntimeToolT],
) -> AgentRunResult:
    """Run the think -> call-tools -> observe loop and return its outcome."""
    return ReactLoop(run_input, host).run()


__all__ = ["ReactLoop", "run_react_loop"]
