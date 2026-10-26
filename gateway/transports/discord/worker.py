"""Discord Gateway WebSocket worker (discord.py)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import discord

from gateway.core.middleware.approvals import ApprovalBroker
from gateway.core.storage import SessionResolver
from gateway.core.storage.session.binding_store import BindingStore
from gateway.transports.discord.approvals import handle_component_interaction
from gateway.transports.discord.dispatcher import DiscordTurnDispatcher
from gateway.transports.discord.events import (
    DM_GUILD_ID,
    DiscordInboundAttachment,
    DiscordInboundMessage,
)
from gateway.transports.discord.feedback import record_feedback_interaction
from gateway.transports.discord.settings import DiscordGatewaySettings
from infrastructure.turn_host.turn_callback import TurnCallback

_PLATFORM_DISCORD = "discord"
_THREAD_HISTORY_LIMIT = 40
_STATUS_PREFIXES = ("*", "Working", "Thinking", "Running")
_LEADING_MENTION_RE = re.compile(r"^(?:<@!?\d+>\s*)+")


def strip_leading_discord_mentions(text: str) -> str:
    """Remove leading ``<@id>`` tokens so ``@bot /status`` routes as literal slash."""
    return _LEADING_MENTION_RE.sub("", text.strip()).strip()


async def _reap_cancelled_task(task: asyncio.Task[object]) -> None:
    """Await a cancelled task so its cleanup (``finally`` / ``close``) can finish.

    CodeQL ``py/ineffectual-statement`` does not model ``await`` as a side
    effect, so a bare ``await task`` is flagged. Binding the result to ``_``
    keeps the await as an assignment while preserving reap semantics.
    """
    try:
        _ = await task
    except asyncio.CancelledError:
        return


def run_discord_gateway_thread(
    *,
    settings: DiscordGatewaySettings,
    logger: logging.Logger,
    handler: TurnCallback,
    bindings: BindingStore,
    executor: ThreadPoolExecutor,
    stop_event: threading.Event,
    ready_event: threading.Event,
) -> None:
    try:
        asyncio.run(
            _discord_gateway_main(
                settings=settings,
                logger=logger,
                handler=handler,
                bindings=bindings,
                executor=executor,
                stop_event=stop_event,
                ready_event=ready_event,
            )
        )
    except Exception:
        logger.critical("[discord-gateway] fatal error in gateway thread", exc_info=True)


async def _discord_gateway_main(
    *,
    settings: DiscordGatewaySettings,
    logger: logging.Logger,
    handler: TurnCallback,
    bindings: BindingStore,
    executor: ThreadPoolExecutor,
    stop_event: threading.Event,
    ready_event: threading.Event,
) -> None:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.guild_messages = True
    intents.dm_messages = True

    session_resolver = SessionResolver(bindings, platform=_PLATFORM_DISCORD)
    approvals = ApprovalBroker()
    dispatcher = DiscordTurnDispatcher(
        settings=settings,
        bot_token=settings.bot_token,
        session_resolver=session_resolver,
        handler=handler,
        logger=logger,
        approvals=approvals,
    )

    class OpenSREDiscordClient(discord.Client):
        async def on_ready(self) -> None:
            logger.info("[discord-gateway] connected as %s", self.user)
            if self.user:
                dispatcher.set_bot_user_id(str(self.user.id))
            ready_event.set()

        async def on_message(self, message: discord.Message) -> None:
            if message.author.bot or stop_event.is_set():
                return
            inbound = normalize_message(message, bot_user_id=dispatcher.bot_user_id)
            if inbound is None:
                return
            if isinstance(message.channel, discord.Thread):
                history = await _fetch_thread_history(message, bot_user_id=dispatcher.bot_user_id)
                if history:
                    inbound = replace(inbound, thread_history=history)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(executor, dispatcher.dispatch, inbound)

        async def on_interaction(self, interaction: discord.Interaction) -> None:
            if stop_event.is_set():
                return
            if interaction.type == discord.InteractionType.component:
                if record_feedback_interaction(interaction):
                    with contextlib.suppress(discord.HTTPException):
                        await interaction.response.send_message(
                            "Thanks for the feedback.", ephemeral=True, delete_after=3.0
                        )
                    return
                if handle_component_interaction(
                    interaction,
                    broker=approvals,
                    allowed_user_ids=settings.allowed_user_ids,
                ):
                    with contextlib.suppress(discord.HTTPException):
                        await interaction.response.send_message("Recorded.", ephemeral=True)
                    return
                # Ack silently so stale/unauthorized clicks don't show
                # "This interaction failed" in the client.
                with contextlib.suppress(discord.HTTPException):
                    await interaction.response.defer()

    client = OpenSREDiscordClient(intents=intents)

    async def _watch_stop() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(0.5)
        await client.close()

    watch_task = asyncio.create_task(_watch_stop())
    try:
        await client.start(settings.bot_token)
    finally:
        stop_event.set()
        watch_task.cancel()
        await _reap_cancelled_task(watch_task)


async def _fetch_thread_history(
    message: discord.Message,
    *,
    bot_user_id: str,
) -> tuple[tuple[str, str], ...]:
    if not isinstance(message.channel, discord.Thread):
        return ()
    bot_id = bot_user_id.strip()
    out: list[tuple[str, str]] = []
    try:
        async for prior in message.channel.history(limit=_THREAD_HISTORY_LIMIT, oldest_first=True):
            if prior.id == message.id:
                continue
            text = (prior.content or "").strip()
            if not text:
                continue
            if any(text.startswith(prefix) for prefix in _STATUS_PREFIXES):
                continue
            if str(prior.author.id) == bot_id:
                out.append(("assistant", text))
            else:
                out.append(("user", text))
    except discord.HTTPException:
        return ()
    return tuple(out)


def normalize_message(
    message: discord.Message,
    *,
    bot_user_id: str,
    thread_history: tuple[tuple[str, str], ...] = (),
) -> DiscordInboundMessage | None:
    if message.author.bot:
        return None
    user_id = str(message.author.id)
    channel_id = str(message.channel.id)
    guild_id = str(message.guild.id) if message.guild else DM_GUILD_ID
    thread_id = channel_id
    if isinstance(message.channel, discord.Thread):
        thread_id = str(message.channel.id)
    text = strip_leading_discord_mentions((message.content or "").strip())
    if not text and not message.attachments:
        return None

    addressed = isinstance(message.channel, discord.DMChannel)
    if not addressed and bot_user_id:
        addressed = client_mentions_bot(message, bot_user_id)
    is_thread_followup = isinstance(message.channel, discord.Thread) and not addressed
    if not addressed and not is_thread_followup:
        return None

    attachments = tuple(
        DiscordInboundAttachment(
            filename=str(a.filename or "file"),
            url=str(a.url or ""),
            content_type=str(a.content_type or "application/octet-stream"),
            size=int(a.size or 0),
        )
        for a in message.attachments
        if a.url
    )
    return DiscordInboundMessage(
        guild_id=guild_id,
        user_id=user_id,
        channel_id=channel_id,
        message_id=str(message.id),
        thread_id=thread_id,
        text=text,
        addressed=addressed,
        attachments=attachments,
        thread_history=thread_history,
    )


def client_mentions_bot(message: discord.Message, bot_user_id: str) -> bool:
    if not bot_user_id:
        return False
    return any(str(user.id) == bot_user_id for user in message.mentions)


__all__ = [
    "client_mentions_bot",
    "normalize_message",
    "run_discord_gateway_thread",
    "strip_leading_discord_mentions",
]
