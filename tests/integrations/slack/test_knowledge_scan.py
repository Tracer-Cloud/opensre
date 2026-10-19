"""The Slack scan reads only what any full member could, and fails without losing knowledge.

A scan acts for no member, and what it writes reaches every member's prompt.
So it must never request private channels or DMs, never list guests, never
read a Slack Connect channel's messages (another organization's people), and
never hand a secret to the model. The service around it must scan a newly
connected integration at once without hammering one that fails.
"""

from __future__ import annotations

from typing import Any

import pytest

import integrations.slack.knowledge_scan as scan_module
import integrations.slack.web_client as web_client
from config.constants import OPENSRE_KNOWLEDGE_SCAN_MESSAGES_ENV, OPENSRE_MEMORY_DISABLED_ENV
from core.domain.knowledge import (
    ORGANIZATION_SLUG,
    ScanOutcome,
    list_workspace_entries,
    load_workspace_entry,
)
from integrations.knowledge_scans import KnowledgeScanService, Scanner
from integrations.slack.web_client import SlackBotTarget

_TARGET = SlackBotTarget(bot_token="xoxb-test")


@pytest.fixture(autouse=True)
def _memory_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)


def _install_workspace(monkeypatch: pytest.MonkeyPatch, history_calls: list[str]) -> None:
    monkeypatch.setattr(
        scan_module,
        "fetch_workspace_identity",
        lambda _t: ({"name": "Acme", "domain": "acme", "url": "https://acme.slack.com/"}, ""),
    )
    members = [
        {"id": "U1", "real_name": "Ada Lovelace", "title": "SRE lead"},
        {"id": "U2", "real_name": "Guest Contractor", "title": "", "is_guest": True},
        {"id": "B1", "real_name": "Deploy Bot", "is_bot": True},
    ]
    monkeypatch.setattr(scan_module, "fetch_team_members", lambda _t: (members, "", False))
    channels = [
        {"id": "C1", "name": "payments-oncall", "num_members": 12, "is_member": True},
        {
            "id": "C2",
            "name": "partner-acme-x",
            "num_members": 30,
            "is_member": True,
            "is_ext_shared": True,
        },
        {"id": "C3", "name": "random", "num_members": 40, "is_member": False},
    ]
    monkeypatch.setattr(scan_module, "list_public_channels", lambda _t: (channels, "", False))

    def history(
        _t: SlackBotTarget, *, channel_id: str, limit: int
    ) -> tuple[list[dict[str, str]], str]:
        history_calls.append(channel_id)
        return (
            [
                {"user": "U1", "text": "checkout p99 is back to normal after the rollback"},
                {"user": "U1", "text": "rotated key AKIAIOSFODNN7EXAMPLE for the payments job"},
                {"user": "B9", "text": "deploy finished"},
            ],
            "",
        )

    monkeypatch.setattr(scan_module, "fetch_channel_messages", history)


def test_a_scan_writes_the_profile_from_what_any_full_member_could_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history_calls: list[str] = []
    _install_workspace(monkeypatch, history_calls)
    prompts: list[str] = []

    def summarize(prompt: str) -> str:
        prompts.append(prompt)
        return (
            '{"overview": "Acme runs a payments platform.", '
            '"channels": {"payments-oncall": "pages for checkout", "secret-plans": "made up"}}'
        )

    outcome = scan_module.scan_slack_workspace(summarize=summarize, target=_TARGET)

    assert outcome.ok and set(outcome.written) == {ORGANIZATION_SLUG, "integration-slack"}
    organization = load_workspace_entry(ORGANIZATION_SLUG)
    assert organization is not None
    assert "Ada Lovelace" in organization.body
    assert "Acme runs a payments platform." in organization.body
    # Guests and bots are not the team.
    assert "Guest Contractor" not in organization.body
    assert "Deploy Bot" not in organization.body
    # Only the member, unshared channel was read: never #random (not a member)
    # nor the Slack Connect channel (another organization's messages).
    assert history_calls == ["C1"]
    # The model never saw the secret or a bot's message.
    assert "AKIAIOSFODNN7EXAMPLE" not in prompts[0]
    assert "deploy finished" not in prompts[0]
    # A note for a channel that does not exist is dropped.
    channel_map = load_workspace_entry("integration-slack")
    assert channel_map is not None and "made up" not in channel_map.body
    assert organization.verified is True and "Slack scan" in organization.evidence


def test_with_message_reading_off_a_scan_reads_no_message(monkeypatch: pytest.MonkeyPatch) -> None:
    history_calls: list[str] = []
    _install_workspace(monkeypatch, history_calls)
    monkeypatch.setenv(OPENSRE_KNOWLEDGE_SCAN_MESSAGES_ENV, "0")

    outcome = scan_module.scan_slack_workspace(summarize=lambda _p: "{}", target=_TARGET)

    assert outcome.ok
    assert history_calls == []


def test_a_failed_scan_keeps_the_previous_knowledge(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_workspace(monkeypatch, [])
    assert scan_module.scan_slack_workspace(target=_TARGET).ok
    before = load_workspace_entry(ORGANIZATION_SLUG)
    monkeypatch.setattr(scan_module, "fetch_workspace_identity", lambda _t: (None, "invalid_auth"))

    outcome = scan_module.scan_slack_workspace(target=_TARGET)

    assert not outcome.ok and outcome.error == "invalid_auth"
    assert load_workspace_entry(ORGANIZATION_SLUG) == before


def test_the_channel_list_never_asks_slack_for_private_channels_or_dms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []

    def fake_request(_method: str, path: str, _token: str, *, params: dict[str, Any]) -> Any:
        seen.append({"path": path, **params})
        return (
            {
                "ok": True,
                "channels": [
                    {"id": "C1", "name": "general", "num_members": 3},
                    {"id": "G1", "name": "leadership", "is_private": True},
                ],
            },
            "",
        )

    monkeypatch.setattr(web_client, "_request_json", fake_request)

    channels, error, _truncated = web_client.list_public_channels(_TARGET)

    assert error == "" and [c["id"] for c in channels or []] == ["C1"]
    assert [call["types"] for call in seen] == ["public_channel"]


def _scanner(results: list[ScanOutcome], calls: list[str], *, ok: bool = True) -> Scanner:
    def scan(_summarize: Any) -> ScanOutcome:
        calls.append("slack")
        if ok:
            from core.domain.knowledge import save_workspace_entry
            from core.domain.memory import MemoryType

            save_workspace_entry(
                slug="integration-slack",
                memory_type=MemoryType.INTEGRATION,
                description="Slack workspace",
                body="channels",
            )
        outcome = ScanOutcome("slack", ok=ok, written=("integration-slack",) if ok else ())
        results.append(outcome)
        return outcome

    return Scanner("slack", available=lambda: True, scan=scan)


def test_a_new_connection_is_scanned_once_and_a_failure_is_not_retried_every_pass() -> None:
    calls: list[str] = []
    clock = {"now": 1_000.0}
    service = KnowledgeScanService(
        scanners=(_scanner([], calls),),
        summarizer_factory=lambda: None,
        clock=lambda: clock["now"],
        retry_after_seconds=3_600,
    )

    service.run_due()
    service.run_due()

    assert calls == ["slack"]  # missing entry → scanned; fresh entry → left alone
    assert list_workspace_entries()

    failing_calls: list[str] = []
    failing = KnowledgeScanService(
        scanners=(_scanner([], failing_calls, ok=False),),
        summarizer_factory=lambda: None,
        clock=lambda: clock["now"],
        retry_after_seconds=3_600,
        max_age_seconds=0,
    )
    failing.run_due()
    failing.run_due()
    assert failing_calls == ["slack"]
    clock["now"] += 3_601
    failing.run_due()
    assert failing_calls == ["slack", "slack"]


def test_a_rescan_request_scans_a_fresh_integration_and_unknown_ones_are_refused() -> None:
    calls: list[str] = []
    service = KnowledgeScanService(scanners=(_scanner([], calls),), summarizer_factory=lambda: None)
    service.run_due()

    assert service.request_rescan("slack") is True
    assert service.request_rescan("pagerduty") is False
    service.run_due()

    assert calls == ["slack", "slack"]
    assert service.status_rows()[0]["service"] == "slack"
