"""Core-owned default tool provider for the shared agent harness."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from core.agent_harness.ports import (
    CancelCapableConsole,
    ConfirmFn,
    LlmProviderPortsFactory,
    SlashPortsFactory,
    SubprocessPresenterFactory,
    TaskCancelPortsFactory,
    ToolEventObserver,
)
from core.agent_harness.tools.action_tools import get_action_tools_from_integrations_view
from core.agent_harness.tools.tool_context import (
    ACTION_TOOL_CONTEXT_RESOURCE_KEY,
    ActionToolScope,
)
from core.tool import SideEffectLevel

# Fail-closed: unattended ticks may only use tools that cannot mutate the
# machine or an external system. Morning-report weather/news is pre-fetched
# by the scheduled runner, so shell_run is not required on the tick.
_UNATTENDED_SAFE_LEVELS = frozenset({SideEffectLevel.NONE, SideEffectLevel.READ_ONLY})
_UNATTENDED_BLOCKED_NAMES = frozenset(
    {"propose_scheduled_delivery", "slash_invoke", "execute_python_code"}
)

ActionObserverFactory = Callable[[str], ToolEventObserver]
# Return value is tools.interactive_shell.subprocess.SubprocessPresenter (surface-injected).


_TOOL_INPUT_LOG_PREVIEW_LIMIT = 500


def tool_allowed_for_unattended_run(tool: Any) -> bool:
    """True when ``tool`` may run on a scheduled skill tick."""
    name = getattr(tool, "name", None)
    if name in _UNATTENDED_BLOCKED_NAMES:
        return False
    return getattr(tool, "side_effect_level", None) in _UNATTENDED_SAFE_LEVELS


def _tool_input_preview(value: Any) -> str:
    preview = repr(value)
    if len(preview) > _TOOL_INPUT_LOG_PREVIEW_LIMIT:
        return f"{preview[: _TOOL_INPUT_LOG_PREVIEW_LIMIT - 3]}..."
    return preview


class DefaultToolProvider:
    """:class:`core.agent_harness.ports.ToolProvider` backed by action tools."""

    def __init__(
        self,
        session: Any,
        console: Any,
        *,
        request_exit: Callable[[], None] | None = None,
        precomputed_action_tools: list[Any] | None = None,
        observer_factory: ActionObserverFactory | None = None,
        tool_action_logger: logging.Logger | None = None,
        subprocess_presenter_factory: SubprocessPresenterFactory | None = None,
        llm_provider_ports_factory: LlmProviderPortsFactory | None = None,
        task_cancel_ports_factory: TaskCancelPortsFactory | None = None,
        slash_ports_factory: SlashPortsFactory | None = None,
        unattended: bool = False,
    ) -> None:
        self._session = session
        self._console = console
        self._request_exit = request_exit
        self._precomputed_action_tools = precomputed_action_tools
        self._observer_factory = observer_factory
        self._tool_action_logger = tool_action_logger
        self._subprocess_presenter_factory = subprocess_presenter_factory
        self._llm_provider_ports_factory = llm_provider_ports_factory
        self._task_cancel_ports_factory = task_cancel_ports_factory
        self._slash_ports_factory = slash_ports_factory
        self._unattended = unattended
        self._tool_scope: ActionToolScope | None = None

    def bind_session(self, session: Any) -> None:
        """Point this provider at a freshly resolved session (gateway reuse)."""
        self._session = session
        self._tool_scope = None

    def bind_console(self, console: CancelCapableConsole) -> None:
        """Point tool UI (observers, subprocess presenter) at ``console``."""
        self._console = console
        self._tool_scope = None

    def action_tools(
        self,
        *,
        confirm_fn: ConfirmFn | None,
        is_tty: bool | None,
        resolved_integrations: dict[str, Any] | None = None,
        turn_user_message: str = "",
    ) -> list[Any]:
        subprocess_presenter = None
        presenter_factory = self._subprocess_presenter_factory
        if presenter_factory is not None:
            subprocess_presenter = presenter_factory(
                self._session,
                self._console,
                confirm_fn,
                is_tty,
                True,
            )

        llm_provider_ports = None
        if self._llm_provider_ports_factory is not None:
            llm_provider_ports = self._llm_provider_ports_factory()

        task_cancel_ports = None
        if self._task_cancel_ports_factory is not None:
            task_cancel_ports = self._task_cancel_ports_factory()

        slash_ports = None
        if self._slash_ports_factory is not None:
            slash_ports = self._slash_ports_factory()

        ctx = ActionToolScope(
            session=self._session,
            console=self._console,
            confirm_fn=confirm_fn,
            is_tty=is_tty,
            request_exit=self._request_exit,
            action_already_listed=True,
            # Built once per turn, before any tool runs — so the current
            # history length is this turn's starting boundary.
            history_start=len(getattr(self._session, "history", None) or []),
            turn_user_message=turn_user_message,
            subprocess_presenter=subprocess_presenter,
            llm_provider_ports=llm_provider_ports,
            task_cancel_ports=task_cancel_ports,
            slash_ports=slash_ports,
        )
        self._tool_scope = ctx
        if self._precomputed_action_tools is not None:
            tools = list(self._precomputed_action_tools)
        else:
            resolved = (
                resolved_integrations
                if resolved_integrations is not None
                else self._resolved_integrations()
            )
            tools = get_action_tools_from_integrations_view(ctx, resolved_integrations=resolved)
        if not getattr(self._session, "skill_discovery_enabled", True):
            tools = [tool for tool in tools if tool.name != "skill_view"]
        if self._unattended:
            return [tool for tool in tools if tool_allowed_for_unattended_run(tool)]
        return tools

    def tool_resources(self) -> dict[str, Any]:
        if self._tool_scope is None:
            return {}
        return {ACTION_TOOL_CONTEXT_RESOURCE_KEY: self._tool_scope}

    def observer(self, *, message: str) -> ToolEventObserver:
        if self._observer_factory is not None:
            observer = self._observer_factory(message)
        else:

            def observer(_kind: str, _data: dict[str, Any]) -> None:
                return None

        if self._tool_action_logger is None:
            return observer
        logger = self._tool_action_logger

        def _logging_observer(kind: str, data: dict[str, Any]) -> None:
            if kind == "tool_start":
                tool_name = str(data.get("name") or "").strip()
                if tool_name:
                    logger.info(
                        "tool action name=%s input=%s",
                        tool_name,
                        _tool_input_preview(data.get("input", {})),
                    )
            elif kind == "tool_end":
                tool_name = str(data.get("name") or "").strip()
                if tool_name:
                    from core.events import tool_result_is_error

                    output = data.get("output")
                    logger.info(
                        "tool result name=%s ok=%s size=%d",
                        tool_name,
                        not tool_result_is_error(output),
                        len(str(output)),
                    )
            observer(kind, data)

        return _logging_observer

    def _resolved_integrations(self) -> dict[str, Any]:
        from core.agent_harness.session.integration_resolution import resolve_and_cache_integrations

        # resolve_and_cache_integrations returns a fresh dict.
        return resolve_and_cache_integrations(self._session)
