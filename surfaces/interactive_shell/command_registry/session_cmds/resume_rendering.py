"""Presentation for /resume: render a resumed session's prior activity.

Pure rendering — takes a console plus already-loaded session data and prints it
in REPL turn order. Holds no lookup or orchestration logic so the resume command
module stays focused on the resume flow.
"""

from __future__ import annotations

from collections import deque

from rich.console import Console
from rich.markup import escape

from surfaces.interactive_shell.ui import DIM, HIGHLIGHT

_HISTORY_DISPLAY_CHAT_KINDS: frozenset[str] = frozenset(
    {"chat", "cli_agent", "follow_up", "alert", "incoming_alert"}
)


def _response_for_prompt(turn_details: list[dict], prompt: str) -> str:
    for detail in turn_details:
        if detail.get("prompt") == prompt:
            return str(detail.get("response") or "")
    return ""


def render_resumed_session_history(
    console: Console,
    *,
    history: list[dict],
    turn_details: list[dict],
    messages: list[tuple[str, str]],
) -> None:
    """Render prior session activity in REPL turn order, including slash commands."""
    from surfaces.interactive_shell.ui.streaming.renderer import render_reply_block

    if not history and not messages:
        return

    console.print(f"[{DIM}]─── conversation history ─────────────────────────────────[/]")

    if history:
        assistant_by_user: dict[str, deque[str]] = {}
        pending_user: str | None = None
        for role, text in messages:
            if role == "user":
                pending_user = text
            elif role == "assistant" and pending_user is not None:
                assistant_by_user.setdefault(pending_user, deque()).append(text)
                pending_user = None

        for rec in history:
            kind = rec.get("kind", "")
            text = rec.get("text") or ""
            if kind == "slash":
                console.print(f"[bold]$ {escape(text)}[/bold]")
                continue
            if kind not in _HISTORY_DISPLAY_CHAT_KINDS or not text:
                continue
            console.print(f"[bold {HIGHLIGHT}]❯[/] {escape(text)}")
            response = _response_for_prompt(turn_details, text)
            if not response:
                queued = assistant_by_user.get(text)
                response = queued.popleft() if queued else ""
            if response:
                render_reply_block(console, response)
        console.print(f"[{DIM}]─────────────────────────────────────────────────────────[/]")
        return

    has_pending_user = False
    for role, text in messages:
        if role == "user":
            console.print(f"[bold {HIGHLIGHT}]❯[/] {escape(text)}")
            has_pending_user = True
        elif role == "assistant" and has_pending_user:
            render_reply_block(console, text)
            has_pending_user = False
    console.print(f"[{DIM}]─────────────────────────────────────────────────────────[/]")


__all__ = ["render_resumed_session_history"]
