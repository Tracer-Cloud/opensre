"""Session list data and adapter for the shared resume picker."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from surfaces.interactive_shell.ui.resume_picker import ResumeMenuItem, choose_resume_session


@dataclass(frozen=True)
class SessionMenuItem:
    session_id: str
    title: str
    started: str
    duration: str
    turns: str
    is_current: bool = False
    started_full: str = ""
    activity_at: str | datetime | int | float | None = None


def choose_recent_session(items: Sequence[SessionMenuItem]) -> str | None:
    """Browse recent sessions using the same full-width UI as /resume."""
    return choose_resume_session(
        [
            ResumeMenuItem(
                session_id=item.session_id,
                title=item.title,
                activity_at=item.activity_at,
                is_current=item.is_current,
                detail=(
                    f"{item.session_id[:8]}  ·  {item.started_full or item.started}  ·  "
                    f"{item.duration}  ·  {item.turns} turns"
                ),
            )
            for item in items
        ],
        heading=f"Sessions  ·  {len(items)} recent",
    )
