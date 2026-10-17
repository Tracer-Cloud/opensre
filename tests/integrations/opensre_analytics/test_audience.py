"""OpenSRE Analytics never reaches a conversation outside the OpenSRE team.

The figures are Tracer's own product analytics. Each test proves that no
analytics are read or posted for an audience that is, or may be, external.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import infrastructure.scheduling.scheduler as scheduler_pkg
import infrastructure.scheduling.scheduler.loops as loops
from infrastructure.scheduling.scheduler.outcomes import WorkStatus
from infrastructure.scheduling.scheduler.run_activity import collect_run_activity
from infrastructure.scheduling.scheduler.storage import list_tasks
from infrastructure.scheduling.scheduler.types import Provider
from integrations.manual_loop_runner import run_manual_prompt_loop
from integrations.opensre_analytics import audience, report
from integrations.opensre_analytics.tools.opensre_analytics_growth_tool import tool as growth_tool
from integrations.opensre_analytics.tools.opensre_analytics_report_loop_tool import (
    tool as schedule_tool,
)


@pytest.fixture(autouse=True)
def _scheduler_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "opensre-home"
    for info in pkgutil.walk_packages(scheduler_pkg.__path__, f"{scheduler_pkg.__name__}."):
        module = importlib.import_module(info.name)
        if hasattr(module, "OPENSRE_HOME_DIR"):
            monkeypatch.setattr(module, "OPENSRE_HOME_DIR", home)
    monkeypatch.setattr(loops, "resolve_slack_credentials", lambda _p: {"access_token": "xoxb"})
    monkeypatch.setattr(audience, "resolve_bot_token", _bot_token)


def _bot_token() -> tuple[SimpleNamespace, str]:
    return SimpleNamespace(bot_token="xoxb-test"), ""


class _Reads:
    """Records every attempt to read analytics."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("analytics must not be read for this audience")


def _sharing(shared: bool | None) -> Any:
    def answer(_target: object, _channel: str) -> tuple[bool | None, str]:
        return shared, "" if shared is not None else "missing_scope"

    return answer


@pytest.mark.parametrize(
    ("platform", "shared"),
    [
        pytest.param("telegram", False, id="telegram"),
        pytest.param("slack", True, id="slack-connect-channel"),
        pytest.param("slack", None, id="slack-unconfirmed-fails-closed"),
    ],
)
def test_no_analytics_are_read_for_a_conversation_that_may_be_external(
    monkeypatch: pytest.MonkeyPatch, platform: str, shared: bool | None
) -> None:
    # Arrange
    reads = _Reads()
    monkeypatch.setattr(growth_tool, "collect_growth", reads)
    monkeypatch.setattr(audience, "conversation_externally_shared", _sharing(shared))

    # Act
    result = growth_tool.opensre_analytics_daily_growth(
        analytics_token="token", origin_platform=platform, origin_chat_id="C0CHAT"
    )

    # Assert
    assert result["available"] is False
    assert "internal to the OpenSRE team" in str(result["error"])
    assert reads.calls == 0


def test_a_scheduled_model_turn_cannot_read_analytics_whatever_its_loop_posts_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: a generic scheduled prompt has no chat stamp, so it looks local.
    reads = _Reads()
    monkeypatch.setattr(growth_tool, "collect_growth", reads)

    # Act
    with collect_run_activity():
        result = growth_tool.opensre_analytics_daily_growth(analytics_token="token")

    # Assert
    assert result["available"] is False
    assert reads.calls == 0


def test_scheduling_into_a_channel_shared_with_another_organization_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(schedule_tool, "resolve_bot_token", _bot_token)
    monkeypatch.setattr(schedule_tool, "resolve_channel_id", lambda _t, _c: ("C0SHARED", ""))
    monkeypatch.setattr(audience, "conversation_externally_shared", _sharing(True))

    # Act
    result = schedule_tool.schedule_analytics_growth_report(
        channel="#customer-acme", origin_platform="slack", origin_chat_id="D0DM"
    )

    # Assert
    assert result["ok"] is False
    assert list_tasks() == []


def test_a_tick_after_its_channel_became_shared_posts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: scheduled into an internal channel that is later shared.
    monkeypatch.setattr(audience, "conversation_externally_shared", _sharing(False))
    scheduled = report.schedule_growth_report(
        report.GrowthReportSchedule(provider=Provider.SLACK.value, chat_id="C0PRODUCT")
    )
    monkeypatch.setattr(audience, "conversation_externally_shared", _sharing(True))
    reads = _Reads()
    monkeypatch.setattr(report, "collect_growth", reads)

    # Act
    delivered = run_manual_prompt_loop({"task_id": scheduled.task.id, **scheduled.task.params})

    # Assert: an empty body is not delivered, and no figures were read.
    assert delivered == ""
    assert delivered.outcome.status is WorkStatus.BLOCKED
    assert reads.calls == 0
