"""Seed gateway session history from the live Slack thread when needed.

Session JSONL can be empty across redeploys / ephemeral disks / new bindings
while the Slack thread still holds the prior assistant ``Want me to:`` offer.
Seeding from ``conversations.replies`` makes follow-ups like ``yes`` resolve.
"""

from __future__ import annotations

import logging
import re

from core.agent_harness import SessionCore
from gateway.core.session import seed_session_history
from integrations.slack import (
    fetch_channel_messages,
    resolve_bot_token,
)

logger = logging.getLogger(__name__)

_ASSISTANT_SHAPE_RE = re.compile(
    r"(?i)(?:\*\*)?I found:(?:\*\*)?|(?:\*\*)?Want me to:(?:\*\*)?|"
    r"(?:\*\*)?Here's what that looks like:(?:\*\*)?"
)
_THREAD_SEED_LIMIT = 40


def session_needs_thread_seed(
    user_text: str, *, is_reply: bool = False, has_session_history: bool = False
) -> bool:
    """True when follow-up resolution needs a one-time Slack thread fetch.

    Seed only when the gateway session is empty (redeploy / ephemeral disk /
    new binding). Once the session already holds prior turns, keep using it —
    re-fetching ``conversations.replies`` on every reply re-runs the agent
    against a freshly rebuilt history and burns Slack API quota for no gain.

    When the session *is* empty, any threaded reply (or a bare affirmative)
    seeds from the live thread so follow-ups like ``yes`` / ``do that`` still
    resolve after a restart. A brand-new top-level mention needs no seed.
    """
    if has_session_history:
        return False
    if is_reply:
        return True
    bare = str(user_text or "").strip()
    if not bare:
        return False
    lower = bare.lower()
    if lower in {"yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "please"}:
        return True
    return "want me to" in lower and re.search(r"\byes\b", lower) is not None


def messages_from_slack_thread(
    *,
    channel_id: str,
    thread_ts: str,
    exclude_ts: str = "",
    bot_user_id: str = "",
) -> list[tuple[str, str]]:
    """Fetch thread replies and map them to ``(role, content)`` pairs."""
    target, err = resolve_bot_token()
    if target is None:
        logger.debug("slack thread seed skipped: %s", err)
        return []
    raw, fetch_err = fetch_channel_messages(
        target,
        channel_id=channel_id,
        limit=_THREAD_SEED_LIMIT,
        thread_ts=thread_ts,
    )
    if raw is None:
        logger.debug("slack thread seed fetch failed: %s", fetch_err)
        return []

    skip = str(exclude_ts or "").strip()
    bot = str(bot_user_id or "").strip()
    out: list[tuple[str, str]] = []
    for item in raw:
        ts = str(item.get("ts") or "")
        if skip and ts == skip:
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        user = str(item.get("user") or "")
        role = "assistant" if _is_assistant_message(text, user=user, bot_user_id=bot) else "user"
        if role == "user" and user:
            # Attribute the speaker: multi-user threads collapse into one
            # anonymous "user" otherwise, so the agent cannot tell who said
            # what ("call me X" then someone else asks "what is my name?").
            text = f"<@{user}>: {text}"
        out.append((role, text))
    return out


def seed_session_from_slack_thread(
    session: SessionCore,
    *,
    channel_id: str,
    thread_ts: str,
    exclude_ts: str = "",
    bot_user_id: str = "",
) -> int:
    """Replace empty/incomplete session transcript with Slack thread turns.

    Returns the number of messages seeded.
    """
    seeded = messages_from_slack_thread(
        channel_id=channel_id,
        thread_ts=thread_ts,
        exclude_ts=exclude_ts,
        bot_user_id=bot_user_id,
    )
    # Never clobber an in-memory transcript the agent already built this
    # process — callers gate on an empty session via session_needs_thread_seed.
    return seed_session_history(session, seeded, replace_existing=False)


def _is_assistant_message(text: str, *, user: str, bot_user_id: str) -> bool:
    if bot_user_id and user == bot_user_id:
        return True
    return bool(_ASSISTANT_SHAPE_RE.search(text))
