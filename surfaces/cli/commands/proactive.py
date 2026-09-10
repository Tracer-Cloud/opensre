"""Operator interface for proactive Slack policy inspection and triggering."""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict
from typing import Any

import click

from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness import load_master_judgement
from infrastructure.analytics.usage_context import UsageSurface, bound_usage_context
from infrastructure.proactive_messages import (
    DecisionLedger,
    ProactiveContextReader,
    ProactiveDelivery,
    ProactiveJudgementRunner,
    ProactiveTrigger,
    decision_ledger_path,
    judgement_cursor_path,
    latest_completed_interaction_boundary,
)
from integrations.slack import (
    SlackBotTarget,
    fetch_channel_messages,
    markdown_to_slack_mrkdwn,
    post_channel_message_with_id,
    resolve_bot_token,
    resolve_channel_id,
)

_TEST_MESSAGES = (
    "[TEST] Proactive messaging is connected. A real follow-up would include verified evidence, an owner, and a next action.",
    "[TEST] OpenSRE proactive policy delivery reached this Slack conversation successfully.",
    "[TEST] Proactive signal check complete. No production incident or action is implied by this message.",
)


@click.group(name="proactive")
def proactive_command() -> None:
    """Manage and manually trigger proactive Slack message policies."""


@proactive_command.command(name="policies")
@click.option("--json", "json_out", is_flag=True, help="Emit JSON.")
@click.option("--show-body", is_flag=True, help="Print the full trusted Markdown policy.")
def proactive_policies(json_out: bool, show_body: bool) -> None:
    """List the enabled master policy and its starter rules."""
    policy = load_master_judgement()
    payload: dict[str, Any] = {
        "name": policy.name,
        "version": policy.version,
        "enabled": policy.enabled,
        "trigger": policy.trigger,
        "schedule": policy.schedule,
        "rules": list(policy.rule_names),
        "slack_context_limit": policy.slack_context_limit,
    }
    if show_body:
        payload["body"] = policy.body
    if json_out:
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    click.echo(f"{policy.name} v{policy.version} ({'enabled' if policy.enabled else 'disabled'})")
    click.echo(f"Trigger: {policy.trigger} / {policy.schedule}")
    for rule_name in policy.rule_names:
        click.echo(f"  - {rule_name}")
    if show_body:
        click.echo(f"\n{policy.body}")


@proactive_command.command(name="status")
@click.option("--organization-id", default="", help="Organization storage owner.")
@click.option("--user-id", default="local", show_default=True, help="Actor storage owner.")
@click.option("--json", "json_out", is_flag=True, help="Emit JSON.")
def proactive_status(organization_id: str, user_id: str, json_out: bool) -> None:
    """Show policy, Slack credential, and persistence status."""
    scope = _scope(organization_id, user_id)
    target, _error = resolve_bot_token()
    with bound_storage_scope(scope):
        ledger_path = decision_ledger_path()
        cursor_path = judgement_cursor_path()
        decisions = DecisionLedger().recent(50) if ledger_path.exists() else []
    policy = load_master_judgement()
    payload = {
        "policy": policy.name,
        "policy_enabled": policy.enabled,
        "rules": list(policy.rule_names),
        "slack_bot_token_configured": target is not None,
        "decision_count": len(decisions),
        "decision_ledger": str(ledger_path),
        "judgement_cursor": str(cursor_path),
    }
    if json_out:
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    click.echo(f"Policy: {policy.name} ({'enabled' if policy.enabled else 'disabled'})")
    click.echo(f"Slack bot token: {'configured' if target is not None else 'missing'}")
    click.echo(f"Decisions: {len(decisions)}")
    click.echo(f"Ledger: {ledger_path}")
    click.echo(f"Cursor: {cursor_path}")


@proactive_command.command(name="send-test")
@click.option("--channel-id", required=True, help="Slack C…, D…, G…, or #channel target.")
@click.option("--thread-ts", default="", help="Optional originating thread timestamp.")
@click.option("--message", default="", help="Override the clearly marked test message.")
def proactive_send_test(channel_id: str, thread_ts: str, message: str) -> None:
    """Send one explicitly marked test message without running policy judgement."""
    target, resolved_channel = _resolve_slack_target(channel_id)
    text = message.strip() or secrets.choice(_TEST_MESSAGES)
    message_ts, error = post_channel_message_with_id(
        target,
        channel_id=resolved_channel,
        thread_ts=thread_ts.strip(),
        text=text,
    )
    if message_ts is None:
        raise click.ClickException(error or "Slack did not accept the proactive test message.")
    click.echo(f"Sent proactive test message {message_ts} to {resolved_channel}.")


@proactive_command.command(name="trigger")
@click.option("--session-id", required=True, help="Persisted session to evaluate.")
@click.option("--channel-id", required=True, help="Originating Slack C…, D…, G…, or #channel.")
@click.option("--thread-ts", required=True, help="Originating Slack thread timestamp.")
@click.option("--user-id", required=True, help="Originating Slack user ID.")
@click.option("--organization-id", default="", help="Organization storage owner, when scoped.")
@click.option("--json", "json_out", is_flag=True, help="Emit JSON.")
def proactive_trigger(
    session_id: str,
    channel_id: str,
    thread_ts: str,
    user_id: str,
    organization_id: str,
    json_out: bool,
) -> None:
    """Evaluate the latest completed interaction and deliver its policy decision."""
    scope = _scope(organization_id, user_id)
    target, resolved_channel = _resolve_slack_target(channel_id)
    with bound_storage_scope(scope):
        boundary = latest_completed_interaction_boundary(session_id)
        if boundary is None:
            raise click.ClickException(
                f"Session {session_id!r} has no completed user/assistant interaction in this scope."
            )
        start_record_id, end_record_id = boundary
        runner = ProactiveJudgementRunner(
            context_reader=_context_reader(target),
            delivery=_delivery(target),
        )
        trigger = ProactiveTrigger(
            session_id=session_id,
            start_record_id=start_record_id,
            end_record_id=end_record_id,
            channel_id=resolved_channel,
            thread_ts=thread_ts.strip(),
            user_id=user_id,
        )
        with bound_usage_context(
            surface=UsageSurface.SLACK,
            session_id=session_id,
            user_id=user_id,
        ):
            outcome = runner.run(trigger)
        ledger_path = decision_ledger_path()
    payload = {**asdict(outcome), "decision_ledger": str(ledger_path)}
    if json_out:
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    click.echo(f"Judgement: {outcome.status}")
    if outcome.slack_message_ts:
        click.echo(f"Slack message: {outcome.slack_message_ts}")
    click.echo(f"Ledger: {ledger_path}")


def _scope(organization_id: str, user_id: str) -> StorageScope:
    actor = Actor(user_id)
    principal = (
        Principal.org(organization_id) if organization_id.strip() else Principal.individual("local")
    )
    return StorageScope(principal=principal, actor=actor)


def _resolve_slack_target(channel_id: str) -> tuple[SlackBotTarget, str]:
    target, error = resolve_bot_token()
    if target is None:
        raise click.ClickException(
            "Slack bot token is missing. Run `opensre integrations setup slack` "
            "or set SLACK_BOT_TOKEN before triggering delivery."
        )
    resolved_channel, channel_error = resolve_channel_id(target, channel_id.strip())
    if resolved_channel is None:
        raise click.ClickException(channel_error or error or "Slack channel could not be resolved.")
    return target, resolved_channel


def _context_reader(target: SlackBotTarget) -> ProactiveContextReader:
    def _read(*, channel_id: str, thread_ts: str, limit: int) -> dict[str, Any]:
        messages, _error = fetch_channel_messages(
            target,
            channel_id=channel_id,
            thread_ts=thread_ts,
            limit=limit,
        )
        if messages is None:
            return {"status": "failed", "error_type": "api_error", "messages": []}
        return {
            "status": "read",
            "channel_id": channel_id,
            "messages": messages,
            "message_count": len(messages),
            "truncated": len(messages) >= limit,
        }

    return _read


def _delivery(target: SlackBotTarget) -> ProactiveDelivery:
    def _send(*, channel_id: str, thread_ts: str, message: str) -> str | None:
        message_ts, error = post_channel_message_with_id(
            target,
            channel_id=channel_id,
            thread_ts=thread_ts,
            text=markdown_to_slack_mrkdwn(message),
        )
        if message_ts is None:
            raise RuntimeError(error or "Slack proactive delivery failed")
        return message_ts

    return _send


__all__ = ["proactive_command"]
