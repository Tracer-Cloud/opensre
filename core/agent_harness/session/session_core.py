"""Core session state shared by every surface.

The surface-agnostic half of the REPL session: identity, persistence, integration
resolution, token accounting, conversational agent state, and grounding caches —
everything ``core``, ``gateway``, and ``tools`` consumers depend on. The interactive
shell extends this with its own UI state in
:class:`~surfaces.interactive_shell.session.session.Session`.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.agent_harness.session.history_entry import build_history_entry

if TYPE_CHECKING:
    from core.agent_harness.grounding.context import GroundingContext
    from core.agent_harness.session.integration_resolution import IntegrationResolutionResult
else:
    GroundingContext = Any

from config.llm_reasoning_effort import ReasoningEffortChoice
from core.agent_harness.accounting.token_usage import TokenUsage
from core.agent_harness.session.integration_resolution import IntegrationState
from core.agent_harness.session.pending_choice import PendingUserChoice
from core.agent_harness.session.pending_offer import (
    PendingIntegrationSetupOffer,
    PendingScheduleOffer,
)
from core.agent_harness.session.persistence.contracts import SessionStore
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from core.agent_harness.session_goal.goal import SessionGoal
from core.agent_harness.task_plan.plan import TaskPlan
from core.state import MutableAgentState
from infrastructure.scheduling.task_registry import TaskRegistry

#: How many recent history rows keep their full response body. Sized above
#: the conversation window so anything a prompt or a ``*_latest_*`` lookup
#: reads is still intact, while a long session stops holding every reply.
RESPONSE_TEXT_WINDOW = 20


def _default_grounding() -> GroundingContext:
    """Build a fresh per-session grounding cache bundle.

    Imported lazily so the session package can expose the state model without
    eagerly constructing grounding caches.
    """
    from core.agent_harness.grounding.context import GroundingContext

    return GroundingContext()


@dataclass
class SessionCore:
    """Surface-agnostic session state accumulated across REPL turns.

    Carries everything we want to persist across individual turns within the
    same session: accumulated infra context (service names, clusters observed)
    and a short interaction history for /status.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Stable UUID for this session. Rotated on /new so each logical session gets its own ID."""

    started_at: float = field(default_factory=time.time)
    """Unix timestamp of when this session (or post-reset sub-session) began."""

    store: SessionStore = field(default_factory=JsonlSessionStore, repr=False, compare=False)
    """Persistence backend for this session's turns and RCA records.

    Defaults to the JSONL backend; tests can inject an in-memory backend. All
    of this session's writes (record/append/flush) go through it, so the on-disk
    format is swappable without touching Session."""

    resumed_from_name: str = ""
    """Name of the most recently resumed session. Used by /sessions to display a
    fallback name for the current session before it has its own first turn."""

    history: list[dict[str, Any]] = field(default_factory=list)
    """Each entry has type, text, and ok fields for shell, slash, alert, and chat turns."""

    last_assistant_intent: str | None = None
    """Intent label set by the runtime after each handled turn.

    Values: "slash", "follow_up", and the three
    shell action-agent turn paths: "cli_agent_summarized" (a successful action's
    discovery output was summarized into an answer), "cli_agent_handled" (the
    action fully handled the turn; no LLM answer), and "cli_agent_fallback"
    (nothing handled, gathered evidence and answered via LLM chat).
    """

    integrations: IntegrationState = field(default_factory=IntegrationState)
    """Integration-resolution state: configured names, resolved-config cache, warm task.

    The public fields are re-exposed as properties below for API stability; the
    resolution logic and the coupling to the ``integrations`` domain live on
    ``IntegrationState``."""
    available_capabilities: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Optional planning-time capability constraints (slash/cli/synthetic)."""

    accumulated_context: dict[str, Any] = field(default_factory=dict)
    """Reusable infra context — service names, clusters, regions — learned from
    earlier turns that should seed future ones."""

    runtime_metadata: dict[str, Any] = field(default_factory=dict)
    """Read-only process facts (version, build, env) exposed to prompts and sandboxed tools."""

    reasoning_effort: ReasoningEffortChoice | None = None
    """Session-scoped reasoning effort preference for REPL-driven LLM calls."""

    tokens: TokenUsage = field(default_factory=TokenUsage)
    """Per-session token accounting (running totals + LLM call count) for ``/cost``."""

    task_registry: TaskRegistry = field(default_factory=TaskRegistry)
    """This session's in-flight and completed tasks (for /tasks and /cancel).

    Session-scoped task state whose lifecycle the manager owns (bootstrap swaps in
    a persistent registry); only the shell surface reads it today."""

    agent: MutableAgentState = field(default_factory=MutableAgentState)
    """Dedicated conversational-agent state (transcript + per-turn observation).

    Owns the assistant conversation history (alternating
    (\"user\"|\"assistant\", text)) and the per-turn read-only discovery
    observation, kept in one place rather than as loose session fields."""

    grounding: GroundingContext = field(
        default_factory=_default_grounding, repr=False, compare=False
    )
    """Per-session LLM grounding caches (CLI help, docs, AGENTS.md).

    Injected so the grounding caches have a process-scoped lifetime with no
    module-level mutable globals; tests can supply a fresh ``GroundingContext``."""

    pending_schedule_offer: PendingScheduleOffer | None = None
    """Structured schedule awaiting bare yes — set by propose_scheduled_delivery."""

    session_goal: SessionGoal | None = None
    """Outer cross-turn goal (multi-step / keep-going). Distinct from ReAct Goal."""

    offered_upgrade_ctas: set[str] = field(default_factory=set)
    """Session-scoped UpgradeCTA dedupe keys (``cta:service_id``)."""

    pending_integration_setup_offer: PendingIntegrationSetupOffer | None = None
    """Structured integrations-setup awaiting bare yes — armed after L0 UpgradeCTA."""

    pending_user_choice: PendingUserChoice | None = None
    """Structured multiple-choice question queued for the ``/choose`` selection
    menu — set by the ``ask_user_choice`` action tool, consumed once by the
    ``/choose`` handler."""

    ask_user_rounds: int = 0
    """Ask-User clarification rounds asked this workload; caps repeated batches.
    Reset on a genuine user turn."""

    active_skill: str | None = None
    """Skill loaded by ``skill_view`` in the current flow; cleared on a genuine user turn."""

    active_skill_tools: tuple[str, ...] = ()
    """The active skill's declared tools; an answer turn inside the flow offers only these."""

    task_plan: TaskPlan | None = None
    """Live execution checklist for the current workload, rendered above the
    prompt and persisted so it survives transcript compaction."""

    task_plan_work: list[list[str]] = field(default_factory=list)
    """Host-owned work lines per plan step index (not model-writable)."""

    task_plan_work_step_texts: tuple[str, ...] | None = None
    """Checklist identity for ``task_plan_work`` — step texts, ignoring status."""

    task_plan_breakdown_emitted: bool = False
    """True after the post-execution breakdown was printed for this checklist."""

    plan_only_until_authorized: bool = False
    """Set when the user asked for a plan without running it; the execution gate
    keeps mutating steps behind confirmation until a step is confirmed. Set-only
    here — cleared only at the gate on a confirmed mutating step."""

    pending_recovery_note: str | None = None
    """WAL recovery note for the next action turn — set on ``/resume`` when the
    resumed session log holds tool intents that never committed (the process
    died mid-execution). Consumed once by ``TurnSnapshot.from_session``."""

    gather_unreachable_tools: dict[str, str] = field(default_factory=dict)
    """Tool name → connectivity failure summary carried across SessionGoal gathers."""

    gather_unreachable_sources: dict[str, str] = field(default_factory=dict)
    """Source id → connectivity failure summary carried across SessionGoal gathers."""

    @property
    def cli_agent_messages(self) -> list[tuple[str, str]]:
        """Compatibility view used by the surface-agnostic agent turn engine."""
        return self.agent.messages

    @cli_agent_messages.setter
    def cli_agent_messages(self, value: list[tuple[str, str]]) -> None:
        self.agent.messages = value

    @property
    def last_command_observation(self) -> str | None:
        """Latest command/tool observation for the current turn."""
        return self.agent.last_observation

    @last_command_observation.setter
    def last_command_observation(self, value: str | None) -> None:
        self.agent.last_observation = value

    def record(
        self,
        kind: str,
        text: str,
        *,
        ok: bool = True,
        response_text: str | None = None,
        slash_outcome: str | None = None,
    ) -> None:
        """Append an entry to the session history.

        Supports kinds: "shell", "slash", "alert", "chat", "incoming_alert", etc.
        For "incoming_alert", use record_incoming_alert() instead to preserve metadata.

        ``slash_outcome`` tags typo-style slash failures (for example
        ``unknown_command`` or ``invalid_subcommand``) so analytics can
        distinguish them from handler failures.
        """
        entry = build_history_entry(
            kind,
            text,
            ok=ok,
            response_text=response_text,
            slash_outcome=slash_outcome,
        )

        self.history.append(entry)
        self._shed_stale_response_text()

        self.store.append_turn(self, kind, text)

    def _shed_stale_response_text(self) -> None:
        """Drop the response body from the entry just aged out of the window.

        Entries are never removed — ``len(history)`` is a turn counter and one
        caller slices by a captured index — so the list itself has to keep
        growing. The response bodies are the weight: a full agent reply dwarfs
        the type/text/ok fields beside it, and only the newest rows of a kind
        are ever read back. Shedding one entry per append keeps this O(1).
        """
        aged_out = len(self.history) - RESPONSE_TEXT_WINDOW - 1
        if aged_out < 0:
            return
        self.history[aged_out].pop("response_text", None)

    def mark_latest(self, *, ok: bool, kind: str | None = None) -> None:
        """Update the latest history entry, optionally scanning for a matching kind."""
        for latest in reversed(self.history):
            if kind is not None and latest.get("type") != kind:
                continue
            latest["ok"] = ok
            return

    def complete_latest_record(
        self,
        kind: str,
        *,
        response_text: str | None = None,
        ok: bool | None = None,
        slash_outcome: str | None = None,
    ) -> None:
        """Update the newest history row of ``kind`` with analytics outcome text."""
        for latest in reversed(self.history):
            if latest.get("type") != kind:
                continue
            if ok is not None:
                latest["ok"] = ok
            if slash_outcome:
                latest["slash_outcome"] = slash_outcome
            if response_text and response_text.strip():
                latest["response_text"] = response_text.strip()
            return

    # ── integration state: public fields re-exposed from the composed IntegrationState ──

    @property
    def configured_integrations(self) -> tuple[str, ...]:
        """Session-scoped configured integration names for planning-time capability checks."""
        return self.integrations.configured

    @configured_integrations.setter
    def configured_integrations(self, value: tuple[str, ...]) -> None:
        self.integrations.configured = value

    @property
    def configured_integrations_known(self) -> bool:
        """Whether ``configured_integrations`` reflects known state (vs default unknown)."""
        return self.integrations.configured_known

    @configured_integrations_known.setter
    def configured_integrations_known(self, value: bool) -> None:
        self.integrations.configured_known = value

    @property
    def resolved_integrations_cache(self) -> dict[str, Any] | None:
        """Resolved integration configs (env/store) shared across turns."""
        return self.integrations.resolved_cache

    @resolved_integrations_cache.setter
    def resolved_integrations_cache(self, value: dict[str, Any] | None) -> None:
        self.integrations.resolved_cache = value

    @property
    def vcs_repo_scopes(self) -> dict[str, tuple[str, ...]]:
        """Sticky per-vendor repo scopes inferred from chat, env, or git remote."""
        return self.integrations.vcs_repo_scopes

    @vcs_repo_scopes.setter
    def vcs_repo_scopes(self, value: dict[str, tuple[str, ...]]) -> None:
        self.integrations.vcs_repo_scopes = value

    @property
    def active_vcs_repositories(self) -> dict[str, str]:
        """Stable identities for the active per-vendor repository scopes."""
        return self.integrations.active_vcs_repositories

    @active_vcs_repositories.setter
    def active_vcs_repositories(self, value: dict[str, str]) -> None:
        self.integrations.active_vcs_repositories = value

    @property
    def known_vcs_repo_scopes(self) -> dict[str, dict[str, tuple[str, ...]]]:
        """All repositories remembered during this session, grouped by vendor."""
        return self.integrations.known_vcs_repo_scopes

    @known_vcs_repo_scopes.setter
    def known_vcs_repo_scopes(
        self,
        value: dict[str, dict[str, tuple[str, ...]]],
    ) -> None:
        self.integrations.known_vcs_repo_scopes = value

    def refresh_runtime_metadata(self) -> None:
        """Rebuild :attr:`runtime_metadata`, including merged capability warnings."""
        from config.runtime_metadata import build_runtime_metadata
        from infrastructure.safety.sandbox.capabilities import boot_capability_warnings

        meta = build_runtime_metadata()
        tools = meta.get("tools")
        installed = tools if isinstance(tools, dict) else None
        meta["capability_warnings"] = boot_capability_warnings(
            include_path_facts=True,
            installed_tools=installed,
        )
        self.runtime_metadata = meta

    def hydrate_configured_integrations(self) -> None:
        """Load configured integration names (env + local store); metadata-only."""
        self.integrations.hydrate()

    def warm_resolved_integrations(self, *, generation: int | None = None) -> None:
        """Resolve full integration configs once, without progress UI."""
        self.integrations.warm(generation=generation)

    def get_integrations(self) -> IntegrationResolutionResult:
        """Return the session's integration configs as a typed snapshot (cache-aware)."""
        return self.integrations.get()

    def refresh_integration_state(self) -> None:
        """Re-resolve integration state after the local store changes."""
        self.integrations.refresh()

    def clear(self, *, rotate_identity: bool = True) -> None:
        """Reset core session state to fresh (used by /new and /resume).

        Shell subclasses override to also reset their facets; see
        :meth:`~surfaces.interactive_shell.session.session.Session.clear`.
        """
        self.history.clear()
        self.resumed_from_name = ""
        self.last_assistant_intent = None
        self.integrations.reset()
        self.available_capabilities.clear()
        self.accumulated_context.clear()
        self.tokens.reset()
        self.agent.clear()
        self.refresh_runtime_metadata()
        # Keep persisted cross-session task history on disk intact.
        # /new is session-scoped, so swap in a fresh in-memory registry
        # that reuses the same backing store (if any) so /tasks still shows history.
        persist_path = self.task_registry._persist_path
        self.task_registry = (
            TaskRegistry(persist_path=persist_path, load=False)
            if persist_path is not None
            else TaskRegistry()
        )
        self.pending_schedule_offer = None
        self.pending_integration_setup_offer = None
        self.session_goal = None
        self.offered_upgrade_ctas.clear()
        self.pending_user_choice = None
        self.ask_user_rounds = 0
        self.task_plan = None
        self.task_plan_work = []
        self.task_plan_work_step_texts = None
        self.task_plan_breakdown_emitted = False
        self.plan_only_until_authorized = False
        self.pending_recovery_note = None
        self.gather_unreachable_tools.clear()
        self.gather_unreachable_sources.clear()
        if rotate_identity:
            # Rotate session identity so the new post-reset session gets its own ID and file.
            self.session_id = str(uuid.uuid4())
            self.started_at = time.time()

    def release_resources(self) -> None:
        """Cancel background integration-warm work for teardown.

        Called when the handle is discarded (see ``SessionManager.close``); the
        session owns its own teardown. Thread-safe against a background warm
        thread. Shell subclasses override to also drop loop-owned UI references.
        """
        self.integrations.release()
