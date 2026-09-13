"""Resolve a scheduled task into the ordered destinations one run delivers to.

Destination resolution happens once, on the calling thread, before any fan-out
starts: the Slack/Telegram credential lookups a destination needs are not
repeated per delivery attempt, and the resulting order is what per-target run
history is persisted in.

A task carries at most one fan-out mechanism. ``delivery_targets`` (explicit
provider/chat pairs, written by work-item scheduling) wins over the loop
``channels`` list; a task with neither delivers to its own provider/chat. They
are mutually exclusive by construction here so a task holding both cannot
cross-product into duplicate sends.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

from infrastructure.scheduling.scheduler.credentials import (
    resolve_slack_credentials,
    resolve_telegram_default_chat_id,
)
from infrastructure.scheduling.scheduler.delivery import resolve_slack_delivery_chat_id
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_CHANNELS_PARAM,
    LOOP_TELEGRAM_CHAT_ID_PARAM,
)
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask

#: A destination identity: what a run's targets are matched against on rerun.
TargetKey = tuple[Provider, str]

_DELIVERY_TARGETS_PARAM = "delivery_targets"


@dataclass(frozen=True, slots=True)
class DeliveryTarget:
    """One destination for a run, with the task view its adapter is handed."""

    provider: Provider
    chat_id: str
    task: ScheduledTask

    def label(self) -> str:
        """Human-readable destination name used in log and error text."""
        return f"{self.provider.value}:{self.chat_id}" if self.chat_id else self.provider.value


@dataclass(frozen=True, slots=True)
class DeliveryPlan:
    """The destinations for one run, in the order outcomes are reported.

    ``fanned_out`` is about configuration, not target count: a task that asked
    for fan-out retries each failed destination, while a plain single-provider
    task keeps one attempt so an unrecoverable error (a missing credential)
    surfaces immediately instead of being retried.
    """

    targets: tuple[DeliveryTarget, ...] = ()
    fanned_out: bool = False
    error: str = ""


def resolve_delivery_plan(
    task: ScheduledTask, *, only: frozenset[TargetKey] | None = None
) -> DeliveryPlan:
    """Return the destinations ``task`` delivers to, or a plan carrying an error.

    ``only``, when given, narrows the plan to destinations whose
    ``(provider, chat_id)`` is in that set -- a rerun retrying just the
    destinations a previous run failed at, rather than every configured
    destination again. An empty (but non-``None``) set means "narrow to
    nothing", which resolves to an explicit no-op error rather than silently
    delivering to everyone.
    """
    plan = _resolve(task)
    if only is None or plan.error:
        return plan
    return _restrict(plan, only)


def _resolve(task: ScheduledTask) -> DeliveryPlan:
    explicit = _explicit_targets(task)
    if explicit:
        return DeliveryPlan(targets=explicit, fanned_out=True)

    channels, channel_error = _loop_channels(task)
    if channel_error:
        return DeliveryPlan(error=channel_error)
    if channels:
        targets = _dedupe(_target_for_provider(task, provider) for provider in channels)
        return DeliveryPlan(targets=targets, fanned_out=True)

    return DeliveryPlan(targets=(DeliveryTarget(task.provider, task.chat_id, task),))


def _restrict(plan: DeliveryPlan, only: frozenset[TargetKey]) -> DeliveryPlan:
    """Narrow ``plan`` to the destinations named in ``only``, keeping order."""
    kept = tuple(target for target in plan.targets if (target.provider, target.chat_id) in only)
    if not kept:
        return DeliveryPlan(error="No matching destinations to retry")
    return DeliveryPlan(targets=kept, fanned_out=plan.fanned_out)


def _explicit_targets(task: ScheduledTask) -> tuple[DeliveryTarget, ...]:
    """Parse the ``delivery_targets`` param; unreadable entries are skipped."""
    raw = task.params.get(_DELIVERY_TARGETS_PARAM, "").strip()
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()

    slack_creds = resolve_slack_credentials(task.params)
    slack_webhook = str(slack_creds.get("webhook_url") or "")
    targets: list[DeliveryTarget] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        provider_text = str(entry.get("provider", "")).strip().lower()
        if not provider_text:
            continue
        try:
            provider = Provider(provider_text)
        except ValueError:
            continue
        chat_id = str(entry.get("chat_id", "")).strip()
        if provider is Provider.SLACK and not chat_id:
            chat_id = resolve_slack_delivery_chat_id(task, webhook_url=slack_webhook)
        targets.append(
            DeliveryTarget(
                provider,
                chat_id,
                task.model_copy(update={"provider": provider, "chat_id": chat_id}),
            )
        )
    return _dedupe(targets)


def _loop_channels(task: ScheduledTask) -> tuple[tuple[Provider, ...], str]:
    """Return the loop fan-out providers, or an error for an unknown channel."""
    raw = task.params.get(LOOP_CHANNELS_PARAM, "").strip()
    if not raw:
        return (), ""

    providers: list[Provider] = []
    seen: set[Provider] = set()
    for item in raw.split(","):
        value = item.strip().lower()
        if not value:
            continue
        try:
            provider = Provider(value)
        except ValueError:
            return (), f"Unsupported loop delivery channel: {value}"
        if provider not in seen:
            providers.append(provider)
            seen.add(provider)
    if not providers:
        return (), "Loop delivery channel list is empty"
    return tuple(providers), ""


def _target_for_provider(task: ScheduledTask, provider: Provider) -> DeliveryTarget:
    """Resolve the destination a loop channel posts to for ``provider``."""
    chat_id = task.chat_id
    if provider == Provider.TELEGRAM:
        chat_id = (
            task.params.get(LOOP_TELEGRAM_CHAT_ID_PARAM, "").strip()
            or (task.chat_id if task.provider == Provider.TELEGRAM else "")
            or resolve_telegram_default_chat_id(task.params)
        )
    elif provider == Provider.SLACK:
        slack_creds = resolve_slack_credentials(task.params)
        chat_id = resolve_slack_delivery_chat_id(
            task,
            webhook_url=str(slack_creds.get("webhook_url") or ""),
        )
    elif provider == Provider.INTERACTIVE_SHELL:
        chat_id = ""
    return DeliveryTarget(
        provider,
        chat_id,
        task.model_copy(update={"provider": provider, "chat_id": chat_id}),
    )


def _dedupe(targets: Iterable[DeliveryTarget]) -> tuple[DeliveryTarget, ...]:
    """Drop repeat destinations, keeping first-seen order."""
    seen: set[tuple[Provider, str]] = set()
    unique: list[DeliveryTarget] = []
    for target in targets:
        key = (target.provider, target.chat_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(target)
    return tuple(unique)


__all__ = [
    "DeliveryPlan",
    "DeliveryTarget",
    "TargetKey",
    "resolve_delivery_plan",
]
