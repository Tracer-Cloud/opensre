"""Per-turn immutable context snapshot for the agentic turn engine.

Built once at turn start via :meth:`TurnSnapshot.from_session`. Downstream
prompt builders read this snapshot; the live session is still used for writes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from core.agent_harness.prompts.kernel.surfaces import profile_for
from core.agent_harness.session_goal.goal import SessionGoal
from core.agent_harness.session_goal.progress import format_session_goal_brief
from core.state import MAX_CONVERSATION_MESSAGES
from core.state.transcript_window import compact_messages_to_window
from infrastructure.setup_state import cached_setup_state

if TYPE_CHECKING:
    from config.llm_reasoning_effort import ReasoningEffortChoice
    from core.agent_harness.task_plan.plan import TaskPlan
    from core.messages import RuntimeMessage

RuntimeTool = Any


def _setup_state_for_surface(integrations: Sequence[str], surface: str | None) -> str:
    """Render setup facts only when the surface profile allows them.

    Gateway keeps ``setup_state=False`` so a shared chat does not broadcast the
    install's integrations and schedules to every member.

    ``surface=None`` means the caller does not know where the turn is going, and
    yields no facts. :func:`profile_for` deliberately fails *open* so a new
    surface still gets the full prompt; that is the wrong direction for install
    state, which is the one field where guessing wrong discloses something.
    """
    if surface is None or not profile_for(surface).setup_state:
        return ""
    return cached_setup_state(integrations)


@runtime_checkable
class PromptRenderable(Protocol):
    """Structured prompt object that can render itself into provider text."""

    def render(self) -> str:
        """Return the prompt as provider-ready text."""


type SystemPromptInput = str | PromptRenderable


@runtime_checkable
class TurnSnapshotSource(Protocol):
    """Structural source of per-turn snapshot fields.

    ``Session`` satisfies this without inheriting it; headless session
    stores implement the same attributes. Keeping this structural is what lets
    ``agent/`` build a ``TurnSnapshot`` without importing ``interactive_shell``.
    """

    cli_agent_messages: list[tuple[str, str]]
    configured_integrations_known: bool
    reasoning_effort: ReasoningEffortChoice | None

    # Read-only here; ``Session`` stores a tuple. A property matches
    # covariantly, so any concrete ``Sequence[str]`` implementation satisfies it.
    @property
    def configured_integrations(self) -> Sequence[str]:
        """Integration names configured for this session (read-only view)."""


@runtime_checkable
class AgentRuntimeRequest(Protocol):
    """Runtime request contract consumed by ``core.agent.Agent``."""

    system_prompt: Any
    active_tools: Sequence[RuntimeTool]
    resolved_integrations: dict[str, Any]
    tool_resources: dict[str, Any]
    max_iterations: int

    def render_system_prompt(self) -> str:
        """Render ``system_prompt`` into provider text."""

    def runtime_messages(self) -> list[RuntimeMessage]:
        """Messages for this turn's LLM invoke."""

    def validate_runtime_request(self) -> None:
        """Raise if the request is not ready to run."""


def _render_system_prompt(prompt: SystemPromptInput) -> str:
    if isinstance(prompt, str):
        return prompt
    rendered = prompt.render()
    if not isinstance(rendered, str):
        raise TypeError("system_prompt.render() must return str.")
    return rendered


def _select_runtime_request_input(text: str, source: Any) -> Any | None:
    """Read optional runtime-request fields from a structural session source."""
    direct_selector = getattr(source, "select_turn_runtime_input", None)
    if callable(direct_selector):
        return direct_selector(text)

    agent_state = getattr(source, "agent", None)
    state_selector = getattr(agent_state, "select_turn_runtime_input", None)
    if callable(state_selector):
        return state_selector(text)

    return None


def _interactive_choice_available(session: Any, surface: str | None) -> bool:
    """True when this turn can open the Ask User / ``/choose`` picker.

    Gateway and headless sessions have no terminal facet. An interactive-shell
    session with a terminal facet can queue the menu (the tool still checks TTY).
    """
    if surface == "gateway":
        return False
    if surface not in (None, "interactive_shell"):
        return False
    return getattr(session, "terminal", None) is not None


@dataclass(frozen=True)
class TurnSnapshot:
    """Immutable per-turn snapshot and optional runtime request.

    Carries everything the action agent and conversational assistant need to
    build prompts and ground answers, frozen at the moment the turn begins. It
    can also carry the runtime loop request fields that ``Agent.run`` needs.

    The live ``Session`` is still passed separately to callers that need
    to write state (recording history, persisting token usage, updating intent).
    """

    text: str
    """Raw user input text for this turn."""

    conversation_messages: tuple[tuple[str, str], ...]
    """Snapshot of recent CLI conversation: ``(role, content)`` pairs, oldest
    first, compacted to the ``MAX_CONVERSATION_MESSAGES`` window at assembly
    time (overflow becomes a leading session-summary message)."""

    configured_integrations: tuple[str, ...]
    """Integration names known to be configured at turn start."""

    configured_integrations_known: bool
    """Whether ``configured_integrations`` reflects real state (vs unknown)."""

    reasoning_effort: ReasoningEffortChoice | None
    """Session-scoped reasoning effort preference for LLM calls this turn."""

    setup_state: str = ""
    """The operator's connected integrations, schedules, and last delivery
    outcome, rendered as a fact block. The planner decides whether to offer a
    scheduled delivery, so it reads what is already configured."""

    system_prompt: SystemPromptInput = ""
    """Runtime system prompt used by the shared agent loop."""

    available_tools: tuple[RuntimeTool, ...] = ()
    """All tools available to the surface for this turn."""

    active_tools: tuple[RuntimeTool, ...] = ()
    """Subset of tools offered to the model for this turn."""

    resolved_integrations: dict[str, Any] = field(default_factory=dict)
    """Resolved integration configuration passed to tool execution."""

    tool_resources: dict[str, Any] = field(default_factory=dict)
    """Non-serializable runtime resources made available to opted-in tools."""

    max_iterations: int = 1
    """Maximum runtime loop iterations for this request."""

    model: Any | None = None
    """Optional model selection read model for diagnostics."""

    working_directory: str | None = None
    terminal_capabilities: dict[str, Any] = field(default_factory=dict)
    shell_command_context: dict[str, Any] = field(default_factory=dict)
    slash_command: str | None = None
    display_preferences: dict[str, Any] = field(default_factory=dict)
    last_observation: str | None = None

    recovery_note: str | None = None
    """WAL recovery note from ``/resume`` — dangling tool intents formatted for
    the action agent. Consumed from ``session.pending_recovery_note`` (popped:
    the note rides exactly one turn)."""

    task_plan: TaskPlan | None = None
    """Live ``update_plan`` checklist at turn start (survives transcript drop)."""

    plan_only_until_authorized: bool = False
    """When true, the user asked for a plan without running it yet."""

    prompt_surface: str | None = None
    """``interactive_shell``, ``gateway``, or ``None`` when the host did not say."""

    session_goal_attached: bool = False
    """True when a ``/goal`` (SessionGoal) is attached for this turn."""

    session_goal_brief: str = ""
    """Condition, checklist and last verdict of the attached ``/goal``, for the prompt."""

    interactive_choice_available: bool = False
    """True when ``ask_user_choice`` can open a keyboard menu on this surface."""

    active_vcs_repositories: dict[str, str] = field(default_factory=dict)
    """Active repository identity per VCS vendor for this turn."""

    known_vcs_repositories: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Repository identities retained in this session, grouped by VCS vendor."""

    @classmethod
    def from_session(
        cls,
        text: str,
        session: TurnSnapshotSource,
        *,
        surface: str | None,
    ) -> TurnSnapshot:
        """Snapshot the relevant session fields for one turn.

        Call this once at the top of the turn before any mutations happen, then
        pass the returned context downstream. ``session`` is anything satisfying
        :class:`TurnSnapshotSource` (e.g. the shell's ``Session``). When the
        source also exposes ``select_turn_runtime_input`` directly or through
        ``source.agent``, runtime request fields are snapshotted too.

        ``surface`` selects the :class:`SurfaceProfile` and is required so no
        caller can silently claim to be the shell: gateway passes ``"gateway"``
        to keep setup facts out of shared chats, and ``None`` says "unknown",
        which omits them.
        """
        valid_messages = [
            (str(role), str(content))
            for role, content in session.cli_agent_messages
            if isinstance(role, str) and isinstance(content, str)
        ]
        snapshot: tuple[tuple[str, str], ...] = tuple(
            compact_messages_to_window(valid_messages, max_messages=MAX_CONVERSATION_MESSAGES)
        )
        runtime_input = _select_runtime_request_input(text, session)
        last_observation = _read_last_observation(session, runtime_input)
        recovery_note = _pop_recovery_note(session)
        return cls(
            text=text,
            conversation_messages=snapshot,
            configured_integrations=tuple(session.configured_integrations),
            configured_integrations_known=bool(session.configured_integrations_known),
            setup_state=_setup_state_for_surface(session.configured_integrations, surface),
            reasoning_effort=session.reasoning_effort,
            system_prompt=getattr(runtime_input, "system_prompt", ""),
            available_tools=tuple(getattr(runtime_input, "available_tools", ())),
            active_tools=tuple(getattr(runtime_input, "active_tools", ())),
            resolved_integrations=dict(getattr(runtime_input, "resolved_integrations", {}) or {}),
            tool_resources=dict(getattr(runtime_input, "tool_resources", {}) or {}),
            max_iterations=int(getattr(runtime_input, "max_iterations", 1)),
            model=getattr(runtime_input, "model", None),
            last_observation=last_observation,
            recovery_note=recovery_note,
            task_plan=_read_task_plan(session),
            plan_only_until_authorized=bool(getattr(session, "plan_only_until_authorized", False)),
            prompt_surface=surface,
            session_goal_attached=getattr(session, "session_goal", None) is not None,
            session_goal_brief=_session_goal_brief(session),
            interactive_choice_available=_interactive_choice_available(session, surface),
            active_vcs_repositories=dict(getattr(session, "active_vcs_repositories", {}) or {}),
            known_vcs_repositories={
                str(vendor): tuple(scopes)
                for vendor, scopes in (getattr(session, "known_vcs_repo_scopes", {}) or {}).items()
                if isinstance(scopes, dict)
            },
        )

    def render_system_prompt(self) -> str:
        """Render the runtime system prompt to provider-ready text."""
        return _render_system_prompt(self.system_prompt)

    def runtime_messages(self) -> list[RuntimeMessage]:
        """Return the user message list expected by the runtime loop."""
        from core.messages import UserRuntimeMessage

        return [UserRuntimeMessage(content=self.text)]

    def validate_runtime_request(self) -> None:
        """Validate fields required once this object reaches ``Agent.run``."""
        if not self.render_system_prompt():
            raise ValueError("TurnSnapshot.system_prompt is required for Agent.run().")
        if self.max_iterations < 1:
            raise ValueError("TurnSnapshot.max_iterations must be positive.")
        if not self.active_tools:
            raise ValueError("TurnSnapshot.active_tools must include at least one tool.")


def _pop_recovery_note(session: TurnSnapshotSource) -> str | None:
    """Consume ``session.pending_recovery_note`` (optional field, one turn only).

    Popping here — the single per-turn snapshot point — guarantees the note is
    injected into exactly the first turn after ``/resume`` and never lingers in
    later cached prompts.
    """
    note = getattr(session, "pending_recovery_note", None)
    if not isinstance(note, str) or not note.strip():
        return None
    setattr(session, "pending_recovery_note", None)  # noqa: B010 - protocol lacks the optional field
    return note


def _session_goal_brief(session: Any) -> str:
    goal = getattr(session, "session_goal", None)
    if not isinstance(goal, SessionGoal):
        return ""
    return format_session_goal_brief(goal)


def _read_task_plan(session: TurnSnapshotSource) -> TaskPlan | None:
    """Copy the live task plan if the source carries one."""
    from core.agent_harness.task_plan.plan import TaskPlan

    plan = getattr(session, "task_plan", None)
    return plan if isinstance(plan, TaskPlan) else None


def _read_last_observation(session: TurnSnapshotSource, runtime_input: Any | None) -> str | None:
    """Read the last tool observation from runtime input or the live session."""
    from_runtime = getattr(runtime_input, "last_observation", None)
    if isinstance(from_runtime, str) and from_runtime.strip():
        return from_runtime

    agent = getattr(session, "agent", None)
    agent_observation = getattr(agent, "last_observation", None)
    if isinstance(agent_observation, str) and agent_observation.strip():
        return agent_observation

    session_observation = getattr(session, "last_command_observation", None)
    if isinstance(session_observation, str) and session_observation.strip():
        return session_observation

    return None


__all__ = [
    "AgentRuntimeRequest",
    "PromptRenderable",
    "TurnSnapshot",
    "TurnSnapshotSource",
]
