"""In-memory port adapters for headless agent runs.

These adapters implement core.agent_harness.ports interfaces without
external side effects (no IO, no network, no filesystem).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from core.agent_harness.ports import (
    CancelCapableConsole,
    ConfirmFn,
    ToolEventObserver,
)
from core.agent_harness.session.history_entry import build_history_entry
from core.agent_harness.session.pending_offer import PendingScheduleOffer
from core.agent_harness.turns.turn_results import (
    ToolCallingTurnResult,
    TurnResult,
)


@dataclass
class InMemorySessionState:
    """List-backed :class:`core.agent_harness.ports.SessionState` for headless runs."""

    session_id: str = "headless"
    cli_agent_messages: list[tuple[str, str]] = field(default_factory=list)
    configured_integrations: list[str] = field(default_factory=list)
    configured_integrations_known: bool = False
    pending_schedule_offer: PendingScheduleOffer | None = None
    reasoning_effort: Any | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    last_command_observation: str | None = None
    resolved_integrations_cache: dict[str, Any] | None = None
    vcs_repo_scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    active_vcs_repositories: dict[str, str] = field(default_factory=dict)
    known_vcs_repo_scopes: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)
    records: list[tuple[str, str, bool]] = field(default_factory=list)
    available_capabilities: dict[str, Any] = field(default_factory=dict)

    def record(
        self,
        kind: str,
        text: str,
        *,
        ok: bool = True,
        response_text: str | None = None,
        slash_outcome: str | None = None,
    ) -> None:
        self.records.append((kind, text, ok))
        # Mirror SessionCore.history so tools that inspect recent shell/slash
        # rows (e.g. propose_scheduled_delivery's fetch precondition) work
        # under headless smokes the same way they do in the live shell.
        entry = build_history_entry(
            kind,
            text,
            ok=ok,
            response_text=response_text,
            slash_outcome=slash_outcome,
        )
        self.history.append(entry)


@dataclass
class BufferOutputSink:
    """Collects all output into ``lines`` / ``streamed`` for inspection."""

    lines: list[str] = field(default_factory=list)
    streamed: list[str] = field(default_factory=list)

    def print(self, message: str = "") -> None:
        self.lines.append(message)

    def render_response_header(self, label: str) -> None:
        self.lines.append(f"[{label}]")

    def render_error(self, message: str) -> None:
        self.lines.append(f"ERROR: {message}")

    def stream(
        self,
        *,
        label: str,
        chunks: Iterable[str],
        suppress_if_starts_with: str | None = None,
        defer_want_me_to_closer: bool = False,
    ) -> str:
        _ = (label, suppress_if_starts_with, defer_want_me_to_closer)
        text = "".join(str(chunk) for chunk in chunks)
        self.streamed.append(text)
        return text

    def finish_streamed_response(self, answer: str) -> None:
        # Headless tests assert on ``TurnResult.assistant_response_text``.
        _ = answer

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class EmptyPromptContextProvider:
    """Grounding provider that supplies no corpora (headless)."""

    def bind_session(self, session: Any) -> None:
        """No session state — session retarget is a no-op for empty grounding."""
        _ = session

    def surface(self) -> str:
        return "interactive_shell"

    def cli_reference(self) -> str:
        return ""

    def agents_md(self) -> str:
        return ""

    def docs(self, query: str) -> str:
        _ = query
        return ""

    def runtime_facts(self) -> Mapping[str, Any]:
        from config.runtime_metadata import capture_runtime_facts

        return capture_runtime_facts()

    def environment_block(self, runtime: Mapping[str, Any] | None = None) -> str:  # noqa: ARG002 - empty grounding
        return ""

    def long_term_memory(self) -> str:
        return ""

    def setup_state(self) -> str:
        return ""

    def log_diagnostics(self, reason: str) -> None:
        _ = reason


class NullToolProvider:
    """Provides no action tools and a no-op tool-event observer."""

    def bind_session(self, session: Any) -> None:
        """No tools to retarget."""
        _ = session

    def bind_console(self, console: CancelCapableConsole) -> None:
        """No console-backed tools."""
        _ = console

    def action_tools(
        self,
        *,
        confirm_fn: ConfirmFn | None,
        is_tty: bool | None,
        resolved_integrations: dict[str, Any] | None = None,
        turn_user_message: str = "",
    ) -> list[Any]:
        _ = (confirm_fn, is_tty, resolved_integrations, turn_user_message)
        return []

    def tool_resources(self) -> dict[str, Any]:
        return {}

    def observer(self, *, message: str) -> ToolEventObserver:
        _ = message

        def _observer(_kind: str, _data: dict[str, Any]) -> None:
            return None

        return _observer


class NoopTurnAccounting:
    """Records nothing and returns the result unchanged."""

    def record_action_result(self, action_result: ToolCallingTurnResult) -> None:
        _ = action_result

    def finalize(self, result: TurnResult) -> TurnResult:
        return result


class NoopErrorReporter:
    """Swallows reported exceptions (headless)."""

    def report(self, exc: BaseException, *, context: str, expected: bool = False) -> None:
        _ = (exc, context, expected)
