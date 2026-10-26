"""Summary files beside the memories: per-session summaries and ``memory_summary.md``.

Session summaries (``sessions/<session_id>.md``) record what each session tried
and how it ended. They are never put in a prompt directly; consolidation reads
the recent ones to write ``memory_summary.md``, which is read at the top of the
prompt index until the next consolidation replaces it.

Forgetting a memory must not let a summary bring it back, so once a memory is
forgotten no session that began before that moment records a summary, and
summaries recorded up to it are no longer read.
"""

from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from filelock import Timeout

from core.domain.memory.consolidation_state import read_consolidation_state
from core.domain.memory.files import (
    SESSIONS_DIRNAME,
    SUMMARY_FILENAME,
    ensure_private_subdir,
    memory_dir,
    memory_lock,
    write_text_atomically,
)
from core.domain.memory.models import TRUNCATION_MARKER

logger = logging.getLogger(__name__)

MAX_MEMORY_SUMMARY_CHARS = 2_500
MAX_SESSION_SUMMARY_CHARS = 600
MAX_SESSION_SUMMARY_FILES = 30
#: Each extraction pass appends one entry; older entries of a long session are dropped.
MAX_ENTRIES_PER_SESSION_FILE = 10
SESSION_OUTCOMES: tuple[str, ...] = ("success", "partial", "fail", "uncertain")
_DEFAULT_OUTCOME = "uncertain"

# Session ids become file names; anything else is skipped rather than escaped.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ENTRY_PREFIX = "## "
_OUTCOME_SEPARATOR = " · outcome: "


@dataclass(frozen=True)
class SessionSummary:
    """The newest summary recorded for one session."""

    session_id: str
    recorded_at: str
    outcome: str
    text: str


def _cap(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - len(TRUNCATION_MARKER)].rstrip() + TRUNCATION_MARKER


def read_memory_summary(directory: Path | None = None) -> str:
    """``memory_summary.md`` capped for the prompt; ``""`` when absent."""
    path = (directory or memory_dir()) / SUMMARY_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return _cap(text, MAX_MEMORY_SUMMARY_CHARS)


def write_memory_summary_unlocked(directory: Path, text: str) -> None:
    """Replace ``memory_summary.md``; the caller holds the memory lock. May raise ``OSError``."""
    write_text_atomically(directory / SUMMARY_FILENAME, _cap(text, MAX_MEMORY_SUMMARY_CHARS) + "\n")


def remove_memory_summary_unlocked(directory: Path) -> None:
    """Drop ``memory_summary.md`` so a forgotten fact cannot outlive its memory."""
    with contextlib.suppress(FileNotFoundError):
        (directory / SUMMARY_FILENAME).unlink()


def _sessions_dir(directory: Path) -> Path:
    return directory / SESSIONS_DIRNAME


def _split_entries(text: str) -> list[str]:
    """Entry blocks of a session summary file, oldest first (each starts with ``## ``)."""
    entries: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith(_ENTRY_PREFIX):
            entries.append([line])
        elif entries:
            entries[-1].append(line)
    return ["\n".join(lines).strip() for lines in entries]


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _newest_session_files(sessions: Path) -> list[Path]:
    return sorted(sessions.glob("*.md"), key=_mtime, reverse=True)


def _prune_session_files(sessions: Path) -> None:
    files = _newest_session_files(sessions)
    for stale in files[MAX_SESSION_SUMMARY_FILES:]:
        with contextlib.suppress(OSError):
            stale.unlink()


def _forgotten_since(directory: Path, session_started: datetime | None) -> bool:
    """Whether a memory was forgotten after a session that began at ``session_started``.

    An unknown start counts as before any forget.
    """
    forgotten = read_consolidation_state(directory).forgotten_at
    if forgotten is None:
        return False
    if session_started is None:
        return True
    if session_started.tzinfo is None:
        session_started = session_started.replace(tzinfo=UTC)
    return session_started <= forgotten


def append_session_summary(
    session_id: str,
    text: str,
    *,
    outcome: str = _DEFAULT_OUTCOME,
    session_started: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """Append one summary entry for ``session_id``; ``False`` when nothing was written.

    ``session_started`` is when the summarized session began (unknown when
    ``None``). Nothing is written once a memory was forgotten after that: the
    session may have stated the forgotten fact. Keeps the newest
    :data:`MAX_ENTRIES_PER_SESSION_FILE` entries per session and the newest
    :data:`MAX_SESSION_SUMMARY_FILES` session files. Never raises.
    """
    summary = _cap(" ".join(text.split()), MAX_SESSION_SUMMARY_CHARS)
    if not summary or not _SESSION_ID_RE.fullmatch(session_id):
        return False
    label = outcome if outcome in SESSION_OUTCOMES else _DEFAULT_OUTCOME
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    entry = f"{_ENTRY_PREFIX}{stamp}{_OUTCOME_SEPARATOR}{label}\n{summary}"
    try:
        with memory_lock():
            if _forgotten_since(memory_dir(), session_started):
                return False
            sessions = ensure_private_subdir(SESSIONS_DIRNAME)
            path = sessions / f"{session_id}.md"
            try:
                existing = _split_entries(path.read_text(encoding="utf-8"))
            except OSError:
                existing = []
            kept = [*existing, entry][-MAX_ENTRIES_PER_SESSION_FILE:]
            write_text_atomically(path, f"# Session {session_id}\n\n" + "\n\n".join(kept) + "\n")
            _prune_session_files(sessions)
        return True
    except (Timeout, OSError):
        logger.debug("[memory] could not record the session summary", exc_info=True)
        return False


def _parse_entry(session_id: str, entry: str) -> SessionSummary | None:
    header, _, body = entry.partition("\n")
    stamp, _, outcome = header[len(_ENTRY_PREFIX) :].partition(_OUTCOME_SEPARATOR)
    if not body.strip():
        return None
    return SessionSummary(
        session_id=session_id,
        recorded_at=stamp.strip(),
        outcome=outcome.strip() or _DEFAULT_OUTCOME,
        text=body.strip(),
    )


def _recorded_after(summary: SessionSummary, moment: datetime) -> bool:
    try:
        recorded = datetime.fromisoformat(summary.recorded_at)
    except ValueError:
        return False
    if recorded.tzinfo is None:
        recorded = recorded.replace(tzinfo=UTC)
    return recorded > moment


def recent_session_summaries(
    limit: int = 10,
    *,
    directory: Path | None = None,
    after: datetime | None = None,
) -> list[SessionSummary]:
    """The newest entry of up to ``limit`` session files, most recently written first.

    With ``after``, a session whose newest entry was recorded at or before it
    is skipped.
    """
    sessions = _sessions_dir(directory or memory_dir())
    if not sessions.is_dir():
        return []
    summaries: list[SessionSummary] = []
    for path in _newest_session_files(sessions):
        if len(summaries) >= limit:
            break
        try:
            entries = _split_entries(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        parsed = _parse_entry(path.stem, entries[-1]) if entries else None
        if parsed is not None and (after is None or _recorded_after(parsed, after)):
            summaries.append(parsed)
    return summaries


__all__ = [
    "MAX_MEMORY_SUMMARY_CHARS",
    "MAX_SESSION_SUMMARY_CHARS",
    "MAX_SESSION_SUMMARY_FILES",
    "SESSION_OUTCOMES",
    "SessionSummary",
    "append_session_summary",
    "read_memory_summary",
    "recent_session_summaries",
    "remove_memory_summary_unlocked",
    "write_memory_summary_unlocked",
]
