"""Scheduling the daily growth report from Slack, and what each tick delivers."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import infrastructure.scheduling.scheduler as scheduler_pkg
import infrastructure.scheduling.scheduler.loops as loops
from infrastructure.scheduling.scheduler.delivery_plan import configured_target_keys
from infrastructure.scheduling.scheduler.outcomes import WorkStatus
from infrastructure.scheduling.scheduler.storage import get_task, list_tasks
from infrastructure.scheduling.scheduler.types import Provider
from integrations.manual_loop_runner import run_manual_prompt_loop
from integrations.opensre_analytics import audience, report
from integrations.opensre_analytics.tools.opensre_analytics_report_loop_tool import tool


@pytest.fixture(autouse=True)
def _scheduler_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every scheduler module that holds the home directory at ``tmp_path``."""
    home = tmp_path / "opensre-home"
    for info in pkgutil.walk_packages(scheduler_pkg.__path__, f"{scheduler_pkg.__name__}."):
        module = importlib.import_module(info.name)
        if hasattr(module, "OPENSRE_HOME_DIR"):
            monkeypatch.setattr(module, "OPENSRE_HOME_DIR", home)
    monkeypatch.setattr(loops, "resolve_slack_credentials", _slack_bot_credentials)


@pytest.fixture(autouse=True)
def _internal_slack(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every Slack conversation in these tests is internal to the workspace."""
    monkeypatch.setattr(audience, "resolve_bot_token", _bot_token)
    monkeypatch.setattr(audience, "conversation_externally_shared", lambda _t, _c: (False, ""))


def _slack_bot_credentials(_params: dict[str, str]) -> dict[str, str]:
    return {"access_token": "xoxb-test"}


def _bot_token() -> tuple[SimpleNamespace, str]:
    return SimpleNamespace(bot_token="xoxb-test"), ""


def _product_chat_id(_target: object, channel: str) -> tuple[str, str]:
    assert channel.removeprefix("#") == "product-chat"
    return "C0PRODUCT", ""


def _growth_loops() -> list[str]:
    return [task.id for task in list_tasks() if task.params.get("loop_report")]


def test_a_dm_request_posts_to_the_named_channel_and_asking_again_moves_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: a Slack DM turn naming the product channel.
    monkeypatch.setattr(tool, "resolve_bot_token", _bot_token)
    monkeypatch.setattr(tool, "resolve_channel_id", _product_chat_id)
    monkeypatch.setattr(tool, "join_channel", lambda _target, **_kw: (True, ""))

    # Act
    first = tool.schedule_analytics_growth_report(
        time="09:00",
        timezone="Asia/Kolkata",
        channel="#product-chat",
        origin_platform="slack",
        origin_chat_id="D0DM",
    )
    second = tool.schedule_analytics_growth_report(
        time="08:00",
        timezone="Asia/Kolkata",
        channel="product-chat",
        origin_platform="slack",
        origin_chat_id="D0DM",
    )

    # Assert: delivery goes to the channel, not the DM, and one loop is moved.
    assert first["ok"] is True, first
    assert second["ok"] is True, second
    assert second["rescheduled"] is True
    assert _growth_loops() == [first["loop_id"]]
    task = get_task(first["loop_id"])
    assert task is not None
    assert task.cron == "0 8 * * *"
    assert task.timezone == "Asia/Kolkata"
    assert configured_target_keys(task) == ((Provider.SLACK, "C0PRODUCT"),)
    assert second["delivers_to"] == "#product-chat"


def test_without_a_channel_the_report_posts_back_to_the_asking_chat() -> None:
    # Act
    result = tool.schedule_analytics_growth_report(
        time="9am", origin_platform="slack", origin_chat_id="C0ASKING"
    )

    # Assert
    assert result["ok"] is True, result
    task = get_task(result["loop_id"])
    assert task is not None
    assert configured_target_keys(task) == ((Provider.SLACK, "C0ASKING"),)


def test_an_unknown_timezone_is_refused_before_anything_is_stored() -> None:
    result = tool.schedule_analytics_growth_report(
        time="09:00", timezone="IST", origin_platform="slack", origin_chat_id="C0ASKING"
    )

    assert result["ok"] is False
    assert "IANA" in result["error"]
    assert _growth_loops() == []


def test_a_tick_without_the_integration_delivers_a_blocked_notice_not_a_report_of_zeros(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the scheduled loop the tool stores, with no analytics connection.
    import infrastructure.harness_providers as harness_providers

    monkeypatch.setattr(harness_providers, "resolve_integrations", lambda: {})
    scheduled = report.schedule_growth_report(report.GrowthReportSchedule())
    payload = {"task_id": scheduled.task.id, **scheduled.task.params}

    # Act: the runner dispatches to the deterministic builder, not a model turn.
    delivered = run_manual_prompt_loop(payload)

    # Assert
    assert delivered.outcome.status is WorkStatus.BLOCKED
    assert "not connected" in delivered
