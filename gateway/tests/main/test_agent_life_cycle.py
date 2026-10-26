"""
Description:
--------------------------------
The problem that I concretely need to solve now with this test
is that I do not believe that our agent has the same data access as the agent in the interactive shell
so we need to bridge that gap between the two.

The approach to do that is:
- start the gateway and get the session
- this initializes the agent.
- the agent is being passed down the gateway to the event handler to execute the event loops

--------------------------------
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import MagicMock, patch

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from gateway.core.billing.turn_metering import bound_turn_metering
from gateway.core.lifecycle.controller import GatewayController, start_gateway
from gateway.core.lifecycle.errors import GatewayConfigurationError
from gateway.transports.names import TransportName
from gateway.transports.telegram.inbound_handler import (
    handle_polled_inbound_telegram_message,
)
from gateway.transports.telegram.inbound_security import InboundDecision
from gateway.transports.telegram.settings import (
    GatewaySettings,
    TelegramInboundMessage,
)
from gateway.web.startup import WebStartup
from tests.shared.default_headless_build_stub import default_headless_build_stub
from tests.shared.fake_agent import attach_real_handle


def _patch_non_telegram_components(monkeypatch) -> None:
    """Keep lifecycle tests focused on the Telegram worker: skip web/scheduler/pidfile."""
    monkeypatch.setattr(
        "gateway.startup.start_web_server",
        lambda **_kwargs: WebStartup(server=None, status="skipped in lifecycle test"),
    )
    monkeypatch.setattr(GatewayController, "start_scheduler", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(GatewayController, "_publish_status", lambda *_args: None)

    def _skip_transport(**_kwargs: Any) -> None:
        raise GatewayConfigurationError("skipped in lifecycle test")

    monkeypatch.setattr(
        "gateway.transports.startup.start_slack_worker",
        _skip_transport,
    )
    monkeypatch.setattr(
        "gateway.transports.startup.start_discord_worker",
        _skip_transport,
    )


def _patch_process_boot(monkeypatch) -> None:
    """Neutralize shared bootstrap.process side effects during lifecycle tests."""
    from bootstrap.process import reset_process_runtime_for_tests

    reset_process_runtime_for_tests()
    monkeypatch.setattr(
        "bootstrap.process.bootstrap_opensre_env_once",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr("bootstrap.process.install_harness_adapters", lambda: None)
    monkeypatch.setattr(
        "infrastructure.observability.errors.sentry.init_sentry",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr("core.llm.internal.preload.preload_llm_clients", lambda: None)
    monkeypatch.setattr(
        "infrastructure.safety.sandbox.capabilities.boot_capability_warnings",
        lambda: [],
    )


def test_gateway_start_returns_running_gateway_handle(monkeypatch) -> None:
    settings = GatewaySettings(bot_token="tok", auto_start_enabled=False)
    logger = logging.getLogger("gateway.lifecycle.test")
    handle = MagicMock()
    agent_cls = MagicMock()
    attach_real_handle(agent_cls.return_value)
    signal_calls: list[tuple[int, Any]] = []
    background_kwargs: dict[str, Any] = {}

    _patch_process_boot(monkeypatch)
    monkeypatch.setattr("gateway.core.lifecycle.controller.configure_logging", lambda: logger)
    _patch_non_telegram_components(monkeypatch)
    monkeypatch.setattr(
        "gateway.transports.telegram.startup.load_gateway_settings", lambda: settings
    )
    monkeypatch.setattr(
        "gateway.core.lifecycle.controller.signal.signal",
        lambda signum, handler: signal_calls.append((signum, handler)),
    )
    # Patch the agent factory the gateway uses so the turn callback is spyable.
    monkeypatch.setattr(
        "infrastructure.turn_host.session_agents.DefaultHeadlessBuild",
        default_headless_build_stub(agent_cls),
    )

    def _start_telegram_gateway_background(**kwargs: Any) -> MagicMock:
        background_kwargs.update(kwargs)
        return handle

    monkeypatch.setattr(
        "gateway.transports.telegram.startup.start_telegram_gateway_background",
        _start_telegram_gateway_background,
    )

    gateway = GatewayController().start_gateway(wait=False)

    assert isinstance(gateway, GatewayController)
    assert gateway.logger is logger
    assert gateway.surfaces is not None
    telegram = gateway.surfaces.transports.get(TransportName.TELEGRAM)
    assert telegram is not None
    assert telegram.worker is handle
    assert background_kwargs["settings"] is settings
    assert background_kwargs["logger"] is logger
    assert background_kwargs["handle_callback_to_gateway_agent"] is not None
    assert signal_calls
    handle.wait.assert_not_called()

    sink = MagicMock()
    session = MagicMock()
    agent_cls.return_value.dispatch.return_value = TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
            response_text="Hawaii: +25C",
        ),
        assistant_response_text="Hawaii: +25C",
    )
    callback = background_kwargs["handle_callback_to_gateway_agent"]
    with bound_turn_metering(
        organization_id="org_lifecycle",
        reason="telegram_turn",
        idempotency_key="telegram:1",
        on_denied=MagicMock(),
    ):
        callback("hello", session, sink, logger)
    agent_cls.return_value.dispatch.assert_called_once()
    sink.finalize.assert_called_once_with("Hawaii: +25C")
    assert agent_cls.return_value.dispatch.call_args.args == ("hello",)
    ctor = agent_cls.call_args
    assert ctor.kwargs["session"] is session
    # Session-scoped agent holds a live sink proxy; the transport sink is bound
    # each turn (not passed as the constructor ``output`` identity).
    from infrastructure.turn_host.bindable_output import BindableOutput

    assert isinstance(ctor.kwargs["output"], BindableOutput)
    assert ctor.kwargs["surface"] == "gateway"
    turn_binding = agent_cls.return_value.bind_turn.call_args.args[0]
    assert turn_binding.is_tty is False
    # The gateway hands the factory its configured tool provider (the bridge
    # between the agent and the host's tool stack), not loose port factories.
    tool_provider = ctor.kwargs["tools"]
    assert isinstance(tool_provider, DefaultToolProvider)
    assert tool_provider._session is session
    assert tool_provider._tool_action_logger is logger
    assert tool_provider._precomputed_action_tools is None
    with patch.object(logger, "info") as mock_info:
        tool_provider.observer(message="hello")(
            "tool_start",
            {"name": "shell_run", "input": {"command": "pwd"}},
        )
    mock_info.assert_called_once_with(
        "tool action name=%s input=%s",
        "shell_run",
        "{'command': 'pwd'}",
    )
    # The host passes no accounting: the agent's default per-message accounting
    # (DefaultTurnAccounting) applies — pinned in test_default_accounting_is_resolved_fresh_per_message.
    turn_binding = agent_cls.return_value.bind_turn.call_args.args[0]
    assert turn_binding.accounting is None


def test_polled_telegram_message_reaches_start_gateway_agent_callback(monkeypatch) -> None:
    settings = GatewaySettings(
        bot_token="tok",
        auto_start_enabled=False,
        allowed_user_ids=["user-1"],
        stream_edit_interval_seconds=0.01,
    )
    logger = logging.getLogger("gateway.lifecycle.e2e.test")
    handle = MagicMock()
    background_kwargs: dict[str, Any] = {}

    class FakeSessionResolver:
        def __init__(self, session: SessionCore) -> None:
            self._session = session

        def resolve(self, *, user_id: str, chat_id: str, **kwargs: object) -> SessionCore:
            assert user_id == "user-1"
            assert chat_id == "chat-1"
            assert kwargs.get("principal") is not None
            assert kwargs.get("actor") is not None
            return self._session

        def rotate(self, *, user_id: str, chat_id: str, **_kwargs: object) -> SessionCore:
            assert user_id == "user-1"
            assert chat_id == "chat-1"
            return self._session

    monkeypatch.setenv("ORGANIZATION_ID", "org_lifecycle_e2e")
    _patch_process_boot(monkeypatch)
    monkeypatch.setattr("gateway.core.lifecycle.controller.configure_logging", lambda: logger)
    _patch_non_telegram_components(monkeypatch)
    monkeypatch.setattr(
        "gateway.transports.telegram.startup.load_gateway_settings", lambda: settings
    )
    monkeypatch.setattr("gateway.core.lifecycle.controller.signal.signal", lambda *_args: None)

    def _start_telegram_gateway_background(**kwargs: Any) -> MagicMock:
        background_kwargs.update(kwargs)
        return handle

    monkeypatch.setattr(
        "gateway.transports.telegram.startup.start_telegram_gateway_background",
        _start_telegram_gateway_background,
    )
    monkeypatch.setattr(
        "gateway.transports.telegram.inbound_handler.enforce_inbound_telegram_message_security",
        lambda **_kwargs: InboundDecision(allowed=True),
    )

    GatewayController().start_gateway(wait=False)
    callback = background_kwargs["handle_callback_to_gateway_agent"]
    session = SessionCore(store=InMemorySessionStore())
    client = MagicMock()
    client.send_message.return_value = (True, "", "message-1")
    client.edit_message_text.return_value = (True, "")

    async def _run_message() -> None:
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            from gateway.core.middleware.active_turns import ActiveTurnRegistry
            from gateway.core.middleware.approvals import ApprovalBroker

            await handle_polled_inbound_telegram_message(
                TelegramInboundMessage(
                    update_id=1,
                    user_id="user-1",
                    chat_id="chat-1",
                    message_id="telegram-message-1",
                    text="/status",
                ),
                client=client,
                session_resolver=FakeSessionResolver(session),
                settings=settings,
                executor=executor,
                chat_locks={},
                turn_semaphore=asyncio.Semaphore(1),
                approvals=ApprovalBroker(),
                active_cancels=ActiveTurnRegistry(),
                handle_callback_to_gateway_agent=callback,
            )
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

    asyncio.run(_run_message())
    # Typing fires at sink creation and again on each status refresh.
    client.send_chat_action.assert_called_with("chat-1", "typing")


def test_gateway_start_continues_without_telegram_configuration(monkeypatch) -> None:
    """The unified daemon keeps its other components when Telegram is unconfigured."""
    logger = logging.getLogger("gateway.lifecycle.test")
    _patch_process_boot(monkeypatch)
    monkeypatch.setattr("gateway.core.lifecycle.controller.configure_logging", lambda: logger)
    monkeypatch.setattr("gateway.core.lifecycle.controller.signal.signal", lambda *_args: None)
    monkeypatch.setattr("gateway.core.lifecycle.controller.clear_component_status", lambda: None)
    _patch_non_telegram_components(monkeypatch)

    def _unconfigured() -> GatewaySettings:
        raise GatewayConfigurationError("TELEGRAM_BOT_TOKEN is not set")

    monkeypatch.setattr("gateway.transports.telegram.startup.load_gateway_settings", _unconfigured)

    gateway = GatewayController().start_gateway(wait=False)

    assert gateway.surfaces is not None
    assert TransportName.TELEGRAM not in gateway.surfaces.transports
    assert gateway.components["telegram"].startswith("not configured")
    assert gateway.stop() is True
    assert gateway.surfaces is None


def test_start_gateway_wrapper_delegates_to_gateway_instance(monkeypatch) -> None:
    expected = MagicMock(spec=GatewayController)
    calls: list[bool] = []
    factory = object()

    def _start_gateway(
        self: GatewayController,
        *,
        wait: bool = True,
    ) -> GatewayController:
        assert isinstance(self, GatewayController)
        assert self._slash_ports_factory is factory
        calls.append(wait)
        return expected

    monkeypatch.setattr(GatewayController, "start_gateway", _start_gateway)

    assert start_gateway(wait=False, slash_ports_factory=factory) is expected  # type: ignore[arg-type]
    assert calls == [False]
