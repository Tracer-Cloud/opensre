"""Session resume slash command and helpers: /resume."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from core.agent_harness import SessionManager
from core.agent_harness.spi.defaults import default_session_repo
from core.agent_harness.spi.session_state import format_recovery_note
from infrastructure.turn_host.session_lock import (
    SessionExecutionBusyError,
    retain_session_execution_lock,
    session_execution_lock,
)
from surfaces.interactive_shell.command_registry.session_cmds.resume_rendering import (
    render_resumed_session_history,
)
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT, WARNING
from surfaces.interactive_shell.ui.resume_picker import (
    ResumeMenuItem,
    choose_resume_session,
)
from surfaces.shared.terminal.components.choice_menu import (
    prepare_repl_output_line,
    repl_tty_interactive,
)

_RECENT_CONVERSATION_LIMIT = 200


def _record_resume_slash(
    session: Session,
    args: list[str],
    *,
    ok: bool = True,
    picked_id: str | None = None,
) -> None:
    """Record /resume in the active session file after identity is settled."""
    if picked_id:
        text = f"/resume {picked_id[:8]}"
    elif args:
        text = f"/resume {' '.join(args)}"
    else:
        text = "/resume"
    session.record("slash", text, ok=ok)


def _interactive_resume_menu(session: Session, console: Console) -> bool:
    """Show recent conversations and resume the selected one."""
    repo = default_session_repo()
    items: list[ResumeMenuItem] = []
    for entry in repo.load_recent(
        _RECENT_CONVERSATION_LIMIT + 1,
        require_conversation=True,
    ):
        sid = entry["session_id"]
        if sid == session.session_id:
            continue
        title = entry.get("conversation_title") or ""
        if not title:
            continue
        items.append(
            ResumeMenuItem(
                session_id=sid,
                title=title,
                activity_at=entry.get("activity_at") or entry.get("started_at"),
            )
        )
        if len(items) >= _RECENT_CONVERSATION_LIMIT:
            break
    if not items:
        console.print(f"[{DIM}]No previous conversations to resume.[/]")
        return True

    picked = choose_resume_session(items)
    if picked is None:
        return True

    prepare_repl_output_line()
    slash_command = f"/resume {picked[:8]}"
    if not _do_resume(picked, session, console, slash_command=slash_command):
        _record_resume_slash(session, [], picked_id=picked, ok=False)
    return True


def _apply_resume_data_unlocked(
    data: dict,
    session: Session,
    console: Console,
    *,
    slash_command: str | None = None,
) -> bool:
    """Apply loaded session data into the running session and print a summary."""
    messages = data.get("cli_agent_messages") or []
    context = data.get("accumulated_context") or {}
    history = data.get("history") or []
    has_snapshot = data.get("has_snapshot", False)
    sid = data.get("session_id", "")
    short_id = sid[:8] if len(sid) >= 8 else sid
    name = data.get("name") or ""

    if not messages and not context:
        console.print(
            f"[{DIM}]session {short_id} has no conversation to resume "
            "(no chat turns or context found).[/]"
        )
        if not data.get("turn_details") and not has_snapshot:
            console.print(
                f"[{DIM}]tip: turn_detail records are only written when prompt logging is enabled.[/]"
            )
        if slash_command:
            session.record("slash", slash_command, ok=False)
        return True

    existing = session.agent.messages
    if existing:
        console.print(
            f"[{WARNING}]current session has {len(existing)} messages — "
            "they will be replaced by the resumed context.[/]"
        )

    manager = SessionManager.for_session(session)
    manager.rebind_for_resume(
        session,
        session_id=sid,
        started_at=data.get("started_at"),
    )
    manager.restore_context(session, data)

    source = "snapshot" if has_snapshot else "turn records"
    name_str = f" · {escape(name)}" if name else ""
    console.print(
        f"[{HIGHLIGHT}]resumed session {short_id}{name_str}[/] "
        f"[{DIM}]({len(messages)} messages in context from {source})[/]"
    )

    render_resumed_session_history(
        console,
        history=history,
        turn_details=data.get("turn_details") or [],
        messages=list(messages),
    )

    if context:
        console.print(
            f"[{DIM}]accumulated context restored:[/] "
            + ", ".join(f"{escape(k)}={escape(str(v))}" for k, v in sorted(context.items()))
        )

    recovery_note = format_recovery_note(data.get("dangling_tool_intents") or [])
    if recovery_note:
        # The full note goes to the next action turn's prompt; the console only
        # gets a short heads-up so the user knows why the agent may re-check.
        session.pending_recovery_note = recovery_note
        interrupted = len(data.get("dangling_tool_intents") or [])
        console.print(
            f"[{WARNING}]this session was interrupted mid-turn "
            f"({interrupted} tool call(s) never finished) — the agent will "
            "verify state before resuming.[/]"
        )

    if slash_command:
        session.record("slash", slash_command)

    return True


def _apply_resume_data(
    data: dict,
    session: Session,
    console: Console,
    *,
    slash_command: str | None = None,
    refresh_target: bool = False,
) -> bool:
    """Apply a resumed session while holding that target session's lease."""
    session_id = str(data.get("session_id") or "")
    if not session_id:
        return _apply_resume_data_unlocked(
            data,
            session,
            console,
            slash_command=slash_command,
        )

    # TurnRunner begins with the shell's current id, but /resume rebinds the
    # live handle.  Do not wait on a target while holding the source lease: two
    # shells crossing A -> B and B -> A would otherwise deadlock.  The user can
    # retry once the active host finishes its target turn.
    def _apply_with_target_lease() -> bool:
        nonlocal data
        if refresh_target:
            refreshed = default_session_repo().load_session(session_id)
            if refreshed is None:
                console.print(f"[{ERROR}]session '{escape(session_id)}' is no longer available.[/]")
                return False
            data = refreshed
        return _apply_resume_data_unlocked(
            data,
            session,
            console,
            slash_command=slash_command,
        )

    try:
        # When /resume executes inside TurnRunner, transfer the acquired target
        # lease to its whole-turn scope.  The shell keeps it through the final
        # SessionManager.flush after this slash handler returns.  Direct startup
        # resume still uses the ordinary lexical lease below.
        if retain_session_execution_lock(session_id, timeout=0, reentrant=True):
            return _apply_with_target_lease()
        with session_execution_lock(session_id, timeout=0, reentrant=True):
            return _apply_with_target_lease()
    except SessionExecutionBusyError:
        console.print(
            f"[{WARNING}]session {escape(session_id[:8])} is busy in another process — retry shortly.[/]"
        )
        return False


def _lookup_resume_session_data(
    prefix: str,
    session: Session,
    console: Console,
) -> dict | None:
    """Resolve a session to resume by ID prefix or name substring."""
    from core.agent_harness.spi.defaults import default_session_repo

    repo = default_session_repo()
    data = repo.load_session(prefix)
    if data is None and len(prefix) >= 3:
        candidates = [
            e
            for e in repo.load_recent(20)
            if prefix.lower() in (e.get("name") or "").lower()
            and e["session_id"] != session.session_id
        ]
        if len(candidates) == 1:
            data = repo.load_session(candidates[0]["session_id"])
        elif len(candidates) > 1:
            console.print(
                f"[{WARNING}]'{escape(prefix)}' matches {len(candidates)} sessions by name — "
                "use a session ID prefix or be more specific.[/]"
            )
            return None

    if data is not None:
        return data

    n = repo.count_prefix_matches(prefix)
    if n > 1:
        console.print(
            f"[{WARNING}]ambiguous prefix '{escape(prefix)}' matches {n} sessions — "
            "use more characters.[/]"
        )
    else:
        console.print(f"[{ERROR}]session '{escape(prefix)}' not found.[/]")
    return None


def _do_resume(
    prefix: str,
    session: Session,
    console: Console,
    *,
    slash_command: str | None = None,
) -> bool:
    """Load session by ID prefix and restore context into the running session."""
    data = _lookup_resume_session_data(prefix, session, console)
    if data is None:
        return False
    return _apply_resume_data(
        data,
        session,
        console,
        slash_command=slash_command,
        refresh_target=True,
    )


def resume_session_by_prefix(
    prefix: str,
    session: Session,
    console: Console,
    *,
    slash_command: str | None = None,
) -> bool:
    """Load session by ID prefix and restore context into the running session."""
    return _do_resume(prefix, session, console, slash_command=slash_command)


def _cmd_resume(session: Session, console: Console, args: list[str]) -> bool:
    if not args and repl_tty_interactive():
        return _interactive_resume_menu(session, console)

    if not args:
        console.print(f"[{DIM}]usage: /resume <session-id-prefix>[/]")
        console.print(f"[{DIM}]run /sessions to list session IDs.[/]")
        _record_resume_slash(session, args)
        return True

    prefix = args[0].strip()
    session_prefix = prefix.split(":", 1)[0]

    if session.session_id.startswith(session_prefix) and ":" not in prefix:
        console.print(
            f"[{DIM}]session {session_prefix[:8]} is the current session — "
            "run /sessions to pick a previous one.[/]"
        )
        _record_resume_slash(session, args)
        return True

    data = _lookup_resume_session_data(prefix, session, console)
    if data is None:
        _record_resume_slash(session, args, ok=False)
        return True

    slash_command = f"/resume {' '.join(args)}" if args else "/resume"
    if not _apply_resume_data(
        data,
        session,
        console,
        slash_command=slash_command,
        refresh_target=True,
    ):
        _record_resume_slash(session, args, ok=False)
    return True
