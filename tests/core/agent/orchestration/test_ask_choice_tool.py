"""Tests for the agent ask_user_choice tool.

Focus: the tool must never run a raw-stdin picker inline. On an interactive
REPL it stores the pending choice and defers to the ``/choose`` exclusive-stdin
turn; on non-TTY / headless surfaces it tells the model to fall back to a
numbered list.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from core.agent_harness.tools.tool_context import (
    ACTION_TOOL_CONTEXT_RESOURCE_KEY,
    ActionToolScope,
)
from core.agent_harness.turns.headless_adapters import InMemorySessionState
from core.tool import AgentToolContext
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.ask_choice import (
    ask_user_choice_tool,
    execute_ask_user_choice_tool,
)

_TITLE = "How should I handle the uncommitted changes?"
_OPTIONS = [
    "Stash the changes (recommended – quick & safe)",
    "Commit the changes",
    "Use a separate git worktree",
]


@dataclass
class _Ports:
    """Minimal slash-ports fake: the tool only consults ``tty_interactive``."""

    tty: bool = True

    def tty_interactive(self) -> bool:
        return self.tty


def _ctx(
    *,
    session: Any | None = None,
    ports: _Ports | None = None,
    is_tty: bool | None = None,
) -> ActionToolScope:
    console = Console(file=io.StringIO(), force_terminal=False, highlight=False)
    return ActionToolScope(
        session=session if session is not None else Session(),
        console=console,
        slash_ports=ports if ports is not None else _Ports(),
        is_tty=is_tty,
    )


def test_ask_user_choice_tool_is_action_surface_read_only() -> None:
    assert ask_user_choice_tool.name == "ask_user_choice"
    assert "action" in ask_user_choice_tool.surfaces
    assert ask_user_choice_tool.side_effect_level == "read_only"
    assert ask_user_choice_tool.parallel_safe is False
    assert any(
        "headless, scheduled, gateway, or /goal" in example
        for example in ask_user_choice_tool.anti_examples
    )


def test_interactive_repl_defers_menu_to_choose_turn() -> None:
    session = Session()
    ctx = _ctx(session=session)

    result = execute_ask_user_choice_tool({"title": _TITLE, "options": _OPTIONS}, ctx)

    assert result["ok"] is True
    assert result["menu"] == "queued"
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.title == _TITLE
    assert session.pending_user_choice.options == tuple(_OPTIONS)
    assert session.terminal.pending_prompt_default == "/choose"
    assert session.terminal.pending_prompt_autosubmit is True


def test_non_tty_ports_fall_back_to_numbered_list() -> None:
    session = Session()
    ctx = _ctx(session=session, ports=_Ports(tty=False))

    result = execute_ask_user_choice_tool({"title": _TITLE, "options": _OPTIONS}, ctx)

    assert result["ok"] is True
    assert result["menu"] == "unavailable"
    assert session.pending_user_choice is None
    assert session.terminal.pending_prompt_default is None


def test_explicit_non_tty_turn_falls_back() -> None:
    session = Session()
    ctx = _ctx(session=session, is_tty=False)

    result = execute_ask_user_choice_tool({"title": _TITLE, "options": _OPTIONS}, ctx)

    assert result["menu"] == "unavailable"
    assert session.pending_user_choice is None


def test_headless_session_without_terminal_falls_back() -> None:
    session = InMemorySessionState()
    ctx = _ctx(session=session)

    result = execute_ask_user_choice_tool({"title": _TITLE, "options": _OPTIONS}, ctx)

    assert result["ok"] is True
    assert result["menu"] == "unavailable"


def test_missing_title_is_rejected() -> None:
    result = execute_ask_user_choice_tool({"title": "  ", "options": _OPTIONS}, _ctx())
    assert result["ok"] is False


def test_fewer_than_two_distinct_options_is_rejected() -> None:
    session = Session()
    ctx = _ctx(session=session)

    result = execute_ask_user_choice_tool(
        {"title": _TITLE, "options": ["Stash", "Stash", "  "]},
        ctx,
    )

    assert result["ok"] is False
    assert session.pending_user_choice is None


def test_too_many_options_is_rejected() -> None:
    options = [f"Option {index}" for index in range(9)]
    result = execute_ask_user_choice_tool({"title": _TITLE, "options": options}, _ctx())
    assert result["ok"] is False


_QUESTIONS = [
    {
        "label": "Codebase",
        "title": "Where does the /api/orders service live?",
        "options": ["Hypothetical/demo scenario, no real code", "I'll point you at a repo"],
    },
    {
        "label": "Metrics",
        "title": "How should I get the p99 latency data?",
        "options": ["I'll paste the raw numbers/graph description", "Query Datadog"],
    },
    {
        "label": "Window",
        "title": "What's the time window of the p99 regression?",
        "options": ["Last 7 days", "Last 24 hours"],
    },
]


def test_batched_questions_queue_one_ask_user_menu() -> None:
    session = Session()
    ctx = _ctx(session=session)

    result = execute_ask_user_choice_tool(
        {"title": "Ask User", "questions": _QUESTIONS},
        ctx,
    )

    assert result["ok"] is True
    assert result["menu"] == "queued"
    assert "update_plan" in result["instruction"]
    pending = session.pending_user_choice
    assert pending is not None
    assert pending.is_batch() is True
    assert len(pending.questions) == 3
    assert pending.questions[0].label == "Codebase"
    assert session.terminal.pending_prompt_default == "/choose"
    assert session.terminal.awaiting_handoff_answer is True


def test_questions_only_payload_is_valid_and_queues() -> None:
    """The model omits title when sending a batched questions payload."""
    session = Session()
    error = ask_user_choice_tool.validate_public_input({"questions": _QUESTIONS})
    assert error is None
    result = execute_ask_user_choice_tool({"questions": _QUESTIONS}, _ctx(session=session))
    assert result["ok"] is True
    assert result["menu"] == "queued"
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.title == "Ask User"


def test_one_item_questions_array_is_rejected() -> None:
    result = execute_ask_user_choice_tool(
        {"title": "Ask User", "questions": _QUESTIONS[:1]},
        _ctx(),
    )
    assert result["ok"] is False
    assert "title and options" in result["error"]


def test_malformed_question_is_rejected() -> None:
    result = execute_ask_user_choice_tool(
        {"title": "Ask User", "questions": [{"label": "Codebase", "title": "Where?"}]},
        _ctx(),
    )
    assert result["ok"] is False


def test_multi_select_string_false_is_not_truthy() -> None:
    session = Session()
    result = execute_ask_user_choice_tool(
        {"title": _TITLE, "options": _OPTIONS, "multi_select": "false"},
        _ctx(session=session),
    )
    assert result["ok"] is True
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.multi_select is False


def test_multi_select_true_on_single_decision() -> None:
    session = Session()
    result = execute_ask_user_choice_tool(
        {"title": _TITLE, "options": _OPTIONS, "multi_select": True},
        _ctx(session=session),
    )
    assert result["ok"] is True
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.multi_select is True


def test_per_question_multi_select_string_false() -> None:
    session = Session()
    questions = [
        {
            "label": "Extras",
            "title": "Which extras?",
            "options": ["Unit tests", "Dockerfile"],
            "multi_select": "false",
        },
        {
            "label": "Lang",
            "title": "Language?",
            "options": ["Python", "Go"],
        },
    ]
    result = execute_ask_user_choice_tool(
        {"title": "Ask User", "questions": questions},
        _ctx(session=session),
    )
    assert result["ok"] is True
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.questions[0].multi_select is False


def test_allow_custom_from_the_model_reaches_the_pending_choice() -> None:
    # Arrange: the registry calls ``run`` with the model's public arguments as
    # keywords, so every schema property must be accepted by the signature.
    session = Session()
    context = AgentToolContext(
        resolved_integrations={},
        resources={ACTION_TOOL_CONTEXT_RESOURCE_KEY: _ctx(session=session)},
    )

    # Act
    result = ask_user_choice_tool.run(
        title=_TITLE, options=_OPTIONS, allow_custom=False, context=context
    )

    # Assert
    assert result["ok"] is True
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.custom_answer is False
