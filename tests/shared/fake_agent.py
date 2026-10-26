"""A mock agent whose ``handle`` is the harness's real host loop over the mock's ``bind_turn``/``dispatch``.

Host tests fake the agent to assert what the host binds and dispatches. Since
hosts call :meth:`HeadlessAgent.handle`, the fake must run the same loop the
real agent does — bind the turn, dispatch, continue while a session goal is
attached — so ``bind_turn.call_args`` and ``dispatch.call_count`` keep meaning
what they meant.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

from core.agent_harness.spi.session_goal import run_until_session_goal


def fake_agent(*, dispatch_result: Any = None) -> MagicMock:
    """A ``MagicMock`` agent with a real ``handle``; ``dispatch`` returns ``dispatch_result``."""
    agent = MagicMock()
    if dispatch_result is not None:
        agent.dispatch.return_value = dispatch_result
    attach_real_handle(agent)
    return agent


def attach_real_handle(agent: MagicMock) -> MagicMock:
    """Give an existing mock agent the real host loop; returns the same mock.

    ``agent.bound_session`` stands for the real agent's currently bound session
    (``None`` until a binding with a session is applied); ``bind_turn`` on the
    mock is left to the test to assert on, so it is tracked here explicitly.
    """
    agent.bound_session = None
    original_bind = agent.bind_turn

    def _bind(binding: Any) -> Any:
        if binding.session is not None:
            agent.bound_session = binding.session
        return original_bind(binding)

    agent.bind_turn = MagicMock(side_effect=_bind)

    def _handle(
        text: str,
        binding: Any,
        *,
        accounting_factory: Any = None,
        cancel_requested: Any = None,
        cancel_reason: Any = None,
        on_progress: Any = None,
    ) -> Any:
        def _one_turn(message: str) -> Any:
            accounting = accounting_factory(message) if accounting_factory is not None else None
            agent.bind_turn(replace(binding, accounting=accounting))
            return agent.dispatch(message)

        # Same selection as HeadlessAgent.handle: the session this turn states,
        # else the agent's currently bound one.
        session = binding.session if binding.session is not None else agent.bound_session
        return run_until_session_goal(
            _one_turn,
            session,
            text,
            cancel_requested=cancel_requested,
            cancel_reason=cancel_reason,
            on_progress=on_progress,
        ).last_result

    agent.handle.side_effect = _handle
    return agent


__all__ = ["attach_real_handle", "fake_agent"]
