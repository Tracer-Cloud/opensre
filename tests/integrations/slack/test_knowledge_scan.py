"""The Slack scan reads only what any full member could, and fails without losing knowledge.

A scan acts for no member, and what it writes reaches every member's prompt.
So it must never request private channels or DMs, never list guests, never
read a Slack Connect channel's messages (another organization's people), and
never hand a secret to the model. The service around it must scan a newly
connected integration at once without hammering one that fails.
"""

from __future__ import annotations

import time
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

_TARGET = web_client.SlackBotTarget(bot_token="xoxb-test")


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
    monkeypatch.setattr(scan_module, "fetch_team_members", lambda _t, **_kw: (members, "", False))
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
        _t: web_client.SlackBotTarget, *, channel_id: str, limit: int
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

    return Scanner("slack", available=lambda: True, scan=scan, writes=("integration-slack",))


def test_a_new_connection_is_scanned_once_and_a_failure_is_not_retried_every_pass() -> None:
    calls: list[str] = []
    clock = {"now": time.time()}
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


def test_a_member_list_that_fails_keeps_the_last_good_knowledge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Review finding: a rate-limited users.list was read as "no members" and
    # overwrote the profile, then marked it fresh for a week.
    _install_workspace(monkeypatch, [])
    assert scan_module.scan_slack_workspace(target=_TARGET).ok
    before = load_workspace_entry(ORGANIZATION_SLUG)
    monkeypatch.setattr(
        scan_module, "fetch_team_members", lambda _t, **_kw: (None, "ratelimited", False)
    )

    outcome = scan_module.scan_slack_workspace(target=_TARGET)

    assert not outcome.ok and outcome.error == "ratelimited"
    assert load_workspace_entry(ORGANIZATION_SLUG) == before


def test_a_secret_in_a_channel_topic_reaches_neither_the_model_nor_the_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Review finding: topics and purposes went to the model unredacted, even
    # with message reading off.
    _install_workspace(monkeypatch, [])
    monkeypatch.setenv(OPENSRE_KNOWLEDGE_SCAN_MESSAGES_ENV, "0")
    secret = "AKIAIOSFODNN7EXAMPLE"
    channels = [
        {
            "id": "C1",
            "name": "payments-oncall",
            "num_members": 12,
            "topic": f"deploy key {secret}",
            "purpose": "pages",
        }
    ]
    monkeypatch.setattr(scan_module, "list_public_channels", lambda _t: (channels, "", False))
    prompts: list[str] = []

    def summarize(prompt: str) -> str:
        prompts.append(prompt)
        return "{}"

    assert scan_module.scan_slack_workspace(summarize=summarize, target=_TARGET).ok

    channel_map = load_workspace_entry("integration-slack")
    assert channel_map is not None
    assert secret not in prompts[0]
    assert secret not in channel_map.body


def test_a_large_workspace_fits_the_entry_and_counts_what_it_left_out() -> None:
    # Review finding: 80 long rows overflowed the store's body limit and the
    # store cut the map mid-row, losing the count of omitted channels.
    from core.domain.memory import MAX_BODY_CHARS

    view = scan_module.SlackWorkspaceView(
        name="Acme",
        url="",
        people=tuple(
            scan_module.SlackPerson(name=f"Person {i:03d} " + "x" * 60, title="y" * 70)
            for i in range(300)
        ),
        channels=tuple(
            scan_module.SlackChannel(
                id=f"C{i:04d}",
                name=f"channel-{i:03d}-" + "n" * 50,
                topic="",
                purpose="p" * 160,
                members=500 - i,
                bot_is_member=True,
                ext_shared=False,
            )
            for i in range(200)
        ),
    )

    _description, channel_map = scan_module.render_channel_map(view, notes={})
    _description, organization = scan_module.render_organization(view, overview="", notes={})

    assert len(channel_map) <= MAX_BODY_CHARS
    assert len(organization) <= MAX_BODY_CHARS
    assert channel_map.rstrip().endswith("more channels.")
    assert "## Key channels" in organization  # the team table never pushes channels out
    assert "more (the full list" in organization


def test_a_scan_that_keeps_failing_backs_off_instead_of_retrying_hourly() -> None:
    calls: list[str] = []
    clock = {"now": 0.0}
    service = KnowledgeScanService(
        scanners=(_scanner([], calls, ok=False),),
        summarizer_factory=lambda: None,
        clock=lambda: clock["now"],
        retry_after_seconds=3_600,
        max_age_seconds=7 * 24 * 3_600,
    )

    for _hour in range(8):
        service.run_due()
        clock["now"] += 3_601

    # Attempts at 0h, 1h, then after 2h and 4h waits: not one every hour.
    assert len(calls) == 4


def test_a_truncated_member_list_keeps_unreached_readers_but_not_a_demoted_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Review finding: a truncated refresh unioned every earlier reader back in,
    # so a member since made a guest, or deactivated, kept reading.
    from core.domain.knowledge import record_workspace_readers, workspace_readers

    assert record_workspace_readers("slack", ["U_PAST_LAST_PAGE", "U_NOW_GUEST", "U_GONE", "U1"])
    page: list[dict[str, Any]] = [
        {"id": "U1"},
        {"id": "U_NOW_GUEST", "is_guest": True},
        {"id": "U_GONE", "deleted": True},
        {"id": "U_NEW"},
    ]
    asked: list[dict[str, Any]] = []

    def fetch(_target: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], str, bool]:
        asked.append(kwargs)
        return page, "", True

    monkeypatch.setattr(scan_module, "fetch_team_members", fetch)

    assert scan_module.refresh_slack_readers(_TARGET)

    assert workspace_readers() == {"U_PAST_LAST_PAGE", "U1", "U_NEW"}
    assert asked[0]["include_deleted"]  # a deactivated member must be seen to be dropped


@pytest.mark.parametrize("reply", ["RAISE", "Sorry, I cannot help with that."])
def test_a_model_failure_keeps_the_last_overview_instead_of_erasing_it(
    monkeypatch: pytest.MonkeyPatch, reply: str
) -> None:
    # Review finding: a failed summary read as an empty one, so the next scan
    # rewrote the profile without its overview and reported success.
    _install_workspace(monkeypatch, [])
    good = '{"overview": "Acme runs a payments platform.", "channels": {}}'
    assert scan_module.scan_slack_workspace(summarize=lambda _p: good, target=_TARGET).ok

    def failing(_prompt: str) -> str:
        if reply == "RAISE":
            raise RuntimeError("provider down")
        return reply

    outcome = scan_module.scan_slack_workspace(summarize=failing, target=_TARGET)

    assert not outcome.ok and outcome.written == ()
    organization = load_workspace_entry(ORGANIZATION_SLUG)
    assert organization is not None and "Acme runs a payments platform." in organization.body


def test_a_failed_scan_is_retried_even_when_its_entries_look_fresh() -> None:
    # A failure that kept the last good entries leaves nothing missing or stale.
    calls: list[str] = []
    oks = iter([False, True])
    scanner = _scanner([], [])

    def scan(summarize: Any) -> ScanOutcome:
        calls.append("slack")
        written = scanner.scan(summarize)
        return ScanOutcome("slack", ok=next(oks), written=written.written)

    clock = {"now": time.time()}
    service = KnowledgeScanService(
        scanners=(Scanner("slack", available=lambda: True, scan=scan, writes=scanner.writes),),
        summarizer_factory=lambda: None,
        clock=lambda: clock["now"],
        retry_after_seconds=60,
    )

    for _pass in range(3):
        service.run_due()
        clock["now"] += 61

    assert calls == ["slack", "slack"]  # failed → retried; succeeded → left until stale


def test_deleting_any_entry_a_scan_writes_makes_it_due_again() -> None:
    # Review finding: only the integration entry was checked, so a deleted
    # organization profile stayed missing until the week-old refresh.
    from core.domain.knowledge import delete_workspace_entry, save_workspace_entry
    from core.domain.memory import MemoryType

    calls: list[str] = []

    def scan(_summarize: Any) -> ScanOutcome:
        calls.append("slack")
        for slug, memory_type in (
            (ORGANIZATION_SLUG, MemoryType.ORGANIZATION),
            ("integration-slack", MemoryType.INTEGRATION),
        ):
            save_workspace_entry(slug=slug, memory_type=memory_type, description=slug, body="x")
        return ScanOutcome("slack", ok=True, written=(ORGANIZATION_SLUG, "integration-slack"))

    clock = {"now": time.time()}
    service = KnowledgeScanService(
        scanners=(
            Scanner(
                "slack",
                available=lambda: True,
                scan=scan,
                writes=(ORGANIZATION_SLUG, "integration-slack"),
            ),
        ),
        summarizer_factory=lambda: None,
        clock=lambda: clock["now"],
        retry_after_seconds=60,
    )
    service.run_due()
    assert delete_workspace_entry(ORGANIZATION_SLUG)
    clock["now"] += 61

    service.run_due()

    assert calls == ["slack", "slack"]
    assert load_workspace_entry(ORGANIZATION_SLUG) is not None


def test_a_scan_asked_for_in_the_gateway_runs_on_its_loop_not_in_the_turn() -> None:
    # Review finding: `/knowledge scan` in a Slack turn ran a full scan inline,
    # with no backoff, once per message that asked.
    from integrations.knowledge_scans import install_scan_service, scan_on_request

    calls: list[str] = []
    service = KnowledgeScanService(scanners=(_scanner([], calls),), summarizer_factory=lambda: None)
    install_scan_service(service)
    try:
        outcomes, unknown = scan_on_request(["slack", "pagerduty"], force=False)
    finally:
        install_scan_service(None)

    assert (outcomes, unknown, calls) == (None, ["pagerduty"], [])
    service.run_due()
    assert calls == ["slack"]


def test_readers_are_refreshed_every_few_hours_not_every_pass() -> None:
    refreshes: list[float] = []
    clock = {"now": time.time()}
    scanner = _scanner([], [])

    def refresh_readers() -> bool:
        refreshes.append(clock["now"])
        return True

    service = KnowledgeScanService(
        scanners=(
            Scanner(
                scanner.service,
                available=scanner.available,
                scan=scanner.scan,
                writes=scanner.writes,
                refresh_readers=refresh_readers,
            ),
        ),
        summarizer_factory=lambda: None,
        clock=lambda: clock["now"],
    )

    for _hour in range(7):
        service.run_due()
        clock["now"] += 3_600

    assert len(refreshes) == 2  # at 0h and at 6h
