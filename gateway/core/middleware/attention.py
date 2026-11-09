"""Attention gate for un-tagged replies in chat threads the bot is active in.

Thread following: an @mention opens an attention window on that thread; while
the window is open, plain replies can reach the agent without another mention.
Every check here is deterministic and free — no model call ever decides whether
to speak.

Transport-neutral. Slack and Discord share the ``<@id>`` mention form, so the
same gate serves both.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

# A mention keeps the bot listening to the thread this long; each engaged
# turn refreshes it. After expiry a fresh @mention is required.
ATTENTION_WINDOW_SECONDS = 30 * 60.0
# Unprompted replies are rate-limited so the bot never dominates a thread.
MAX_UNPROMPTED_REPLIES = 2
RATE_WINDOW_SECONDS = 10 * 60.0
# Threads tracked before expired entries are pruned (memory bound).
_MAX_TRACKED_THREADS = 1024

_LEADING_USER_MENTION = re.compile(r"^\s*<@(?P<user>[^>]+)>")
# Names people use to address the bot in prose without a real @mention.
_BOT_NAME_HINT = re.compile(r"(?i)\bopen\s?sre\b")
_AFFIRMATIVES = frozenset(
    {
        "yes",
        "y",
        "yeah",
        "yep",
        "yup",
        "sure",
        "ok",
        "okay",
        "please",
        "go ahead",
        "do it",
        "do that",
    }
)


class GateDecision(Enum):
    """Outcome for one un-tagged thread reply."""

    ENGAGE = "engage"  # run a full agent turn
    PASS = "pass"  # human-to-human traffic: stay silent
    RATE_LIMITED = "rate_limited"  # addressed to the bot, but over the unprompted budget


@dataclass
class _ThreadAttention:
    expires_at: float
    # Humans who have spoken in this thread while the window was open. While
    # only one human has spoken, the thread is a 1:1 conversation with the bot
    # and every reply is treated as addressed to it (DM-like trust).
    speakers: set[str] = field(default_factory=set)
    unprompted_at: list[float] = field(default_factory=list)


def is_addressed_to_bot(text: str, *, bot_user_id: str) -> bool:
    """Deterministic check: is this un-tagged reply aimed at the bot?

    Kept intentionally coarse (affirmatives, bot name, questions). A message
    that opens by mentioning another human is never for the bot. False
    negatives cost a re-mention; false positives are capped by the rate limit.
    """
    bare = text.strip()
    lead = _LEADING_USER_MENTION.match(bare)
    if lead:
        # A leading mention names the addressee outright.
        return lead.group("user") == bot_user_id
    lower = bare.lower().rstrip(".!")
    if lower in _AFFIRMATIVES:
        return True
    if _BOT_NAME_HINT.search(bare):
        return True
    return "?" in bare


class ThreadAttentionGate:
    """Track per-thread attention windows and admit un-tagged replies."""

    def __init__(
        self,
        *,
        window_seconds: float = ATTENTION_WINDOW_SECONDS,
        max_unprompted_replies: int = MAX_UNPROMPTED_REPLIES,
        rate_window_seconds: float = RATE_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window_seconds = window_seconds
        self._max_unprompted = max_unprompted_replies
        self._rate_window = rate_window_seconds
        self._clock = clock
        self._threads: dict[str, _ThreadAttention] = {}
        self._lock = threading.Lock()

    def note_addressed_turn(self, conversation_key: str, *, user_id: str = "") -> None:
        """A mention/DM turn ran: open (or refresh) the thread's window."""
        now = self._clock()
        with self._lock:
            self._prune(now)
            entry = self._threads.get(conversation_key)
            if entry is None:
                entry = self._threads[conversation_key] = _ThreadAttention(expires_at=0.0)
            if user_id:
                entry.speakers.add(user_id)
            entry.expires_at = now + self._window_seconds

    def decide(
        self, *, conversation_key: str, text: str, user_id: str, bot_user_id: str
    ) -> GateDecision:
        """Gate one un-tagged reply from ``user_id`` in ``conversation_key``."""
        now = self._clock()
        with self._lock:
            entry = self._threads.get(conversation_key)
            if entry is None or entry.expires_at <= now:
                return GateDecision.PASS
            # A leading mention names the addressee outright — if it's another
            # human, the reply is theirs even in a 1:1 thread with the bot.
            lead = _LEADING_USER_MENTION.match(text.strip())
            if lead and lead.group("user") != bot_user_id:
                entry.speakers.add(user_id)
                return GateDecision.PASS
            solo = entry.speakers <= {user_id}
            entry.speakers.add(user_id)
            if solo:
                # 1:1 conversation with the bot: every reply is for it, like a
                # DM. No address heuristics, no unprompted budget.
                entry.expires_at = now + self._window_seconds
                return GateDecision.ENGAGE
            if not is_addressed_to_bot(text, bot_user_id=bot_user_id):
                return GateDecision.PASS
            entry.unprompted_at = [t for t in entry.unprompted_at if now - t < self._rate_window]
            if len(entry.unprompted_at) >= self._max_unprompted:
                return GateDecision.RATE_LIMITED
            entry.unprompted_at.append(now)
            # Engaging counts as conversation: keep listening.
            entry.expires_at = now + self._window_seconds
            return GateDecision.ENGAGE

    def _prune(self, now: float) -> None:
        if len(self._threads) < _MAX_TRACKED_THREADS:
            return
        self._threads = {
            key: entry for key, entry in self._threads.items() if entry.expires_at > now
        }
