"""Delivery target resolution and validation helpers for work items."""

from __future__ import annotations

from core.domain.work_items import WorkItem, WorkItemChannelTarget, dedupe_channel_targets
from core.tool import AgentToolContext
from infrastructure.scheduling.scheduler.types import Provider
from tools.system.scheduled_loops.destination import conversation_destination
from tools.system.work_items.validation import validate_provider


def gateway_delivery_context(context: AgentToolContext | None) -> tuple[str, str]:
    """Extract default gateway delivery platform and chat identifier from tool context."""
    if context is None:
        return "", ""
    try:
        from core.agent_harness.tools import action_context_from_agent_context

        action_ctx = action_context_from_agent_context(context)
    except RuntimeError:
        return "", ""
    return conversation_destination(action_ctx.session)


def delivery_targets(
    *,
    provider: str,
    chat_id: str,
    item: WorkItem | None = None,
    channel_targets: list[dict[str, str]] | None = None,
    context: AgentToolContext | None = None,
) -> list[WorkItemChannelTarget]:
    """Resolve and deduplicate target channels for work item delivery or reminders."""
    default_provider, default_chat_id = gateway_delivery_context(context)
    targets: list[WorkItemChannelTarget] = []
    for raw_target in channel_targets or []:
        if isinstance(raw_target, dict):
            targets.append(WorkItemChannelTarget.from_mapping(raw_target))
    if provider.strip():
        targets.append(
            WorkItemChannelTarget(provider=provider.strip().lower(), chat_id=chat_id.strip())
        )
    if item is not None:
        targets.extend(item.channel_targets)
        if item.channel.provider:
            targets.append(item.channel)
    if not targets and default_provider:
        targets.append(WorkItemChannelTarget(provider=default_provider, chat_id=default_chat_id))
    return dedupe_targets(targets)


def dedupe_targets(targets: list[WorkItemChannelTarget]) -> list[WorkItemChannelTarget]:
    """Deduplicate channel target items while preserving order."""
    return list(dedupe_channel_targets(targets))


def invalid_delivery_targets(targets: list[WorkItemChannelTarget]) -> list[str]:
    """Return errors for any delivery target with unsupported providers or missing chat IDs."""
    invalid: list[str] = []
    for target in targets:
        parsed_provider = validate_provider(target.provider)
        if parsed_provider is None:
            invalid.append(f"{target.provider}: unsupported provider")
            continue
        if parsed_provider is Provider.SLACK:
            continue
        if not target.chat_id:
            invalid.append(f"{target.provider}: missing chat_id")
    return invalid


_gateway_delivery_context = gateway_delivery_context
_delivery_targets = delivery_targets
_dedupe_targets = dedupe_targets
_invalid_delivery_targets = invalid_delivery_targets

__all__ = [
    "_dedupe_targets",
    "_delivery_targets",
    "_gateway_delivery_context",
    "_invalid_delivery_targets",
    "dedupe_targets",
    "delivery_targets",
    "gateway_delivery_context",
    "invalid_delivery_targets",
]
