"""Download and inline Discord attachments for gateway turns."""

from __future__ import annotations

import logging

import httpx

from config.constants.discord import DISCORD_ATTACHMENT_HOST_SUFFIXES
from config.constants.gateway import ATTACHMENT_MAX_TOTAL_CHARS
from core.llm.image_description import describe_image_via_provider, is_supported_image
from gateway.core.attachments.fetch import is_allowed_host
from gateway.core.attachments.inline import (
    budgeted_section,
    is_text_mimetype,
    join_attachment_sections,
    truncate_attachment_text,
)
from gateway.transports.discord.events import DiscordInboundAttachment

logger = logging.getLogger(__name__)

_MAX_BYTES = 256 * 1024


def is_allowed_attachment_url(url: str) -> bool:
    """Reject non-Discord hosts so the bot token never leaves Discord CDN."""
    return is_allowed_host(url, DISCORD_ATTACHMENT_HOST_SUFFIXES)


def _download(url: str, bot_token: str) -> bytes | None:
    if not is_allowed_attachment_url(url):
        logger.warning("[discord-gateway] attachment URL host rejected")
        return None
    try:
        # No follow_redirects: a CDN redirect off Discord must not carry the bot token.
        with httpx.stream(
            "GET",
            url,
            headers={"Authorization": f"Bot {bot_token}"},
            follow_redirects=False,
            timeout=10.0,
        ) as response:
            if response.status_code != httpx.codes.OK:
                return None
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > _MAX_BYTES:
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx.HTTPError:
        return None


def build_discord_attachments_context(
    attachments: tuple[DiscordInboundAttachment, ...],
    *,
    bot_token: str,
) -> str:
    if not attachments:
        return ""
    sections: list[str] = []
    remaining = ATTACHMENT_MAX_TOTAL_CHARS
    for item in attachments:
        label = item.filename or "attachment"
        if remaining <= 0:
            sections.append(f"- {label} — omitted (attachment budget reached)")
            continue
        if is_supported_image(item.content_type):
            data = _download(item.url, bot_token)
            if data is None:
                sections.append(f"- {label} — could not be downloaded")
                continue
            description = describe_image_via_provider(data, item.content_type)
            if not description:
                sections.append(f"- {label} — image could not be described")
                continue
            header = f"--- image: {label} (vision description) ---"
            section, consumed = budgeted_section(header, description, remaining)
            remaining -= consumed
            sections.append(section)
            continue
        if is_text_mimetype(item.content_type):
            data = _download(item.url, bot_token)
            if data is None:
                sections.append(f"- {label} — could not be downloaded")
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("latin-1", errors="replace")
            text = truncate_attachment_text(text)
            header = f"--- attached file: {label} ---"
            section, consumed = budgeted_section(header, text, remaining)
            remaining -= consumed
            sections.append(section)
            continue
        sections.append(f"- {label} ({item.content_type}) — not readable")
    return join_attachment_sections(sections)


__all__ = ["build_discord_attachments_context", "is_allowed_attachment_url"]
