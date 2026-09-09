"""/goal slash sugar over SessionGoal."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from core.agent_harness.session_goal.goal import session_goal_is_active
from surfaces.interactive_shell.command_registry.session_cmds.goal import _cmd_goal
from surfaces.interactive_shell.session import Session


def _console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, force_terminal=False, width=120), buf


def test_goal_set_show_and_clear() -> None:
    session = Session()
    console, buf = _console()

    assert _cmd_goal(session, console, ["set", "--max-turns", "3", "finish the checklist"])
    assert session_goal_is_active(session)
    assert session.session_goal is not None
    assert session.session_goal.max_outer_turns == 3
    assert "finish the checklist" in session.session_goal.condition

    buf.truncate(0)
    buf.seek(0)
    assert _cmd_goal(session, console, [])
    out = buf.getvalue()
    assert "finish the checklist" in out

    assert _cmd_goal(session, console, ["clear"])
    assert not session_goal_is_active(session)


def test_goal_set_queues_condition_as_immediate_turn() -> None:
    """Setting a goal starts work without a separate prompt."""
    session = Session()
    console, buf = _console()

    assert _cmd_goal(session, console, ["set", "--max-turns", "3", "all auth tests pass"])
    assert session.terminal.pending_prompt_default == "all auth tests pass"
    assert session.terminal.pending_prompt_autosubmit is True
    assert session.session_goal is not None
    assert session.session_goal.host_owned is True
    # A condition without numbered steps gets no checklist: one item would only echo it.
    assert session.session_goal.checklist == ()
    assert session.session_goal.max_outer_turns == 3
    # The block printed here is remembered, so the loop's first paint is one line.
    assert session.terminal.goal_paint_signature is not None
    assert session.session_goal.started_at is not None
    out = buf.getvalue()
    assert "◎ /goal active" in out
    assert "—" not in out.split("◎ /goal active", 1)[1].split("\n", 1)[0]
    assert "+0 tokens" in out
    assert "own prompt turn" in out
    assert "[N] ❯" in out


def test_goal_show_prints_active_duration_and_tokens() -> None:
    session = Session()
    session.tokens.record(input_tokens=100, output_tokens=50)
    console, buf = _console()

    assert _cmd_goal(session, console, ["set", "--max-turns", "4", "ship the fix"])
    session.tokens.record(input_tokens=200, output_tokens=100)

    buf.truncate(0)
    buf.seek(0)
    assert _cmd_goal(session, console, ["show"])
    out = buf.getvalue()
    assert "◎ /goal active" in out
    assert "ship the fix" in out
    assert "tokens" in out.lower()
    assert "turn" in out.lower()


def test_bare_goal_condition_is_set_and_starts_turn() -> None:
    """Bare ``/goal <condition>`` (no ``set``) attaches and autosubmits."""
    session = Session()
    console, _buf = _console()

    assert _cmd_goal(session, console, ["all", "tests", "in", "test/auth", "pass"])
    assert session_goal_is_active(session)
    assert session.session_goal is not None
    assert session.session_goal.condition == "all tests in test/auth pass"
    assert session.terminal.pending_prompt_default == "all tests in test/auth pass"
    assert session.terminal.pending_prompt_autosubmit is True


def test_goal_pause_resume_and_edit() -> None:
    from core.agent_harness.session_goal.goal import (
        session_goal_is_attached,
        session_goal_is_paused,
    )

    session = Session()
    console, buf = _console()

    assert _cmd_goal(session, console, ["set", "--max-turns", "4", "ship the fix"])
    assert session_goal_is_active(session)
    turns_before = session.session_goal.turns_used if session.session_goal else -1

    buf.truncate(0)
    buf.seek(0)
    assert _cmd_goal(session, console, ["pause"])
    assert session_goal_is_paused(session)
    assert session_goal_is_attached(session)
    assert not session_goal_is_active(session)
    assert "◎ /goal paused" in buf.getvalue()
    assert session.session_goal is not None
    assert session.session_goal.turns_used == turns_before

    buf.truncate(0)
    buf.seek(0)
    assert _cmd_goal(session, console, ["edit", "ship", "the", "fix", "and", "tests"])
    assert session.session_goal is not None
    assert session.session_goal.condition == "ship the fix and tests"
    assert session_goal_is_paused(session)
    assert session.terminal.pending_prompt_autosubmit is not True

    buf.truncate(0)
    buf.seek(0)
    assert _cmd_goal(session, console, ["resume"])
    assert session_goal_is_active(session)
    assert "◎ /goal active" in buf.getvalue()
    assert session.terminal.pending_prompt_default == "ship the fix and tests"
    assert session.terminal.pending_prompt_autosubmit is True


def test_goal_edit_while_active_queues_new_condition() -> None:
    session = Session()
    console, _buf = _console()

    assert _cmd_goal(session, console, ["set", "old condition"])
    session.terminal.pending_prompt_default = None
    session.terminal.pending_prompt_autosubmit = False
    assert _cmd_goal(session, console, ["edit", "new", "condition"])
    assert session.session_goal is not None
    assert session.session_goal.condition == "new condition"
    assert session_goal_is_active(session)
    assert session.terminal.pending_prompt_default == "new condition"
    assert session.terminal.pending_prompt_autosubmit is True


def test_goal_show_includes_paused() -> None:
    from core.agent_harness.session_goal.goal import (
        SessionGoal,
        SessionGoalStatus,
        attach_session_goal,
    )

    session = Session()
    console, buf = _console()
    attach_session_goal(
        session,
        SessionGoal(
            condition="paused work",
            max_outer_turns=3,
            status=SessionGoalStatus.PAUSED,
            host_owned=True,
        ),
    )
    assert _cmd_goal(session, console, ["show"])
    assert "◎ /goal paused" in buf.getvalue()
    assert "paused work" in buf.getvalue()


def test_goal_pause_flushes_so_resolve_keeps_paused() -> None:
    """Gateway rebuilds SessionCore from disk each turn — pause must persist.

    Without a flush, the next inbound message restores the pre-pause ACTIVE
    snapshot and ``run_until_session_goal`` continues despite ``/goal pause``.
    """
    from core.agent_harness.session import InMemorySessionStore, SessionCore, SessionManager
    from core.agent_harness.session_goal.goal import (
        session_goal_is_active,
        session_goal_is_paused,
    )
    from core.agent_harness.session_goal.persist import SESSION_GOAL_STATE_CUSTOM_TYPE

    store = InMemorySessionStore()
    session = Session()
    session.store = store
    store.open_session(session)
    store.append_turn(session, "chat", "seed")
    console, _buf = _console()

    assert _cmd_goal(session, console, ["set", "--max-turns", "4", "ship the fix"])
    assert session_goal_is_active(session)
    assert _cmd_goal(session, console, ["pause"])
    assert session_goal_is_paused(session)

    goal_records = [
        rec
        for rec in store.read(session.session_id)
        if rec.get("type") == "custom_message"
        and rec.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
    ]
    assert goal_records
    payload = goal_records[-1]["content"]
    assert isinstance(payload, dict)
    goal_payload = payload.get("session_goal")
    assert isinstance(goal_payload, dict)
    assert goal_payload.get("status") == "paused"
    assert goal_payload.get("condition") == "ship the fix"

    restored = SessionCore(store=InMemorySessionStore())
    SessionManager(store=InMemorySessionStore()).restore_context(
        restored,
        {
            "cli_agent_messages": [],
            "accumulated_context": {},
            "session_goal_state": payload,
            "history": [],
        },
    )
    assert session_goal_is_paused(restored)
    assert not session_goal_is_active(restored)


def test_goal_pause_on_session_core_without_terminal() -> None:
    """Gateway SessionCore has no ``terminal`` — pause must still flush.

    Greptile P1: ``session.terminal.…`` raised AttributeError before
    ``_persist_goal_state``, so the next inbound message restored ACTIVE.
    """
    from core.agent_harness.session import InMemorySessionStore, SessionCore, SessionManager
    from core.agent_harness.session_goal.goal import (
        SessionGoal,
        SessionGoalStatus,
        attach_session_goal,
        session_goal_is_active,
        session_goal_is_paused,
    )
    from core.agent_harness.session_goal.persist import SESSION_GOAL_STATE_CUSTOM_TYPE

    store = InMemorySessionStore()
    core = SessionCore(store=store)
    store.open_session(core)
    store.append_turn(core, "chat", "seed")
    attach_session_goal(
        core,
        SessionGoal(
            condition="ship the fix",
            max_outer_turns=4,
            status=SessionGoalStatus.ACTIVE,
            host_owned=True,
        ),
    )
    SessionManager.for_session(core).flush(core)
    assert session_goal_is_active(core)
    assert not hasattr(core, "terminal") or getattr(core, "terminal", None) is None

    console, buf = _console()
    assert _cmd_goal(core, console, ["pause"])
    assert session_goal_is_paused(core)
    assert "◎ /goal paused" in buf.getvalue()

    goal_records = [
        rec
        for rec in store.read(core.session_id)
        if rec.get("type") == "custom_message"
        and rec.get("custom_type") == SESSION_GOAL_STATE_CUSTOM_TYPE
    ]
    assert goal_records
    payload = goal_records[-1]["content"]
    assert isinstance(payload, dict)
    assert payload.get("session_goal", {}).get("status") == "paused"
