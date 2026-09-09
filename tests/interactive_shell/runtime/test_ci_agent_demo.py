"""The suggested-loops CI demo retains its scheduling and delivery behavior."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from rich.console import Console

import surfaces.interactive_shell.runtime.startup.ci_agent_demo as ci_agent_demo
from surfaces.interactive_shell.session import Session
from tools.system.workspace_git_scan.scan import RepoActivity, WorkspaceSnapshot


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=120), buf


def _repo(name: str, github: str, *, commits: int, own: int, workflows: bool) -> RepoActivity:
    owner, _, repo = github.partition("/")
    return RepoActivity(
        name=name,
        path=f"/home/u/{name}",
        origin=f"https://github.com/{github}",
        github_owner=owner,
        github_repo=repo,
        commits=commits,
        own_commits=own,
        uncommitted=0,
        has_workflows=workflows,
    )


_SNAPSHOT = WorkspaceSnapshot(
    root="/home/u",
    days=30,
    repos=(
        _repo("busy-team-repo", "acme/busy", commits=900, own=0, workflows=True),
        _repo("mine", "me/mine", commits=120, own=110, workflows=True),
        _repo("no-ci", "me/no-ci", commits=300, own=300, workflows=False),
        _repo("local-only", "", commits=50, own=50, workflows=True),
    ),
)


def _answers(monkeypatch: pytest.MonkeyPatch, *values: str | None) -> list[dict]:
    """Script successive menu answers and record each menu's choices."""
    calls: list[dict] = []
    answers: Iterator[str | None] = iter(values)

    def choose(**kwargs: object) -> str | None:
        calls.append(kwargs)
        return next(answers)

    monkeypatch.setattr(ci_agent_demo, "repl_choose_one", choose)
    return calls


def _scheduled_stub(owner: str, repo: str) -> object:
    from infrastructure.scheduling.scheduler.loop_constants import LOOP_PROMPT_PARAM
    from infrastructure.scheduling.scheduler.loops import ManualLoop
    from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
    from integrations.github.tools.ci_analytics.loop import ScheduledLoop, loop_name, loop_prompt

    task = ScheduledTask(
        id="loop1",
        name=loop_name(owner, repo),
        kind=TaskKind.MANUAL_LOOP,
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider=Provider.INTERACTIVE_SHELL,
        window_hours=24,
        enabled=True,
        params={LOOP_PROMPT_PARAM: loop_prompt(owner, repo)},
    )
    loop = ManualLoop(
        task=task, channels=(Provider.INTERACTIVE_SHELL,), next_run="2026-09-08 08:00"
    )
    return ScheduledLoop(loop=loop, reused=False)


def _service_state_factory(*, installed: bool, supported: bool = True) -> object:
    from infrastructure.scheduling.scheduler.background_service import BackgroundServiceState

    def state() -> BackgroundServiceState:
        return BackgroundServiceState("Darwin", supported, installed, None, None)

    return state


def _agent_demo_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, object]]:
    """Fake the scheduler side of the agent demo; returns the schedule calls made."""
    from infrastructure.scheduling.scheduler.local_delivery import LocalLoopMessage

    monkeypatch.setattr(ci_agent_demo, "scan_workspace", lambda *_a, **_kw: _SNAPSHOT)
    monkeypatch.setattr(ci_agent_demo, "resolve_github_token", lambda _token: "tok")
    monkeypatch.setattr(ci_agent_demo, "marker_path", lambda: tmp_path / "onboarding_demo.json")
    calls: list[dict[str, object]] = []

    def schedule(owner: str, repo: str, **kwargs: object) -> object:
        calls.append({"owner": owner, "repo": repo, **kwargs})
        return _scheduled_stub(owner, repo)

    def messages(limit: int = 20) -> list[LocalLoopMessage]:
        return [
            LocalLoopMessage(
                message_id="local:1",
                task_id="loop1",
                loop_id="loop1",
                name="CI reliability check",
                created_at="2026-09-07T18:00:00+00:00",
                message="**CI/CD reliability for me/mine, last 7 days**",
                prompt="",
            )
        ]

    monkeypatch.setattr(ci_agent_demo, "schedule_ci_reliability_loop", schedule)
    monkeypatch.setattr(
        ci_agent_demo, "background_service_state", _service_state_factory(installed=True)
    )
    monkeypatch.setattr(ci_agent_demo, "local_timezone", lambda: "UTC")
    monkeypatch.setattr(ci_agent_demo, "reload_loop_scheduler", lambda: 1)
    monkeypatch.setattr(ci_agent_demo, "run_loop_now", lambda _task_id: True)
    monkeypatch.setattr(ci_agent_demo, "get_loop_messages", messages)
    return calls


def test_agent_demo_schedules_the_loop_runs_it_once_and_shows_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange: pick the agent demo, the user's repository, weekdays, then exit.
    calls = _agent_demo_ready(monkeypatch, tmp_path)
    menus = _answers(monkeypatch, "me/mine", "weekdays", "exit")
    session = Session()
    console, buf = _capture()

    # Act
    queued = ci_agent_demo.start_ci_agent_demo(session, console)

    # Assert: loop created for the pick, weekdays at 08:00, card and first report shown.
    assert queued is False
    assert calls == [
        {"owner": "me", "repo": "mine", "time_text": "08:00", "weekdays": True, "timezone": "UTC"}
    ]
    titles = [menu["title"] for menu in menus]
    assert titles == [
        "Which repository should the agent watch?",
        "When should it run?",
        "What would you like to do next?",
    ]
    assert all(menu["header"] == "Ask User" for menu in menus)
    output = buf.getvalue()
    assert "Scheduled: CI reliability check · me/mine" in output
    assert "/loops messages" in output
    assert "CI/CD reliability for me/mine, last 7 days" in output
    assert "Demo exited" in output
    assert session.terminal.pending_prompt_autosubmit is False


def test_agent_demo_warns_when_the_first_pass_skipped_the_analysis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange: the loop ran, but the model answered without the analytics header.
    from infrastructure.scheduling.scheduler.local_delivery import LocalLoopMessage

    _agent_demo_ready(monkeypatch, tmp_path)
    refusal = LocalLoopMessage(
        message_id="local:2",
        task_id="loop1",
        loop_id="loop1",
        name="CI reliability check",
        created_at="2026-09-07T18:00:00+00:00",
        message="Report unavailable: no report scope was provided.",
        prompt="",
    )
    monkeypatch.setattr(ci_agent_demo, "get_loop_messages", lambda **_kw: [refusal])
    _answers(monkeypatch, "me/mine", "weekdays", "exit")
    session = Session()
    console, buf = _capture()

    # Act
    ci_agent_demo.start_ci_agent_demo(session, console)

    # Assert: the refusal is not shown as the report; the retry command names the loop.
    output = " ".join(buf.getvalue().split())
    assert "Report unavailable" not in output
    assert "/loops run loop1" in output


def test_agent_demo_offers_the_background_service_and_installs_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange: no service yet; the user picks it, then exits.
    from infrastructure.scheduling.scheduler.background_service import BackgroundServiceState

    _agent_demo_ready(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ci_agent_demo, "background_service_state", _service_state_factory(installed=False)
    )
    installs: list[bool] = []

    def install() -> BackgroundServiceState:
        installs.append(True)
        return BackgroundServiceState("Darwin", True, True, tmp_path / "unit", tmp_path / "log")

    monkeypatch.setattr(ci_agent_demo, "install_background_service", install)
    menus = _answers(monkeypatch, "me/mine", "weekdays", "service", "exit")
    session = Session()
    console, buf = _capture()

    # Act
    ci_agent_demo.start_ci_agent_demo(session, console)

    # Assert: the service row led the menu, the install ran once, then the menu came back.
    first_next = [value for value, _label in menus[2]["choices"]]
    assert first_next[0] == "service"
    assert installs == [True]
    assert "Background scheduler installed" in " ".join(buf.getvalue().split())
    assert menus[3]["title"] == "What would you like to do next?"


def test_agent_demo_hands_off_to_the_slack_demo_when_chosen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _agent_demo_ready(monkeypatch, tmp_path)
    _answers(monkeypatch, "me/mine", "daily", "slack")
    session = Session()
    console, _buf = _capture()

    queued = ci_agent_demo.start_ci_agent_demo(session, console)

    assert queued is True
    assert session.terminal.pending_prompt_autosubmit is True
    assert "Slack" in session.terminal.pending_prompt_default


def test_agent_demo_rejects_an_unparseable_custom_time_without_scheduling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _agent_demo_ready(monkeypatch, tmp_path)
    _answers(monkeypatch, "me/mine", "half past eight")
    session = Session()
    console, buf = _capture()

    queued = ci_agent_demo.start_ci_agent_demo(session, console)

    assert queued is False
    assert calls == []
    assert "Could not schedule the check" in buf.getvalue()


def test_agent_demo_stops_with_setup_hint_when_no_github_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _agent_demo_ready(monkeypatch, tmp_path)
    monkeypatch.setattr(ci_agent_demo, "resolve_github_token", lambda _token: "")
    _answers(monkeypatch)
    session = Session()
    console, buf = _capture()

    queued = ci_agent_demo.start_ci_agent_demo(session, console)

    assert queued is False
    assert calls == []
    assert "opensre integrations setup github" in " ".join(buf.getvalue().split())
