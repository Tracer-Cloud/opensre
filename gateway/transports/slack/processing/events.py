"""Parse Slack Events API payloads into normalized inbound messages."""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_LEADING_MENTION = re.compile(r"^\s*<@[^>]+>\s*")

_DM_CHANNEL_TYPE = "im"

# Message subtypes that still carry a real user mention/message to answer.
# Everything else with a subtype (edits, joins, channel bookkeeping) is ignored.
_HANDLED_MESSAGE_SUBTYPES = frozenset({"file_share", "thread_broadcast"})
# A bot or workflow mention in an open channel may arrive tagged as a bot message.
_OPEN_CHANNEL_BOT_SUBTYPES = _HANDLED_MESSAGE_SUBTYPES | {"bot_message"}


@dataclass(frozen=True)
class SlackInboundFile:
    """One Slack file attached to an inbound message (metadata only)."""

    id: str
    name: str
    mimetype: str
    size: int
    url_private: str


@dataclass(frozen=True)
class SlackInboundMessage:
    """Normalized inbound Slack mention or DM text (plus optional files)."""

    team_id: str
    user_id: str
    channel_id: str
    ts: str
    thread_ts: str
    text: str
    # True for an @mention or DM (directly addressed); False for an un-tagged
    # reply in a channel thread, which is only answered when the bot is already
    # active in that thread.
    addressed: bool = True
    files: tuple[SlackInboundFile, ...] = ()
    # True for an @mention in a channel listed in SLACK_OPEN_CHANNEL_IDS, where
    # any author (human, bot or workflow) may start a turn.
    open_channel: bool = False
    # Author bot id (B…) of a bot/workflow mention accepted in an open channel.
    bot_id: str = ""

    @property
    def conversation_key(self) -> str:
        """Session binding key: one conversation per Slack thread."""
        return f"{self.team_id}:{self.channel_id}:{self.thread_ts}"


def parse_events_api_payload(
    payload: Mapping[str, Any],
    *,
    open_channel_ids: Collection[str] = (),
) -> SlackInboundMessage | None:
    """Return the inbound message for an ``events_api`` envelope payload.

    Accepts ``app_mention`` events (channels), plain ``message`` events in
    DMs, and un-tagged ``message`` events that are replies inside an existing
    channel thread (``addressed=False`` — the worker's attention gate decides
    whether those run a turn). ``file_share`` / ``thread_broadcast`` subtypes
    that still carry a real message are kept. Returns ``None`` for anything
    else — bot echoes, bookkeeping subtypes (edits, joins), top-level channel
    chatter, and events missing required fields.

    Bot-authored events are dropped unless they are an ``app_mention`` in a
    channel listed in ``open_channel_ids``.
    """
    event = payload.get("event")
    if not isinstance(event, Mapping):
        return None
    subtype = event.get("subtype")
    bot_id = str(event.get("bot_id") or "")
    event_type = event.get("type")
    open_channel = event_type == "app_mention" and str(event.get("channel") or "") in (
        open_channel_ids
    )
    if bot_id:
        accepted = open_channel and (not subtype or subtype in _OPEN_CHANNEL_BOT_SUBTYPES)
        if event_type == "app_mention":
            # Low volume, and the only trace of why a bot mention was (not) answered.
            logger.info(
                "[slack-gateway] bot mention %s bot_id=%s user=%s channel=%s subtype=%s",
                "accepted" if accepted else "ignored (channel not open)",
                bot_id,
                event.get("user") or "",
                event.get("channel") or "",
                subtype or "",
            )
        if not accepted:
            return None
    elif subtype and subtype not in _HANDLED_MESSAGE_SUBTYPES:
        return None

    is_mention = event_type == "app_mention"
    is_dm = event_type == "message" and event.get("channel_type") == _DM_CHANNEL_TYPE
    ts = str(event.get("ts") or "")
    raw_thread_ts = str(event.get("thread_ts") or "")
    # An un-tagged reply inside an existing channel thread; only answered when the
    # bot is already active in that thread (membership is checked by the worker).
    is_thread_followup = event_type == "message" and bool(raw_thread_ts) and raw_thread_ts != ts
    if not (is_mention or is_dm or is_thread_followup):
        return None

    team_id = str(payload.get("team_id") or event.get("team") or "")
    # Some bot posts carry no ``user``; the bot id then stands in as the speaker.
    user_id = str(event.get("user") or bot_id or "")
    channel_id = str(event.get("channel") or "")
    addressed = is_mention or is_dm
    text = str(event.get("text") or "")
    if addressed:
        text = _LEADING_MENTION.sub("", text)
    # An unaddressed reply keeps its leading mention: "<@U2> can you look" is
    # aimed at a human, and the attention gate must be able to see that.
    text = text.strip()
    files = _parse_files(event.get("files"))
    if not (team_id and user_id and channel_id and ts and (text or files)):
        return None

    return SlackInboundMessage(
        team_id=team_id,
        user_id=user_id,
        channel_id=channel_id,
        ts=ts,
        thread_ts=raw_thread_ts or ts,
        text=text,
        addressed=addressed,
        files=files,
        open_channel=open_channel,
        bot_id=bot_id,
    )


def _parse_files(raw_files: Any) -> tuple[SlackInboundFile, ...]:
    """Parse Slack's ``files`` array, keeping only entries with an id."""
    if not isinstance(raw_files, Sequence) or isinstance(raw_files, (str, bytes)):
        return ()
    parsed = (_parse_file(item) for item in raw_files)
    return tuple(file for file in parsed if file is not None)


def _parse_file(item: Any) -> SlackInboundFile | None:
    """Build one inbound file from a raw Slack file dict, or ``None`` if it has no id.

    ``url_private`` is optional — a download can fall back to ``files.info``.
    """
    if not isinstance(item, Mapping):
        return None
    file_id = str(item.get("id") or "").strip()
    if not file_id:
        return None
    return SlackInboundFile(
        id=file_id,
        name=str(item.get("name") or item.get("title") or file_id),
        mimetype=str(item.get("mimetype") or "application/octet-stream"),
        size=_parse_size(item.get("size")),
        url_private=str(item.get("url_private") or item.get("url_private_download") or ""),
    )


def _parse_size(raw_size: Any) -> int:
    """Coerce a raw file size to a non-negative int (0 when missing or invalid)."""
    try:
        size = int(raw_size or 0)
    except (TypeError, ValueError):
        return 0
    return max(size, 0)
