"""Tests for the first-experience demo picker."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from rich.console import Console

import surfaces.interactive_shell.runtime.startup.demo_picker as demo_picker
from core.agent_harness.prompts.getting_started import GETTING_STARTED_MENU
from core.agent_harness.prompts.skills import getting_started_skills
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


def _offerable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    marker = tmp_path / "onboarding_demo.json"
    monkeypatch.setattr(demo_picker, "marker_path", lambda: marker)
    monkeypatch.setattr(demo_picker, "is_test_run", lambda: False)
    monkeypatch.setattr(demo_picker, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(demo_picker, "capture_onboarding_demo_prompted", lambda: None)
    monkeypatch.setattr(demo_picker, "capture_onboarding_demo_skipped", lambda: None)
    monkeypatch.setattr(demo_picker, "capture_onboarding_demo_selected", lambda **_kw: None)
    monkeypatch.setattr(demo_picker, "scan_workspace", lambda *_a, **_kw: _SNAPSHOT)
    monkeypatch.setattr(demo_picker, "resolve_github_token", lambda _token: "tok")
    return marker


def _answers(monkeypatch: pytest.MonkeyPatch, *values: str | None) -> list[dict]:
    """Script successive menu answers and record each menu's choices."""
    calls: list[dict] = []
    answers: Iterator[str | None] = iter(values)

    def choose(**kwargs: object) -> str | None:
        calls.append(kwargs)
        return next(answers)

    monkeypatch.setattr(demo_picker, "repl_choose_one", choose)
    return calls


def _analysis_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[tuple[str, str]]:
    """Fake the GitHub read; returns the (owner, repo) pairs analyzed."""
    from datetime import UTC, datetime

    from integrations.github.tools.ci_analytics.analysis import Analysis
    from integrations.github.tools.ci_analytics.metrics import compute_report

    _offerable(monkeypatch, tmp_path)
    analyzed: list[tuple[str, str]] = []

    def analyze(owner: str, repo: str, **_kw: object) -> Analysis:
        analyzed.append((owner, repo))
        report = compute_report(
            owner=owner,
            repo=repo,
            default_branch="main",
            window_days=30,
            branch_runs=[],
            pr_runs=[],
            merged_prs=(),
            now=datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
        )
        return Analysis(report=report, runs_read=0)

    monkeypatch.setattr(demo_picker, "analyze_repository", analyze)
    return analyzed


def test_first_menu_always_shows_the_getting_started_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _offerable(monkeypatch, tmp_path)
    calls = _answers(monkeypatch, None)
    session = Session()

    demo_picker.offer_demo(session, None)

    labels = [label for _value, label in calls[0]["choices"]]
    assert labels == list(GETTING_STARTED_MENU)
    assert calls[0].get("note") == demo_picker._MENU_EXPLAINER
    assert tuple(suggestion.label for suggestion in demo_picker.DEMO_SUGGESTIONS) == (
        GETTING_STARTED_MENU[0],
        GETTING_STARTED_MENU[1],
        GETTING_STARTED_MENU[2],
    )
    assert tuple(suggestion.skill for suggestion in demo_picker.DEMO_SUGGESTIONS) == tuple(
        skill.name for skill in getting_started_skills()
    )
    assert demo_picker.DEMO_SUGGESTIONS[0].option == demo_picker.OPTION_CI_ANALYTICS
    assert demo_picker.DEMO_SUGGESTIONS[0].skill == "cicd-analytics-demo"
    assert demo_picker.DEMO_SUGGESTIONS[0].prompt == ""
    assert demo_picker.DEMO_SUGGESTIONS[1].skill == "cicd-reliability-agent"
    assert demo_picker.DEMO_SUGGESTIONS[2].skill == "slack-handoff"


def test_unmapped_getting_started_skill_does_not_crash_the_picker() -> None:
    from core.agent_harness.prompts.skills.loader import ActionSkill

    skill = ActionSkill(
        name="future-demo",
        description="x",
        path=Path("."),
        getting_started="A future demo",
        demo_order=9,
    )

    suggestion = demo_picker._suggestion_from_skill(skill)

    assert suggestion.option == "future_demo"
    assert suggestion.prompt == "A future demo"
    assert suggestion.skill == "future-demo"


def test_dismissing_the_demo_does_not_leave_the_explainer_in_the_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _offerable(monkeypatch, tmp_path)
    _answers(monkeypatch, None)
    session = Session()
    console, buf = _capture()

    demo_picker.offer_demo(session, console)

    assert "toy example" not in buf.getvalue()
    assert "real GitHub repositories" not in buf.getvalue()


def test_analytics_demo_scans_asks_analyzes_and_offers_the_next_step_without_a_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange: pick the analytics demo, a repository, then exit at the final menu.
    marker = _offerable(monkeypatch, tmp_path)
    analyzed = _analysis_ready(monkeypatch, tmp_path)
    calls = _answers(monkeypatch, demo_picker.OPTION_CI_ANALYTICS, "me/mine", "exit")
    session = Session()
    console, buf = _capture()

    # Act
    queued = demo_picker.offer_demo(session, console)

    # Assert: chart, menus, report, headline, and the demo's own next-step menu; no prompt.
    assert queued is False
    assert analyzed == [("me", "mine")]
    output = buf.getvalue()
    assert "live snapshot built from your machine" in output
    assert "Activity (commits, last 30 days)" in output
    assert "CI/CD reliability for me/mine, last 30 days" in output
    assert "No CI failures were found in the last 30 days." in output
    assert [c["title"] for c in calls] == [
        "Which demo would you like me to run? (Esc to skip)",
        "Which repository should I analyze?",
        "What would you like to do next?",
    ]
    assert all(c["header"] == "Ask User" for c in calls)
    repo_choices = [value for value, _label in calls[1]["choices"]]
    assert repo_choices == ["me/mine", "acme/busy", demo_picker.EXAMPLE_REPOSITORY, "custom"]
    next_values = [value for value, _label in calls[2]["choices"]]
    assert next_values == ["agent", "slack", "exit"]
    assert session.terminal.pending_prompt_autosubmit is False
    assert json.loads(marker.read_text())["option"] == demo_picker.OPTION_CI_ANALYTICS


def test_analytics_demo_chains_into_the_agent_demo_without_asking_the_repository_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    schedule_calls = _agent_demo_ready(monkeypatch, tmp_path)
    _analysis_ready(monkeypatch, tmp_path)
    calls = _answers(
        monkeypatch, demo_picker.OPTION_CI_ANALYTICS, "me/mine", "agent", "weekdays", "exit"
    )
    session = Session()
    console, _buf = _capture()

    demo_picker.offer_demo(session, console)

    titles = [c["title"] for c in calls]
    assert titles == [
        "Which demo would you like me to run? (Esc to skip)",
        "Which repository should I analyze?",
        "What would you like to do next?",
        "When should it run?",
        "What would you like to do next?",
    ]
    assert schedule_calls[0]["owner"] == "me" and schedule_calls[0]["repo"] == "mine"


def test_analytics_demo_hands_off_to_slack_when_chosen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _analysis_ready(monkeypatch, tmp_path)
    _answers(monkeypatch, demo_picker.OPTION_CI_ANALYTICS, "me/mine", "slack")
    session = Session()
    console, _buf = _capture()

    queued = demo_picker.offer_demo(session, console)

    assert queued is True
    assert "Slack" in session.terminal.pending_prompt_default


def test_analytics_demo_reports_a_failed_read_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from integrations.github import GitHubApiError

    _offerable(monkeypatch, tmp_path)

    def failing(*_a: object, **_k: object) -> object:
        raise GitHubApiError("boom", status_code=401)

    monkeypatch.setattr(demo_picker, "analyze_repository", failing)
    _answers(monkeypatch, demo_picker.OPTION_CI_ANALYTICS, "me/mine")
    session = Session()
    console, buf = _capture()

    queued = demo_picker.offer_demo(session, console)

    assert queued is False
    text = " ".join(buf.getvalue().split())
    assert "Could not read the GitHub Actions history of me/mine" in text
    assert "boom" not in text


def test_analytics_demo_stops_with_setup_hint_when_no_github_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = _offerable(monkeypatch, tmp_path)
    monkeypatch.setattr(demo_picker, "resolve_github_token", lambda _token: "")
    calls = _answers(monkeypatch, demo_picker.OPTION_CI_ANALYTICS)
    session = Session()
    console, buf = _capture()

    queued = demo_picker.offer_demo(session, console)

    assert queued is False
    assert len(calls) == 1
    assert "opensre integrations setup github" in buf.getvalue()
    assert not session.terminal.pending_prompt_default
    assert not marker.is_file()
    assert demo_picker.should_offer_demo() is True


def test_cancelled_repository_pick_does_not_record_the_demo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = _offerable(monkeypatch, tmp_path)
    _answers(monkeypatch, demo_picker.OPTION_CI_ANALYTICS, None)
    session = Session()

    queued = demo_picker.offer_demo(session, None)

    assert queued is False
    assert not session.terminal.pending_prompt_default
    assert not marker.is_file()
    assert demo_picker.should_offer_demo() is True


def test_slack_option_queues_the_slack_handoff_skill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = _offerable(monkeypatch, tmp_path)
    _answers(monkeypatch, demo_picker.OPTION_SLACK)
    session = Session()

    queued = demo_picker.offer_demo(session, None)

    assert queued is True
    assert session.terminal.pending_prompt_default == GETTING_STARTED_MENU[2]
    assert session.terminal.pending_prompt_plain_turn is False
    assert json.loads(marker.read_text())["option"] == demo_picker.OPTION_SLACK
    assert demo_picker.DEMO_SUGGESTIONS[2].skill == "slack-handoff"


def test_other_demos_queue_their_prompt_directly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _offerable(monkeypatch, tmp_path)
    _answers(monkeypatch, demo_picker.OPTION_SLACK)
    session = Session()

    assert demo_picker.offer_demo(session, None) is True
    assert "Slack" in session.terminal.pending_prompt_default
    assert session.terminal.pending_prompt_plain_turn is False


def test_typed_answer_is_submitted_verbatim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _offerable(monkeypatch, tmp_path)
    _answers(monkeypatch, "show me my flaky tests")
    session = Session()

    assert demo_picker.offer_demo(session, None) is True
    assert session.terminal.pending_prompt_default == "show me my flaky tests"


def test_custom_option_closes_the_demo_and_opens_the_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = _offerable(monkeypatch, tmp_path)
    calls = _answers(monkeypatch, "custom")
    session = Session()

    queued = demo_picker.offer_demo(session, None)

    assert queued is False
    assert not session.terminal.pending_prompt_default
    assert json.loads(marker.read_text())["option"] == "custom"
    assert calls[0].get("custom_label") is None
    assert demo_picker.should_offer_demo() is True


def test_skip_records_the_marker_but_the_next_launch_still_offers_the_picker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = _offerable(monkeypatch, tmp_path)
    _answers(monkeypatch, None)
    session = Session()

    first = demo_picker.offer_demo(session, None)

    assert first is False
    assert not session.terminal.pending_prompt_default
    assert marker.is_file()
    assert demo_picker.should_offer_demo() is True


def test_recorded_choice_does_not_hide_the_picker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = _offerable(monkeypatch, tmp_path)
    marker.write_text('{"option": "skipped"}')
    _answers(monkeypatch, demo_picker.OPTION_SLACK)
    session = Session()

    assert demo_picker.offer_demo(session, None) is True


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

    _offerable(monkeypatch, tmp_path)
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

    monkeypatch.setattr(demo_picker, "schedule_ci_reliability_loop", schedule)
    monkeypatch.setattr(
        demo_picker, "background_service_state", _service_state_factory(installed=True)
    )
    monkeypatch.setattr(demo_picker, "local_timezone", lambda: "UTC")
    monkeypatch.setattr(demo_picker, "reload_loop_scheduler", lambda: 1)
    monkeypatch.setattr(demo_picker, "run_loop_now", lambda _task_id: True)
    monkeypatch.setattr(demo_picker, "get_loop_messages", messages)
    return calls


def test_agent_demo_schedules_the_loop_runs_it_once_and_shows_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange: pick the agent demo, the user's repository, weekdays, then exit.
    calls = _agent_demo_ready(monkeypatch, tmp_path)
    menus = _answers(monkeypatch, demo_picker.OPTION_CI_AGENT, "me/mine", "weekdays", "exit")
    session = Session()
    console, buf = _capture()

    # Act
    queued = demo_picker.offer_demo(session, console)

    # Assert: loop created for the pick, weekdays at 08:00, card and first report shown.
    assert queued is False
    assert calls == [
        {"owner": "me", "repo": "mine", "time_text": "08:00", "weekdays": True, "timezone": "UTC"}
    ]
    titles = [menu["title"] for menu in menus]
    assert titles == [
        "Which demo would you like me to run? (Esc to skip)",
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
    monkeypatch.setattr(demo_picker, "get_loop_messages", lambda **_kw: [refusal])
    _answers(monkeypatch, demo_picker.OPTION_CI_AGENT, "me/mine", "weekdays", "exit")
    session = Session()
    console, buf = _capture()

    # Act
    demo_picker.offer_demo(session, console)

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
        demo_picker, "background_service_state", _service_state_factory(installed=False)
    )
    installs: list[bool] = []

    def install() -> BackgroundServiceState:
        installs.append(True)
        return BackgroundServiceState("Darwin", True, True, tmp_path / "unit", tmp_path / "log")

    monkeypatch.setattr(demo_picker, "install_background_service", install)
    menus = _answers(
        monkeypatch, demo_picker.OPTION_CI_AGENT, "me/mine", "weekdays", "service", "exit"
    )
    session = Session()
    console, buf = _capture()

    # Act
    demo_picker.offer_demo(session, console)

    # Assert: the service row led the menu, the install ran once, then the menu came back.
    first_next = [value for value, _label in menus[3]["choices"]]
    assert first_next[0] == "service"
    assert installs == [True]
    assert "Background scheduler installed" in " ".join(buf.getvalue().split())
    assert menus[4]["title"] == "What would you like to do next?"


def test_agent_demo_hands_off_to_the_slack_demo_when_chosen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _agent_demo_ready(monkeypatch, tmp_path)
    _answers(monkeypatch, demo_picker.OPTION_CI_AGENT, "me/mine", "daily", "slack")
    session = Session()
    console, _buf = _capture()

    queued = demo_picker.offer_demo(session, console)

    assert queued is True
    assert session.terminal.pending_prompt_autosubmit is True
    assert "Slack" in session.terminal.pending_prompt_default


def test_agent_demo_rejects_an_unparseable_custom_time_without_scheduling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _agent_demo_ready(monkeypatch, tmp_path)
    _answers(monkeypatch, demo_picker.OPTION_CI_AGENT, "me/mine", "half past eight")
    session = Session()
    console, buf = _capture()

    queued = demo_picker.offer_demo(session, console)

    assert queued is False
    assert calls == []
    assert "Could not schedule the check" in buf.getvalue()


def test_agent_demo_stops_with_setup_hint_when_no_github_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _agent_demo_ready(monkeypatch, tmp_path)
    monkeypatch.setattr(demo_picker, "resolve_github_token", lambda _token: "")
    _answers(monkeypatch, demo_picker.OPTION_CI_AGENT)
    session = Session()
    console, buf = _capture()

    queued = demo_picker.offer_demo(session, console)

    assert queued is False
    assert calls == []
    assert "opensre integrations setup github" in " ".join(buf.getvalue().split())


def test_demo_skills_declare_their_tools() -> None:
    from core.agent_harness.prompts.skills.loader import list_action_skills

    by_name = {skill.name: skill for skill in list_action_skills()}
    analytics = by_name["cicd-analytics-demo"]
    agent = by_name["cicd-reliability-agent"]
    slack = by_name["slack-handoff"]

    assert analytics.tools == (
        "scan_local_git_workspace",
        "analyze_github_ci_reliability",
        "schedule_ci_reliability_loop",
        "cli_exec",
        "slash_invoke",
        "ask_user_choice",
    )
    assert agent.tools == (
        "scan_local_git_workspace",
        "schedule_ci_reliability_loop",
        "ask_user_choice",
    )
    assert slack.tools == ("cli_exec", "slash_invoke")
