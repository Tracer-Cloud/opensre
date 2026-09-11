"""The reusable tool-calling agent every surface runs (shell, gateway).

You create an ``Agent`` with its config (LLM, system prompt, tools, iteration
cap); ``run()`` gathers that config for one run and hands it to
``core.agent.react_loop.run_react_loop``, which runs the actual
think -> call-tools -> observe loop. ``Agent`` stays thin: it holds the config
and provides the callback methods (from the mixins) the loop calls back into —
it does not contain the loop itself.

"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from core.agent.mixins import EventEmitterMixin, SteeringMixin, ToolFilterMixin
from core.agent.provider_hooks import ProviderHookDelegate
from core.agent.react_loop import run_react_loop
from core.agent.run_io import AgentRunInput, AgentRunResult
from core.events import RuntimeEventCallback, TupleEventCallback
from core.llm.types import AgentLLMClient
from core.messages import ProviderMessage, RuntimeMessage, RuntimeMessageLike
from core.provider import ProviderHooks, ProviderRequest
from core.tool.contracts import RuntimeTool
from core.tool.execution import ToolExecutionHooks

if TYPE_CHECKING:
    from core.agent.goals import Goal
    from core.agent_harness.turns.turn_snapshot import AgentRuntimeRequest


class Agent[RuntimeToolT: RuntimeTool](EventEmitterMixin, ToolFilterMixin, SteeringMixin):
    """Stateful, configurable ReAct agent.

    Wires per-run context into ``run_react_loop`` and exposes hook methods so
    subclasses can customise tool filtering without re-implementing the loop.
    """

    def __init__(
        self,
        *,
        llm: AgentLLMClient,
        system: str | None = None,
        tools: Sequence[RuntimeToolT] | None = None,
        resolved_integrations: dict[str, Any] | None = None,
        max_iterations: int | None = None,
        max_stagnant_iterations: int | None = None,
        on_event: TupleEventCallback | None = None,
        on_runtime_event: RuntimeEventCallback | None = None,
        tool_hooks: ToolExecutionHooks | None = None,
        tool_resources: dict[str, Any] | None = None,
        provider_hooks: ProviderHooks | None = None,
        goal: Goal | None = None,
    ) -> None:
        if llm is None:
            raise ValueError("Agent: llm= must be set at construction.")
        self._llm = llm
        self._system = system
        self._tools: list[RuntimeToolT] | None = list(tools) if tools is not None else None
        self._resolved = resolved_integrations
        self._max_iterations = max_iterations
        if max_stagnant_iterations is not None and max_stagnant_iterations < 1:
            raise ValueError("Agent: max_stagnant_iterations must be positive when set.")
        self._max_stagnant_iterations = max_stagnant_iterations
        # Set per run from the run input; falls back to the constructed value.
        self._effective_max_iterations = max_iterations
        self._on_tuple_event = on_event
        self._on_runtime_event = on_runtime_event
        self._tool_hooks = tool_hooks or ToolExecutionHooks()
        self._tool_resources = dict(tool_resources or {})
        self._hooks = ProviderHookDelegate(provider_hooks or ProviderHooks())
        self._goal = goal
        self._steering_messages: deque[str] = deque()
        self._follow_up_messages: deque[str] = deque()
        self._react_iterations_used = 0
        self._react_executed: list[tuple[Any, Any]] = []
        self._react_hit_iteration_cap = False

    def run(
        self,
        initial_messages: Sequence[RuntimeMessageLike] | None = None,
        *,
        runtime_request: AgentRuntimeRequest | None = None,
    ) -> AgentRunResult:
        """Assemble the resolved per-run input and hand it to ``run_react_loop``."""
        self._react_iterations_used = 0
        self._react_executed = []
        self._react_hit_iteration_cap = False
        run_input = self._build_run_input(initial_messages, runtime_request)
        # A runtime_request carries its own budget, which is what ReactLoop
        # iterates. Goal acceptance must use that, not the construction-time
        # value, or the ceiling never lands on the real last lap.
        self._effective_max_iterations = run_input.max_iterations
        return run_react_loop(run_input, self)

    def _note_react_run_progress(
        self,
        *,
        iterations_used: int,
        executed: list[tuple[Any, Any]],
        hit_iteration_cap: bool,
    ) -> None:
        """Record partial loop progress for telemetry when ``run`` aborts early."""
        self._react_iterations_used = iterations_used
        self._react_executed = executed
        self._react_hit_iteration_cap = hit_iteration_cap

    def _build_run_input(
        self,
        initial_messages: Sequence[RuntimeMessageLike] | None,
        runtime_request: AgentRuntimeRequest | None,
    ) -> AgentRunInput[RuntimeToolT]:
        """Assemble the run input from whichever source the caller supplied.

        A ``runtime_request`` is validated and carries its own resolved context;
        raw ``initial_messages`` fall back to the construction-time config, which
        must include ``system`` and ``max_iterations``.
        """
        if runtime_request is not None:
            runtime_request.validate_runtime_request()
            return AgentRunInput[RuntimeToolT].from_runtime_request(runtime_request, llm=self._llm)
        if initial_messages is not None:
            if self._system is None:
                raise ValueError("Agent.run: system= must be set at construction.")
            if self._max_iterations is None:
                raise ValueError("Agent.run: max_iterations= must be set at construction.")
            return AgentRunInput[RuntimeToolT].from_messages(
                initial_messages,
                llm=self._llm,
                system=self._system,
                tools=self._tools,
                resolved=self._resolved,
                tool_resources=self._tool_resources,
                max_iterations=self._max_iterations,
                max_stagnant_iterations=self._max_stagnant_iterations,
            )
        raise ValueError("Agent.run requires initial_messages or runtime_request.")

    def _should_accept_conclusion(
        self,
        *,
        evidence_count: int,
        iteration: int,
        final_text: str = "",
    ) -> tuple[bool, str | None]:
        """Hook: decide what to do when the LLM stops requesting tools.

        Return ``(True, None)`` to accept the conclusion and end the loop.
        Return ``(False, nudge_text)`` to inject a user message and continue.

        A bare :class:`~core.agent.goals.Goal` without ``verify`` is descriptive
        only — it does not gate stop (capability answers / skill demos must end
        on the first no-tool reply). Reviewed goals from
        ``build_goal_reviewer`` carry ``verify`` and do gate.
        """
        if self._goal is None or self._goal.verify is None:
            from infrastructure.observability.trace.decisions import record_decision

            record_decision(
                "conclusion",
                attributes={
                    "accepted": True,
                    "reason": "no_goal_verifier",
                    "iteration": iteration,
                },
            )
            return True, None
        from core.agent.goals import should_accept_with_goal

        return should_accept_with_goal(
            self._goal,
            final_text=final_text,
            evidence_count=evidence_count,
            iteration=iteration,
            max_iterations=self._effective_max_iterations,
        )

    # Thin forwarders to ``self._hooks`` (a ProviderHookDelegate). Kept as
    # methods rather than an exposed attribute so LoopHost's contract is
    # the four calls, not this concrete delegate type — see loop_host.py.
    def _transform_messages(self, messages: list[RuntimeMessage]) -> list[RuntimeMessage]:
        return self._hooks.transform_messages(messages)

    def _convert_to_llm(self, llm: Any, messages: list[RuntimeMessage]) -> list[ProviderMessage]:
        return self._hooks.convert_to_llm(llm, messages)

    def _before_request(self, request: ProviderRequest) -> ProviderRequest:
        return self._hooks.before_request(request)

    def _after_response(self, request: ProviderRequest, response: Any) -> Any:
        return self._hooks.after_response(request, response)
