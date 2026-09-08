"""/goal slash command: show|set|pause|resume|edit|clear.

``/goal set`` attaches a completion condition and immediately queues that
condition as the next turn (autosubmit). Status shows the progress line
(``SESSION_GOAL_PROGRESS_MARK`` + ``/goal active``) with duration, turn budget,
and token delta. User-facing copy never says ``SessionGoal`` — only ``/goal``.
"""

from __future__ import annotations

from dataclasses import replace

from rich.console import Console
from rich.markup import escape as _rich_escape

from core.agent_harness import SessionManager
from core.agent_harness.spi.session_goal import (
    MAX_GOAL_CONDITION_CHARS,
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    attach_session_goal,
    clear_session_goal,
    derive_session_goal_checklist,
    format_session_goal_progress,
    goal_paint_signature,
    session_goal_is_active,
    session_goal_is_attached,
    session_goal_is_paused,
)
from core.agent_harness.spi.session_state import (
    clear_pending_autosubmit,
    session_terminal,
    set_auto_command,
)
from infrastructure.evidence.evidence_compaction import truncate_message
from infrastructure.terminal.theme import DIM, ERROR, HIGHLIGHT
from surfaces.interactive_shell.runtime import Session
from surfaces.shared.terminal.components.rendering import print_repl_text

_USAGE = "/goal [show|set|pause|resume|edit|clear|help]  or  /goal <condition>"
# A condition is plain prose describing the state to reach or the request to
# finish; flags come before it. First entry doubles as the empty-state example.
_SET_EXAMPLES: tuple[str, ...] = (
    "/goal set gateway p95 latency back under 500ms",
    "/goal set find out why checkout returns 502s and post the root cause",
    "/goal set --max-turns 10 fix the failing morning-report delivery to Slack",
)
_EDIT_EXAMPLE = "/goal edit also confirm the fix reached prod-eu-42"

# One example per subcommand, shown on the generic usage path.
_COMMAND_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("/goal", "show the active goal and its progress"),
    (_SET_EXAMPLES[0], "start working toward a condition"),
    ("/goal pause", "hold the goal without losing it"),
    ("/goal resume", "continue a paused goal"),
    (_EDIT_EXAMPLE, "replace the condition, keep progress"),
    ("/goal clear", "drop the goal"),
)
_HELP_FOOTER = "/goal · pause · resume · edit · clear"


def _print_set_usage(console: Console) -> None:
    console.print(f"[{ERROR}]usage:[/] /goal set [--max-turns N] <condition>")
    console.print(f"[{DIM}]examples:[/]")
    for example in _SET_EXAMPLES:
        console.print(f"  [{HIGHLIGHT}]{example}[/]")


def _print_goal_usage(console: Console) -> None:
    console.print(f"[{ERROR}]usage:[/] {_rich_escape(_USAGE)}")
    console.print(f"[{DIM}]examples:[/]")
    for example, what in _COMMAND_EXAMPLES:
        console.print(f"  [{HIGHLIGHT}]{example}[/] [{DIM}]— {what}[/]")


def _persist_goal_state(session: Session) -> None:
    """Flush so gateway ``resolve`` (fresh SessionCore per turn) sees mutations.

    Interactive shell keeps one in-memory session, but chat transports rebuild
    from the transcript each inbound message — pause/resume/edit/clear/set must
    land as ``session_goal_state`` or the next turn restores the prior snapshot.
    """
    SessionManager.for_session(session).flush(session)


def _show(session: Session, console: Console) -> bool:
    goal = getattr(session, "session_goal", None)
    if not isinstance(goal, SessionGoal) or not session_goal_is_attached(session):
        console.print(
            f"[{DIM}]no active goal.[/] Set one with [{HIGHLIGHT}]/goal set <condition>[/] "
            f"[{DIM}]— e.g.[/] [{HIGHLIGHT}]{_SET_EXAMPLES[0]}[/]"
        )
        return True
    print_repl_text(console, format_session_goal_progress(goal, session=session), markup=False)
    return True


def _set(session: Session, console: Console, args: list[str]) -> bool:
    max_turns = 5
    rest = list(args)
    while rest and rest[0].startswith("--"):
        flag = rest.pop(0)
        if flag in {"--max-turns", "--max_turns"} and rest:
            try:
                max_turns = max(1, int(rest.pop(0)))
            except ValueError:
                _print_set_usage(console)
                return True
        else:
            console.print(f"[{ERROR}]unknown flag:[/] {_rich_escape(flag)}")
            return True
    condition = " ".join(rest).strip()
    if not condition:
        _print_set_usage(console)
        return True
    checklist = derive_session_goal_checklist(condition)
    goal = SessionGoal(
        condition=condition,
        max_outer_turns=max_turns,
        status=SessionGoalStatus.ACTIVE,
        host_owned=True,
        checklist=checklist,
        step_count=len(checklist) or None,
    )
    goal = attach_session_goal(session, goal)
    _queue_condition(session, condition)
    _persist_goal_state(session)
    _print_goal_block(session, console, goal)
    console.print(
        f"[{DIM}]→ next: condition runs as its own prompt turn "
        f"(look for [/][{HIGHLIGHT}][N] ❯[/][{DIM}] below). [/]"
        f"[{DIM}]{_HELP_FOOTER}[/]"
    )
    return True


def _queue_condition(session: Session, condition: str) -> None:
    """Start the work: the REPL autosubmits the condition; headless hosts run it in-loop."""
    if session_terminal(session) is not None:
        set_auto_command(session, condition)


def _print_goal_block(session: Session, console: Console, goal: SessionGoal) -> None:
    """Print the full block once and remember it, so the loop repaints one line per turn."""
    print_repl_text(console, format_session_goal_progress(goal, session=session), markup=False)
    terminal = session_terminal(session)
    if terminal is not None:
        terminal.goal_paint_signature = goal_paint_signature(goal)


def _pause(session: Session, console: Console) -> bool:
    goal = getattr(session, "session_goal", None)
    if not isinstance(goal, SessionGoal) or not session_goal_is_active(session):
        if isinstance(goal, SessionGoal) and session_goal_is_paused(session):
            print_repl_text(
                console, format_session_goal_progress(goal, session=session), markup=False
            )
            console.print(f"[{DIM}]already paused.[/]")
            return True
        console.print(f"[{DIM}]no active goal to pause.[/]")
        return True
    paused = goal.with_status(SessionGoalStatus.PAUSED).with_reason(
        SessionGoalReason.PAUSED_BY_USER
    )
    paused = attach_session_goal(session, paused)
    # Drop any queued autosubmit so pause actually stops the next turn.
    # Gateway SessionCore has no terminal facet — must not AttributeError
    # before flush (or the next inbound message restores ACTIVE).
    clear_pending_autosubmit(session)
    _persist_goal_state(session)
    print_repl_text(console, format_session_goal_progress(paused, session=session), markup=False)
    console.print(f"[{DIM}]paused. {_HELP_FOOTER.replace('pause · ', '')}[/]")
    return True


def _resume(session: Session, console: Console) -> bool:
    goal = getattr(session, "session_goal", None)
    if not isinstance(goal, SessionGoal) or not session_goal_is_paused(session):
        if isinstance(goal, SessionGoal) and session_goal_is_active(session):
            print_repl_text(
                console, format_session_goal_progress(goal, session=session), markup=False
            )
            console.print(f"[{DIM}]already active.[/]")
            return True
        console.print(f"[{DIM}]no paused goal to resume.[/]")
        return True
    resumed = goal.with_status(SessionGoalStatus.ACTIVE).with_reason(
        SessionGoalReason.WAITING_HOST_SIGNAL
        if goal.host_owned
        else SessionGoalReason.WAITING_TOOL_EVIDENCE
    )
    resumed = attach_session_goal(session, resumed)
    _queue_condition(session, resumed.condition)
    _persist_goal_state(session)
    _print_goal_block(session, console, resumed)
    console.print(
        f"[{DIM}]→ next: condition runs as its own prompt turn "
        f"(look for [/][{HIGHLIGHT}][N] ❯[/][{DIM}] below). [/]"
        f"[{DIM}]{_HELP_FOOTER}[/]"
    )
    return True


def _edit(session: Session, console: Console, args: list[str]) -> bool:
    goal = getattr(session, "session_goal", None)
    if not isinstance(goal, SessionGoal) or not session_goal_is_attached(session):
        console.print(
            f"[{DIM}]no goal to edit. Set one with [/][{HIGHLIGHT}]/goal set <condition>[/]."
        )
        return True
    condition = " ".join(args).strip()
    if not condition:
        console.print(f"[{ERROR}]usage:[/] /goal edit <condition>")
        console.print(f"[{DIM}]e.g.[/] [{HIGHLIGHT}]{_EDIT_EXAMPLE}[/]")
        return True
    condition = truncate_message(condition, MAX_GOAL_CONDITION_CHARS)
    edited = replace(goal, condition=condition)
    edited = attach_session_goal(session, edited)
    if session_goal_is_active(session):
        _queue_condition(session, condition)
    _persist_goal_state(session)
    _print_goal_block(session, console, edited)
    if session_goal_is_active(session):
        console.print(
            f"[{DIM}]→ next: updated condition runs as its own prompt turn "
            f"(look for [/][{HIGHLIGHT}][N] ❯[/][{DIM}] below). [/]"
            f"[{DIM}]{_HELP_FOOTER}[/]"
        )
    else:
        console.print(f"[{DIM}]condition updated (still paused). {_HELP_FOOTER}[/]")
    return True


def _clear(session: Session, console: Console) -> bool:
    if getattr(session, "session_goal", None) is None:
        console.print(f"[{DIM}]no goal to clear.[/]")
        return True
    clear_session_goal(session)
    _persist_goal_state(session)
    console.print(f"[{HIGHLIGHT}]goal cleared.[/]")
    return True


def _cmd_goal(session: Session, console: Console, args: list[str]) -> bool:
    if not args:
        return _show(session, console)
    sub = args[0].lower()
    if sub in {"show", "status"}:
        return _show(session, console)
    if sub == "set":
        return _set(session, console, args[1:])
    if sub == "pause":
        return _pause(session, console)
    if sub == "resume":
        return _resume(session, console)
    if sub == "edit":
        return _edit(session, console, args[1:])
    if sub in {"clear", "unset"}:
        return _clear(session, console)
    if sub == "help":
        _print_goal_usage(console)
        return True
    # ``/goal <condition>`` is sugar for ``/goal set <condition>``.
    return _set(session, console, args)
