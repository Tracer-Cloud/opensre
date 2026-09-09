"""Extract durable facts into long-term memory during and after sessions.

One best-effort LLM pass over a chat transcript. Callers schedule it via
:func:`schedule_memory_extraction` after every recorded turn and again on
session close / rotation. Mid-session runs coalesce onto a single daemon worker
so rapid turns do not pile up provider calls. Process-exit close waits for
extraction to finish (interruptible by Ctrl+C) so durable facts always persist.
Never raises out: any failure (LLM unavailable, malformed output, disk errors)
is logged and ignored. Environment gates can disable the whole feature or only
the extraction pass.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
from typing import Any, Protocol

from core.domain.memory import (
    MEMORY_TYPES,
    MemoryType,
    auto_extract_enabled,
    ensure_memory_store,
    find_memory_safety_issues,
    redact_memory_unsafe_text,
    render_prompt_index,
    save_memory,
)

logger = logging.getLogger(__name__)

MIN_CHAT_MESSAGES = 2
MAX_MEMORIES_PER_SESSION = 5
# Poll interval while waiting for close-path extraction. Short enough that
# Ctrl+C stays responsive; the join itself runs until the worker finishes so
# durable facts are never abandoned on a slow provider.
_CLOSE_EXTRACTION_POLL_SECONDS = 0.25
_MAX_TRANSCRIPT_TURNS = 30

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL)
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{2,}")
_SAMPLE_OR_SYNTHETIC_RE = re.compile(
    r"(?i)\b(?:sample|demo|synthetic|test)\s+"
    r"(?:alert|investigation|scenario|template|suite|run|rca)\b|"
    r"\bcloudopsbench\b|"
    r"\b(?:sample|template):[a-z0-9_-]+\b"
)
_EXPLICIT_MEMORY_RE = re.compile(r"(?i)\b(?:remember|save|store|keep|note|memorize)\b")
_USER_GROUNDED_TYPES = frozenset({"infrastructure", "repository", "investigation_learning"})
_GROUNDING_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "alert",
        "alerts",
        "because",
        "caused",
        "cluster",
        "could",
        "deploy",
        "deployment",
        "details",
        "error",
        "errors",
        "fact",
        "facts",
        "failure",
        "failures",
        "future",
        "incident",
        "investigation",
        "issue",
        "known",
        "lesson",
        "lessons",
        "memory",
        "notes",
        "outage",
        "prod",
        "production",
        "root",
        "service",
        "stable",
        "that",
        "this",
        "uses",
        "with",
    }
)

_EXTRACTION_PROMPT = """\
You maintain the long-term memory of an SRE assistant. Below is the transcript
of one terminal session and the memories already stored.

Decide what is useful to keep for future sessions. The user will NOT say
"remember" — extract durable facts from ordinary conversation whenever they
will help later turns.

Extract ONLY durable knowledge worth keeping across future sessions:
- who the user is (name, role, how they like to work)
- infrastructure facts and conventions (cluster names, naming schemes, known-flaky services)
- repository facts (owner/name, purpose, default branch, conventions); keep a separate
  stable memory for each repository instead of replacing the previously active repo
- stable preferences the user stated
- lessons learned from real incidents or investigations the user confirmed

Do NOT extract: transient session state, one-off command outputs, secrets,
credentials, scheduled loops or tasks created in this session, demo or
onboarding outcomes, the status of work just done (the shell already shows
it and it goes stale), or anything already captured by an existing memory
(unless it needs updating — then reuse the existing name exactly).

User-authored statements are the source of truth. Do NOT store infrastructure
facts or investigation learnings that appear only in assistant/tool output.
Never store details from built-in sample, demo, synthetic, test alerts, benchmark
scenarios, or generated RCA examples as the user's infrastructure or incident
history unless the user explicitly says they are real and asks to remember them.

Return a JSON array (no prose). Each item:
{{"name": "kebab-case-slug", "type": "user|infrastructure|repository|preference|investigation_learning",
  "description": "one line, max 200 chars", "content": "full markdown body"}}

Return [] when nothing qualifies. At most {max_memories} items.

--- Existing memory index ---
{memory_index}

--- Session transcript ---
{transcript}
"""

_worker_lock = threading.Lock()
_pending_messages: list[tuple[str, str]] | None = None
_pending_context: contextvars.Context | None = None
_worker: threading.Thread | None = None


class _ChatSession(Protocol):
    """The slice of :class:`SessionCore` the extractor reads."""

    cli_agent_messages: list[tuple[str, str]]


def schedule_memory_extraction(
    messages: list[tuple[str, str]],
    *,
    wait_for_completion: bool = False,
) -> None:
    """Snapshot ``messages`` and extract memories.

    When ``wait_for_completion`` is true (session ``close`` / process exit), run
    synchronously so durable facts always land before the process ends. Turn and
    rotation paths leave it false: a single daemon worker coalesces the latest
    transcript so inbound handling is not stalled and rapid turns share one LLM
    call.
    """
    if not auto_extract_enabled():
        return
    snapshot = list(messages)
    if len(snapshot) < MIN_CHAT_MESSAGES:
        return
    if wait_for_completion:
        global _pending_messages, _pending_context
        with _worker_lock:
            _pending_messages = None
            _pending_context = None
        # Run extraction off the main thread and wait until it finishes so
        # durable facts always land before process exit. Poll the join so
        # Ctrl+C during shutdown stays interruptible without raising through
        # the network read mid-call.
        ctx = contextvars.copy_context()
        worker = threading.Thread(
            target=ctx.run,
            args=(_extract_memories_safe, snapshot),
            name="opensre-memory-extraction-close",
            daemon=True,
        )
        worker.start()
        try:
            while worker.is_alive():
                worker.join(timeout=_CLOSE_EXTRACTION_POLL_SECONDS)
        except KeyboardInterrupt:
            logger.warning(
                "Memory extraction interrupted during session close; "
                "final transcript facts may be incomplete"
            )
            raise
        return
    _schedule_coalesced(snapshot)


def _schedule_coalesced(snapshot: list[tuple[str, str]]) -> None:
    """Queue ``snapshot`` on the coalescing worker, capturing storage scope.

    Copy the current context so the per-turn storage scope (ContextVar set by
    ``bound_storage_scope``) is inherited: without it ``current_scope()`` is
    None on the worker thread and ``save_memory()`` would resolve to the org
    root instead of ``users/<actor_id>/memory/``, misfiling the user's
    extracted facts where their in-scope turns never read them.
    """
    global _pending_messages, _pending_context, _worker
    ctx = contextvars.copy_context()
    with _worker_lock:
        _pending_messages = snapshot
        _pending_context = ctx
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(
            target=_coalesced_extract_worker,
            name="opensre-memory-extraction",
            daemon=True,
        )
        _worker.start()


def _coalesced_extract_worker() -> None:
    global _pending_messages, _pending_context, _worker
    while True:
        with _worker_lock:
            snapshot = _pending_messages
            ctx = _pending_context
            _pending_messages = None
            _pending_context = None
            if snapshot is None:
                _worker = None
                return
        if ctx is not None:
            ctx.run(_extract_memories_safe, snapshot)
        else:
            _extract_memories_safe(snapshot)


def extract_memories_from_session(session: _ChatSession) -> None:
    """Run one extraction pass synchronously; silent no-op when gated off."""
    messages = list(getattr(session, "cli_agent_messages", []) or [])
    extract_memories_from_messages(messages)


def extract_memories_from_messages(messages: list[tuple[str, str]]) -> None:
    """Run one extraction pass over ``messages``; never raises."""
    _extract_memories_safe(list(messages))


def _extract_memories_safe(messages: list[tuple[str, str]]) -> None:
    try:
        if not auto_extract_enabled():
            return
        if len(messages) < MIN_CHAT_MESSAGES:
            return
        ensure_memory_store()
        response = _invoke_extraction_llm(messages)
        if not response:
            return
        _save_extracted(_parse_extraction(response), messages)
    except Exception:
        logger.debug("[memory] session-end extraction failed", exc_info=True)


def _invoke_extraction_llm(messages: list[tuple[str, str]]) -> str:
    from core.agent_harness.prompts.memory.conversation import format_recent_conversation

    try:
        from core.llm.factory import LLMRole, get_llm

        llm = get_llm(LLMRole.CLASSIFICATION)
    except Exception:
        logger.debug("[memory] extraction LLM unavailable", exc_info=True)
        return ""

    transcript = redact_memory_unsafe_text(
        format_recent_conversation(messages, max_turns=_MAX_TRANSCRIPT_TURNS)
    )
    prompt = _EXTRACTION_PROMPT.format(
        max_memories=MAX_MEMORIES_PER_SESSION,
        memory_index=render_prompt_index() or "(no memories stored yet)",
        transcript=transcript,
    )
    result = llm.invoke(prompt)
    content = getattr(result, "content", result)
    return content if isinstance(content, str) else str(content)


def _parse_extraction(response: str) -> list[dict[str, Any]]:
    """Pull the JSON array out of the LLM response; [] for anything unusable."""
    text = response.strip()
    fenced = _FENCED_JSON_RE.search(text)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            return []
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _save_extracted(items: list[dict[str, Any]], messages: list[tuple[str, str]]) -> None:
    from core.domain.memory import is_valid_slug, slugify

    saved = 0
    for item in items:
        if saved >= MAX_MEMORIES_PER_SESSION:
            break
        name = item.get("name")
        memory_type = item.get("type")
        description = item.get("description")
        content = item.get("content")
        if not isinstance(name, str) or memory_type not in MEMORY_TYPES:
            continue
        if not isinstance(description, str) or not description.strip():
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if not _extracted_item_is_user_grounded(
            memory_type=memory_type,
            name=name,
            description=description,
            content=content,
            messages=messages,
        ):
            logger.debug(
                "[memory] skipped extracted memory %r because it is not grounded "
                "in user-authored durable facts",
                name,
            )
            continue
        issues = find_memory_safety_issues(description, content)
        if issues:
            logger.debug(
                "[memory] skipped extracted memory %r due to safety rules: %s",
                name,
                ",".join(issue.rule for issue in issues),
            )
            continue
        slug = slugify(name)
        if not is_valid_slug(slug):
            continue
        # ``memory_type`` was already validated against MEMORY_TYPES above.
        if save_memory(
            slug=slug,
            memory_type=MemoryType(memory_type),
            description=description,
            body=content,
        ):
            saved += 1
    if saved:
        logger.debug("[memory] memory extraction saved %d memories", saved)


def _extracted_item_is_user_grounded(
    *,
    memory_type: str,
    name: str,
    description: str,
    content: str,
    messages: list[tuple[str, str]],
) -> bool:
    """Reject assistant-invented infra/incident memories from automatic extraction.

    User/profile/preference facts are still mostly LLM-classified, but
    infrastructure and incident learnings are high-impact enough that auto-save
    should only persist them when the user actually supplied the durable fact.
    This prevents sample/demo RCA output from becoming the user's real memory.
    """
    if memory_type not in _USER_GROUNDED_TYPES:
        return True

    user_text = _joined_role_text(messages, role="user")
    if not user_text.strip():
        return False
    full_transcript = "\n".join(text for _role, text in messages)
    explicit_memory_request = _EXPLICIT_MEMORY_RE.search(user_text) is not None
    if _SAMPLE_OR_SYNTHETIC_RE.search(full_transcript) and not explicit_memory_request:
        return False
    if explicit_memory_request:
        return True
    return bool(
        _distinctive_tokens(f"{name}\n{description}\n{content}") & _distinctive_tokens(user_text)
    )


def _joined_role_text(messages: list[tuple[str, str]], *, role: str) -> str:
    return "\n".join(text for message_role, text in messages if message_role == role)


def _distinctive_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in _WORD_RE.findall(text.lower()):
        normalized = token.strip("._-")
        if len(normalized) < 4 or normalized in _GROUNDING_STOPWORDS:
            continue
        tokens.add(normalized)
        if "-" in normalized or "_" in normalized or "." in normalized:
            tokens.update(
                part
                for part in re.split(r"[-_.]+", normalized)
                if len(part) >= 4 and part not in _GROUNDING_STOPWORDS
            )
    return tokens


__all__ = [
    "extract_memories_from_messages",
    "extract_memories_from_session",
    "schedule_memory_extraction",
]
