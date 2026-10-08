"""Slack Web API messaging client used by the gateway."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Protocol

from slack_sdk.errors import SlackApiError
from slack_sdk.web import WebClient

# Slack rejects chat.postMessage text above this length with msg_too_long.
SLACK_MAX_MESSAGE_CHARS = 40_000
# Block Kit markdown blocks cap at 12k chars; longer answers fall back to
# mrkdwn text, which Slack accepts up to SLACK_MAX_MESSAGE_CHARS.
SLACK_MAX_MARKDOWN_BLOCK_CHARS = 12_000

logger = logging.getLogger(__name__)

_WORKING_REACTION = "eyes"
_DONE_REACTION = "white_check_mark"
_FAILED_REACTION = "x"

Blocks = Sequence[dict[str, Any]]


class SlackMessagingClient(Protocol):
    """The messaging surface the Slack turn output needs."""

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: Blocks | None = None,
    ) -> str | None:
        """Post a message and return its ``ts``, or ``None`` on failure."""

    def update_message(
        self,
        *,
        channel: str,
        ts: str,
        text: str,
        blocks: Blocks | None = None,
    ) -> bool:
        """Replace a posted message's text; return whether the update succeeded."""

    def add_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        """Add an emoji reaction; return whether it succeeded."""

    def remove_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        """Remove an emoji reaction; return whether it succeeded."""

    def delete_message(self, *, channel: str, ts: str) -> bool:
        """Delete one of the bot's own messages; return whether it succeeded."""

    def start_stream(self, *, channel: str, thread_ts: str) -> str | None:
        """Open a streamed timeline message; return its ``ts`` or ``None``."""

    def append_stream(self, *, channel: str, ts: str, chunks: Blocks) -> bool:
        """Append markdown/task chunks to a streamed message."""

    def stop_stream(self, *, channel: str, ts: str, blocks: Blocks | None = None) -> bool:
        """Finish a streamed message, optionally attaching final blocks."""
        ...

    def set_thread_status(
        self,
        *,
        channel: str,
        thread_ts: str,
        status: str,
        loading_messages: Sequence[str] | None = None,
    ) -> bool:
        """Show or clear the loading indicator on the triggering thread.

        This is Slack's assistant status on that thread, not a reply. An empty
        ``status`` clears it.
        """
        ...


# API errors that mean streaming will never work for this app/workspace
# (feature-gated or unknown method) — cache and stop probing until restart.
_STREAMING_UNSUPPORTED_ERRORS = frozenset(
    {
        "unknown_method",
        "method_not_supported_for_channel_type",
        "not_allowed_token_type",
        "missing_scope",
        "feature_not_enabled",
        "not_allowed",
        "invalid_arguments",
    }
)


class SlackWebApiClient:
    """:class:`SlackMessagingClient` backed by the Slack Web API."""

    def __init__(self, web_client: WebClient) -> None:
        self._web_client = web_client
        self._streaming_unsupported = False

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: Blocks | None = None,
    ) -> str | None:
        # Blocks are best-effort: if Slack rejects them (workspace/app plan
        # limitations on the markdown block), deliver the plain text instead
        # of dropping the answer.
        if blocks is not None:
            try:
                response = self._web_client.chat_postMessage(
                    channel=channel,
                    text=text,
                    thread_ts=thread_ts,
                    blocks=list(blocks),
                )
                return str(response.get("ts") or "") or None
            except SlackApiError as exc:
                logger.warning(
                    "[slack-gateway] chat.postMessage with blocks failed (%s); retrying text-only",
                    exc.response.get("error"),
                )
        try:
            response = self._web_client.chat_postMessage(
                channel=channel,
                text=text,
                thread_ts=thread_ts,
            )
        except SlackApiError as exc:
            logger.error("[slack-gateway] chat.postMessage failed: %s", exc.response.get("error"))
            return None
        return str(response.get("ts") or "") or None

    def update_message(
        self,
        *,
        channel: str,
        ts: str,
        text: str,
        blocks: Blocks | None = None,
    ) -> bool:
        if blocks is not None:
            try:
                self._web_client.chat_update(channel=channel, ts=ts, text=text, blocks=list(blocks))
                return True
            except SlackApiError as exc:
                logger.warning(
                    "[slack-gateway] chat.update with blocks failed (%s); retrying text-only",
                    exc.response.get("error"),
                )
        try:
            self._web_client.chat_update(channel=channel, ts=ts, text=text)
        except SlackApiError as exc:
            logger.debug("[slack-gateway] chat.update failed: %s", exc.response.get("error"))
            return False
        return True

    def add_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        try:
            self._web_client.reactions_add(channel=channel, timestamp=timestamp, name=emoji)
        except SlackApiError as exc:
            error = str(exc.response.get("error") or "")
            if error == "already_reacted":
                return True
            logger.debug("[slack-gateway] reactions.add failed: %s", error)
            return False
        return True

    def remove_reaction(self, *, channel: str, timestamp: str, emoji: str) -> bool:
        try:
            self._web_client.reactions_remove(channel=channel, timestamp=timestamp, name=emoji)
        except SlackApiError as exc:
            error = str(exc.response.get("error") or "")
            if error == "no_reaction":
                return True
            logger.debug("[slack-gateway] reactions.remove failed: %s", error)
            return False
        return True

    def delete_message(self, *, channel: str, ts: str) -> bool:
        try:
            self._web_client.chat_delete(channel=channel, ts=ts)
        except SlackApiError as exc:
            logger.debug("[slack-gateway] chat.delete failed: %s", exc.response.get("error"))
            return False
        except Exception:
            logger.debug("[slack-gateway] chat.delete failed", exc_info=True)
            return False
        return True

    def start_stream(self, *, channel: str, thread_ts: str) -> str | None:
        # Streaming is documented under Slack's AI-apps surface; whether it
        # works without the Agents feature toggle is workspace/app-dependent,
        # so the first failure with a permanent-looking error disables it for
        # this process and this class falls back to placeholder editing.
        if self._streaming_unsupported:
            return None
        try:
            response = self._web_client.chat_startStream(
                channel=channel,
                thread_ts=thread_ts,
                task_display_mode="timeline",
            )
        except SlackApiError as exc:
            error = str(exc.response.get("error") or "")
            if error in _STREAMING_UNSUPPORTED_ERRORS:
                self._streaming_unsupported = True
                logger.info(
                    "[slack-gateway] chat.startStream unsupported (%s); "
                    "falling back to message editing for this process",
                    error,
                )
            else:
                logger.warning("[slack-gateway] chat.startStream failed: %s", error)
            return None
        except Exception:
            logger.warning("[slack-gateway] chat.startStream failed", exc_info=True)
            return None
        return str(response.get("ts") or "") or None

    def append_stream(self, *, channel: str, ts: str, chunks: Blocks) -> bool:
        try:
            self._web_client.chat_appendStream(channel=channel, ts=ts, chunks=list(chunks))
        except SlackApiError as exc:
            logger.warning(
                "[slack-gateway] chat.appendStream failed: %s", exc.response.get("error")
            )
            return False
        except Exception:
            logger.warning("[slack-gateway] chat.appendStream failed", exc_info=True)
            return False
        return True

    def stop_stream(self, *, channel: str, ts: str, blocks: Blocks | None = None) -> bool:
        if blocks is not None:
            try:
                self._web_client.chat_stopStream(channel=channel, ts=ts, blocks=list(blocks))
                return True
            except SlackApiError as exc:
                # A workspace that rejects our closing blocks (feedback buttons
                # may be feature-gated) must still get the stream stopped.
                logger.warning(
                    "[slack-gateway] chat.stopStream with blocks failed (%s); "
                    "retrying without blocks",
                    exc.response.get("error"),
                )
            except Exception:
                logger.warning("[slack-gateway] chat.stopStream failed", exc_info=True)
        try:
            self._web_client.chat_stopStream(channel=channel, ts=ts)
        except SlackApiError as exc:
            logger.warning("[slack-gateway] chat.stopStream failed: %s", exc.response.get("error"))
            return False
        except Exception:
            logger.warning("[slack-gateway] chat.stopStream failed", exc_info=True)
            return False
        return True

    def set_thread_status(
        self,
        *,
        channel: str,
        thread_ts: str,
        status: str,
        loading_messages: Sequence[str] | None = None,
    ) -> bool:
        """Loading indicator on the triggering thread. Empty ``status`` clears it."""
        payload: dict[str, Any] = {
            "channel_id": channel,
            "thread_ts": thread_ts,
            "status": status,
        }
        if loading_messages:
            payload["loading_messages"] = list(loading_messages)[:10]
        try:
            self._web_client.assistant_threads_setStatus(**payload)
        except SlackApiError as exc:
            logger.debug(
                "[slack-gateway] assistant.threads.setStatus failed: %s",
                exc.response.get("error"),
            )
            return False
        except Exception:
            logger.debug("[slack-gateway] assistant.threads.setStatus failed", exc_info=True)
            return False
        return True


def mark_turn_working(client: SlackMessagingClient, *, channel: str, timestamp: str) -> None:
    """Best-effort eyes reaction while the agent is working."""
    client.add_reaction(channel=channel, timestamp=timestamp, emoji=_WORKING_REACTION)


def mark_turn_done(client: SlackMessagingClient, *, channel: str, timestamp: str) -> None:
    """Swap eyes → checkmark when the turn finishes."""
    client.remove_reaction(channel=channel, timestamp=timestamp, emoji=_WORKING_REACTION)
    client.add_reaction(channel=channel, timestamp=timestamp, emoji=_DONE_REACTION)


def mark_turn_failed(client: SlackMessagingClient, *, channel: str, timestamp: str) -> None:
    """Swap eyes → x when the turn raised without completing."""
    client.remove_reaction(channel=channel, timestamp=timestamp, emoji=_WORKING_REACTION)
    client.add_reaction(channel=channel, timestamp=timestamp, emoji=_FAILED_REACTION)
