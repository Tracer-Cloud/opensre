"""A loop can email its report from its owner's own SMTP or Gmail mailbox."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from config.scope_context import current_scope
from infrastructure.scheduling.scheduler.delivery_plan import resolve_delivery_plan
from infrastructure.scheduling.scheduler.loop_constants import (
    LOOP_CHANNELS_PARAM,
    LOOP_CREATED_BY_PARAM,
    LOOP_EMAIL_ACCOUNT_PARAM,
    LOOP_EMAIL_TO_PARAM,
)
from infrastructure.scheduling.scheduler.loops import create_manual_loop
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from integrations.scheduled_email import EmailScheduledDelivery

_REPORT = "# Morning report · Mon 19 Oct\nAll quiet — CI and Sentry checked."


def _email_task(account: str) -> ScheduledTask:
    return ScheduledTask(
        id="loop-email",
        name="Morning report",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 9 * * 1-5",
        provider=Provider.EMAIL,
        chat_id="ana@example.com,ben@example.com",
        organization="org_A",
        params={
            LOOP_CREATED_BY_PARAM: "user_ana",
            LOOP_EMAIL_ACCOUNT_PARAM: account,
            LOOP_CHANNELS_PARAM: "email",
        },
    )


def test_an_email_loop_stores_its_recipients_and_mailbox(tmp_path: Path) -> None:
    # Act
    loop = create_manual_loop(
        name="Morning report",
        prompt="Summarize CI.",
        cron="0 9 * * 1-5",
        channels=["email"],
        email_to="ana@example.com; ben@example.com ana@example.com",
        email_account="gmail:apn_123",
        store_path=tmp_path / "tasks.json",
    )

    # Assert: deduplicated recipients become the email destination.
    params = loop.task.params
    assert params[LOOP_EMAIL_TO_PARAM] == "ana@example.com,ben@example.com"
    assert params[LOOP_EMAIL_ACCOUNT_PARAM] == "gmail:apn_123"
    [target] = resolve_delivery_plan(loop.task).targets
    assert (target.provider, target.chat_id) == (Provider.EMAIL, "ana@example.com,ben@example.com")


@pytest.mark.parametrize(
    ("email_to", "account", "message"),
    [
        ("", "smtp:rec_1", "at least one recipient"),
        ("not-an-address", "smtp:rec_1", "not an email address"),
        ("ana@example.com", "", "mailbox to send from"),
        ("ana@example.com", "outlook:1", "mailbox to send from"),
    ],
)
def test_an_email_loop_needs_valid_recipients_and_a_mailbox(
    tmp_path: Path, email_to: str, account: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        create_manual_loop(
            name="Morning report",
            prompt="Summarize CI.",
            cron="0 9 * * 1-5",
            channels=["email"],
            email_to=email_to,
            email_account=account,
            store_path=tmp_path / "tasks.json",
        )


def test_an_smtp_loop_sends_html_from_the_owners_own_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the owner's personal SMTP record, read in the owner's scope.
    seen_actors: list[str] = []
    sent: list[dict[str, Any]] = []

    def load_member_records() -> list[dict[str, Any]]:
        scope = current_scope()
        seen_actors.append(scope.actor.id if scope else "")
        return [{"service": "smtp", "name": "rec_1", "credentials": {"from_address": "ana@x.io"}}]

    monkeypatch.setattr("integrations.member_integrations.load_member_records", load_member_records)
    monkeypatch.setattr(
        "integrations.scheduled_email.delivery.send_smtp_email",
        lambda **kwargs: sent.append(kwargs) or (True, "", []),
    )

    # Act
    ok, error, _ = EmailScheduledDelivery().deliver(_email_task("smtp:rec_1"), _REPORT)

    # Assert
    assert (ok, error) == (True, "")
    assert seen_actors == ["user_ana"]
    [email] = sent
    assert email["recipients"] == ["ana@example.com", "ben@example.com"]
    assert email["subject"] == "Morning report · Mon 19 Oct"
    assert email["html"].startswith("<!doctype html>")
    assert email["smtp_ctx"] == {"from_address": "ana@x.io"}


def test_an_smtp_loop_whose_account_was_removed_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("integrations.member_integrations.load_member_records", lambda: [])

    ok, error, _ = EmailScheduledDelivery().deliver(_email_task("smtp:rec_1"), _REPORT)

    assert ok is False
    assert "no longer connected" in error


def test_a_gmail_loop_sends_through_the_owners_pipedream_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    calls: list[dict[str, Any]] = []

    def call_proxy_tool(**kwargs: Any) -> dict[str, object]:
        scope = current_scope()
        calls.append({**kwargs, "actor": scope.actor.id if scope else ""})
        return {}

    monkeypatch.setattr("integrations.pipedream.proxy.call_proxy_tool", call_proxy_tool)

    # Act
    ok, error, _ = EmailScheduledDelivery().deliver(_email_task("gmail:apn_123"), _REPORT)

    # Assert: sent as the owner, as HTML, to every recipient.
    assert (ok, error) == (True, "")
    [call] = calls
    assert call["actor"] == "user_ana"
    assert call["personal"] is True
    assert (call["service"], call["account_id"], call["tool_name"]) == (
        "gmail",
        "apn_123",
        "gmail-send-email",
    )
    assert call["arguments"]["to"] == ["ana@example.com", "ben@example.com"]
    assert call["arguments"]["bodyType"] == "html"


def test_an_email_loop_without_an_owner_is_refused() -> None:
    task = _email_task("gmail:apn_123")
    task.params.pop(LOOP_CREATED_BY_PARAM)

    ok, error, _ = EmailScheduledDelivery().deliver(task, _REPORT)

    assert ok is False
    assert "no owner" in error


def test_a_gmail_send_the_tool_rejected_is_a_failed_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the proxy call succeeds but the Gmail tool reports an error.
    monkeypatch.setattr(
        "integrations.pipedream.proxy.call_proxy_tool",
        lambda **_kwargs: {"isError": True, "content": [{"type": "text", "text": "Invalid To"}]},
    )

    # Act
    ok, error, _ = EmailScheduledDelivery().deliver(_email_task("gmail:apn_123"), _REPORT)

    # Assert
    assert ok is False
    assert "Invalid To" in error


def test_a_retry_after_a_partly_refused_send_goes_only_to_the_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the server refuses ben on the first attempt, then accepts him.
    monkeypatch.setattr(
        "integrations.member_integrations.load_member_records",
        lambda: [{"service": "smtp", "name": "rec_1", "credentials": {"from_address": "a@x.io"}}],
    )
    attempts: list[list[str]] = []

    def send(**kwargs: Any) -> tuple[bool, str, list[str]]:
        attempts.append(list(kwargs["recipients"]))
        if len(attempts) == 1:
            return False, "The SMTP server refused ben@example.com", ["ben@example.com"]
        return True, "", []

    monkeypatch.setattr("integrations.scheduled_email.delivery.send_smtp_email", send)
    delivery = EmailScheduledDelivery()
    task = _email_task("smtp:rec_1")

    # Act: the executor retries the failed destination with the same report.
    first = delivery.deliver(task, _REPORT)
    second = delivery.deliver(task, _REPORT)
    third = delivery.deliver(task, _REPORT)

    # Assert: ana is never sent twice; once ben has it, a retry sends nothing.
    assert first[0] is False and "ben@example.com" in first[1]
    assert attempts == [["ana@example.com", "ben@example.com"], ["ben@example.com"]]
    assert second[0] is True and third[0] is True
