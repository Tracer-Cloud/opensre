"""Condense a session into the digest memory extraction reads.

The source is the session's JSONL log: user and assistant ``message`` records
plus the ``tool_call`` / ``tool_result`` pairs of every tool the agent ran, so
facts shown by tool output can be remembered with provenance. A turn's tool
records are written while it runs and its messages when it ends, so tool
records belong to the user message that follows them.

A pass covers the session through the newest turn recorded when it was
queued. That turn's messages may not be logged yet, so it comes from the
in-memory transcript when the log lacks it; anything logged after it belongs
to a later pass. Sessions without a log (in-memory hosts, tests) use the
transcript text.

Demo turns are dropped, and so are summaries of earlier turns (compaction
records and session-summary messages): a summary blends many turns, so the
demo fence cannot vouch for it, and the log keeps the turns it summarizes.
Turns ``/new`` carried in from the previous session are dropped too: that
session's own passes, with its demo fence, already covered them.
Every piece is passed through the memory redactor, each tool result is
capped, and when the whole digest is over budget the newest turns are kept.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.agent_harness.session.memory_turns import DemoTurns, normalize_user_text
from core.agent_harness.session.persistence.contracts import CARRIED_MESSAGE_METADATA_KEY
from core.agent_harness.session.persistence.paths import session_path
from core.domain.memory import EvidenceCorpus, redact_memory_unsafe_text
from core.state.transcript_window import is_summary_message

MAX_DIGEST_CHARS = 60_000
MAX_TOOL_RESULT_CHARS = 1_500
MAX_TOOL_ARGUMENTS_CHARS = 300
MAX_MESSAGE_CHARS = 4_000

#: Tools whose calls record or read plans, skills and memory rather than the user's systems.
_BOOKKEEPING_TOOLS = frozenset(
    {
        "ask_user_choice",
        "memory_forget",
        "memory_recall",
        "memory_remember",
        "session_goal_complete",
        "session_goal_set",
        "skill_view",
        "update_plan",
    }
)
_TRACE_SPAN_MARKER = b'"trace_span"'
_ELLIPSIS = "…"


@dataclass
class _Turn:
    """One exchange: the user's message, the tools that ran for it, and the reply."""

    user_text: str = ""
    turn_id: str | None = None
    assistant_text: str = ""
    tools: list[str] = field(default_factory=list)
    #: Copied in by ``/new`` from the session it rotated out of.
    carried: bool = False


@dataclass(frozen=True)
class SessionDigest:
    """The extraction input for one session."""

    text: str
    #: Complete turns (a user message plus a reply or tool calls) in the digest.
    turns: int
    demo_turns_dropped: int
    #: When the session began, from its log; ``None`` when the log does not say.
    started_at: datetime | None = None
    #: The user's messages and the tool lines exactly as ``text`` shows them,
    #: which the provenance an extraction claims is checked against.
    read: EvidenceCorpus = EvidenceCorpus()

    @property
    def empty(self) -> bool:
        """True when nothing outside demo runs is left to extract from."""
        return self.turns == 0


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + _ELLIPSIS if limit > 0 else ""


def _safe(text: str, limit: int, *, flat: bool = False) -> str:
    """Redact secrets, then cap: capping first could cut a secret below its detector's length."""
    redacted = redact_memory_unsafe_text(text)
    return _clip(" ".join(redacted.split()) if flat else redacted, limit)


def _tool_line(tool: str, arguments: Any, ok: Any, result: Any) -> str:
    try:
        args_text = json.dumps(arguments, ensure_ascii=False, default=str, sort_keys=True)
    except (TypeError, ValueError):
        args_text = str(arguments)
    status = "error" if ok is False else "ok"
    result_text = result if isinstance(result, str) else json.dumps(result, default=str)
    return (
        f"TOOL {tool} {_safe(args_text, MAX_TOOL_ARGUMENTS_CHARS, flat=True)} → {status}: "
        f"{_safe(result_text, MAX_TOOL_RESULT_CHARS, flat=True)}"
    )


def _load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_bytes().splitlines():
        if not line.strip() or _TRACE_SPAN_MARKER in line:
            continue
        with contextlib.suppress(ValueError):
            record = json.loads(line.decode("utf-8"))
            if isinstance(record, dict):
                records.append(record)
    return records


def _parse_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def _session_started(records: Sequence[dict[str, Any]]) -> datetime | None:
    """The log header's creation time, else its earliest entry's; ``None`` when neither parses."""
    header = next((record for record in records if record.get("type") == "session"), None)
    if header is not None and (created := _parse_stamp(header.get("created_at"))) is not None:
        return created
    stamps = [stamp for record in records if (stamp := _parse_stamp(record.get("timestamp")))]
    return min(stamps, default=None)


def _turns_from_log(records: Sequence[dict[str, Any]]) -> list[_Turn]:
    turns: list[_Turn] = []
    pending_tools: list[str] = []
    calls: dict[str, tuple[str, Any]] = {}
    open_turn: _Turn | None = None
    for record in records:
        kind = record.get("type")
        if kind == "tool_call":
            calls[str(record.get("id"))] = (str(record.get("tool") or ""), record.get("arguments"))
        elif kind == "tool_result":
            tool, arguments = calls.pop(
                str(record.get("parent_id")), (str(record.get("tool") or ""), None)
            )
            if tool and tool not in _BOOKKEEPING_TOOLS:
                pending_tools.append(
                    _tool_line(tool, arguments, record.get("ok"), record.get("content", ""))
                )
        elif kind == "message":
            role = record.get("role")
            content = str(record.get("content") or "")
            metadata = record.get("metadata")
            turn_id = metadata.get("turn_id") if isinstance(metadata, dict) else None
            carried = (
                isinstance(metadata, dict) and metadata.get(CARRIED_MESSAGE_METADATA_KEY) is True
            )
            if is_summary_message((str(role), content)):
                continue
            if role == "user":
                if open_turn is not None:
                    turns.append(open_turn)
                open_turn = _Turn(
                    user_text=content,
                    turn_id=turn_id if isinstance(turn_id, str) else None,
                    tools=pending_tools,
                    carried=carried,
                )
                pending_tools = []
            elif role == "assistant":
                if open_turn is None:
                    open_turn = _Turn(tools=pending_tools, carried=carried)
                    pending_tools = []
                open_turn.assistant_text = content
                turns.append(open_turn)
                open_turn = None
    if open_turn is not None:
        turns.append(open_turn)
    if pending_tools:
        # Tools of the turn being recorded right now; its messages land after it ends.
        turns.append(_Turn(tools=pending_tools))
    return turns


def _turns_from_transcript(messages: Sequence[tuple[str, str]]) -> list[_Turn]:
    turns: list[_Turn] = []
    for role, text in messages:
        if is_summary_message((role, text)):
            continue
        if role == "user":
            turns.append(_Turn(user_text=text))
        elif role == "assistant":
            if not turns or turns[-1].assistant_text:
                turns.append(_Turn())
            turns[-1].assistant_text = text
    return turns


def _newest_turn(transcript: Sequence[tuple[str, str]], demo: DemoTurns) -> _Turn | None:
    """The transcript's last exchange, the newest recorded turn; ``None`` without one."""
    turns = _turns_from_transcript(transcript)
    if not turns or not turns[-1].user_text:
        return None
    newest = turns[-1]
    newest.turn_id = demo.latest_turn_id_for(newest.user_text)
    return newest


def _exchange(turn: _Turn) -> tuple[str, str]:
    """A turn's request and reply, whitespace-insensitive."""
    return normalize_user_text(turn.user_text), normalize_user_text(turn.assistant_text)


def _same_turn(logged: _Turn, newest: _Turn) -> bool:
    """Whether ``logged`` is ``newest``: by prompt turn id when known, else by its text."""
    if newest.turn_id:
        return logged.turn_id == newest.turn_id
    return _exchange(logged) == _exchange(newest)


def _in_flight(turn: _Turn) -> bool:
    """A turn whose tools are logged but whose messages are not, because it is still ending."""
    return not (turn.user_text or turn.turn_id or turn.assistant_text)


def _through_newest(logged: list[_Turn], newest: _Turn | None) -> list[_Turn]:
    """The logged turns up to and including ``newest``, taking it from the transcript if absent.

    Anything logged after ``newest`` belongs to a turn recorded after this
    pass was queued, which the demo fence of this pass knows nothing about.
    When ``newest`` is not logged yet, the tools logged while it ran are the
    trailing in-flight turn.
    """
    if newest is None:
        return logged
    for index in range(len(logged) - 1, -1, -1):
        if _same_turn(logged[index], newest):
            return logged[: index + 1]
    if logged and _in_flight(logged[-1]):
        newest.tools = logged[-1].tools
        logged = logged[:-1]
    return [*logged, newest]


def _is_demo(turn: _Turn, demo: DemoTurns, *, newest: bool) -> bool:
    if not turn.user_text and not turn.turn_id:
        # Only the in-flight turn has tools and no message yet.
        return newest and demo.latest_is_demo
    return demo.covers(turn_id=turn.turn_id, user_text=turn.user_text)


_OMITTED_NOTE = "({count} earlier tool calls omitted)"
#: Room kept for the omitted-calls note when a turn's tools do not all fit.
_OMITTED_NOTE_RESERVE = 40


@dataclass(frozen=True)
class _Rendered:
    """One turn as the model reads it, with its user message and tool lines kept apart."""

    text: str
    user: str
    tools: tuple[str, ...]


def _render(turn: _Turn, budget: int, *, clip: bool) -> _Rendered | None:
    """One turn as text within ``budget``; ``None`` when it cannot fit and ``clip`` is off.

    Tool lines go first when space runs out, oldest first, since the last
    calls of a turn usually show what finally worked. With ``clip`` the
    request and the reply themselves are cut to fit; a piece cut short is not
    kept as evidence.
    """
    user = _safe(turn.user_text, MAX_MESSAGE_CHARS)
    head = f"USER: {user}" if user else ""
    tail = (
        f"ASSISTANT: {_safe(turn.assistant_text, MAX_MESSAGE_CHARS)}" if turn.assistant_text else ""
    )
    fixed = sum(len(line) + 1 for line in (head, tail) if line)
    tools = list(turn.tools)
    note = ""
    if fixed + sum(len(line) + 1 for line in tools) > budget:
        kept: list[str] = []
        used = fixed + _OMITTED_NOTE_RESERVE
        for line in reversed(tools):
            if used + len(line) + 1 > budget:
                break
            kept.append(line)
            used += len(line) + 1
        kept.reverse()
        note = _OMITTED_NOTE.format(count=len(tools) - len(kept))
        tools = kept
    text = "\n".join(line for line in (head, note, *tools, tail) if line)
    if len(text) <= budget:
        return _Rendered(text=text, user=user, tools=tuple(tools))
    if not clip:
        return None
    text = _clip(text, budget)
    return _Rendered(
        text=text,
        user=user if head and head in text else "",
        tools=tuple(line for line in tools if line in text),
    )


def _assemble(turns: list[_Turn], demo: DemoTurns, max_chars: int) -> SessionDigest:
    kept: list[_Turn] = []
    dropped = 0
    for index, turn in enumerate(turns):
        if _is_demo(turn, demo, newest=index == len(turns) - 1):
            dropped += 1
        else:
            kept.append(turn)
    rendered: list[_Rendered] = []
    remaining = max_chars
    counted = 0
    for turn in reversed(kept):
        # Only the newest turn may be cut mid-message; an older turn that does
        # not fit ends the digest rather than adding a fragment.
        piece = _render(turn, remaining, clip=not rendered)
        if piece is None or not piece.text:
            break
        rendered.append(piece)
        remaining -= len(piece.text) + 2
        counted += 1 if turn.user_text and (turn.assistant_text or turn.tools) else 0
        if remaining <= 0:
            break
    rendered.reverse()
    return SessionDigest(
        text="\n\n".join(piece.text for piece in rendered),
        turns=counted,
        demo_turns_dropped=dropped,
        read=EvidenceCorpus(
            user_messages=tuple(piece.user for piece in rendered if piece.user),
            tool_lines=tuple(line for piece in rendered for line in piece.tools),
        ),
    )


def build_session_digest(
    session_id: str,
    *,
    demo: DemoTurns,
    transcript: Sequence[tuple[str, str]] = (),
    max_chars: int = MAX_DIGEST_CHARS,
) -> SessionDigest:
    """The digest of ``session_id`` through its newest recorded turn.

    ``transcript`` is the in-memory transcript when the pass was queued; its
    last exchange is the newest recorded turn, which ``demo`` names. The log
    wins when it holds at least one user message, even if every one of them
    was a demo turn; otherwise the transcript is used. Never raises on a
    malformed or missing log.
    """
    if session_id:
        records: list[dict[str, Any]] = []
        with contextlib.suppress(OSError):
            records = _load_records(session_path(session_id))
        logged = _turns_from_log(records)
        if any(turn.user_text for turn in logged):
            newest = _newest_turn(transcript, demo)
            # Matched against carried turns too, so a transcript that still ends
            # with a carried exchange never brings it back as this session's turn.
            own = [turn for turn in _through_newest(logged, newest) if not turn.carried]
            digest = _assemble(own, demo, max_chars)
            return replace(digest, started_at=_session_started(records))
    return _assemble(_turns_from_transcript(transcript), demo, max_chars)


__all__ = [
    "MAX_DIGEST_CHARS",
    "MAX_MESSAGE_CHARS",
    "MAX_TOOL_ARGUMENTS_CHARS",
    "MAX_TOOL_RESULT_CHARS",
    "SessionDigest",
    "build_session_digest",
]
