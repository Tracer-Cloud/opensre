"""CI reliability demo used by the suggested-loops picker."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markdown import Markdown
from rich.markup import escape
from rich.text import Text

from config.constants.paths import OPENSRE_HOME_DIR
from core.agent_harness.spi.grounding import (
    GETTING_STARTED_CUSTOM,
    getting_started_skills,
)
from infrastructure.scheduling.scheduler.background_service import (
    background_service_state,
    install_background_service,
)
from infrastructure.scheduling.scheduler.local_delivery import get_loop_messages
from infrastructure.scheduling.scheduler.loops import parse_loop_time
from infrastructure.terminal.theme import WARNING
from integrations.github import (
    DEFAULT_LOOP_TIME,
    local_timezone,
    loop_card,
    report_looks_complete,
    resolve_github_token,
    schedule_ci_reliability_loop,
)
from surfaces.interactive_shell.runtime.loop_scheduler import reload_loop_scheduler, run_loop_now
from surfaces.interactive_shell.ui.streaming.renderer import render_note_block, reply_gutter
from surfaces.shared.terminal.components.choice_menu import (
    repl_choose_one,
)
from surfaces.shared.terminal.components.loaders import llm_loader
from tools.system.workspace_git_scan.render import snapshot_renderable
from tools.system.workspace_git_scan.scan import RepoActivity, WorkspaceSnapshot, scan_workspace

if TYPE_CHECKING:
    from rich.console import Console

    from surfaces.interactive_shell.session import Session


logger = logging.getLogger(__name__)
MARKER_FILENAME = "onboarding_demo.json"
EXAMPLE_REPOSITORY = "Tracer-Cloud/opensre"
_CUSTOM_LABEL = GETTING_STARTED_CUSTOM
_MENU_HEADER = "Ask User"
_CUSTOM_OPTION = "custom"
_SNAPSHOT_LEAD = "Here's a live snapshot built from your machine:"
_REPOSITORY_TITLE = "Which repository should I analyze?"
_EXAMPLE_LABEL = f"Use the open-source example repository ({EXAMPLE_REPOSITORY})"
_MAX_OWN_REPOSITORIES = 3
_SCAN_DAYS = 30
_LOOP_REPOSITORY_TITLE = "Which repository should the agent watch?"
_LOOP_TIME_TITLE = "When should it run?"
_LOOP_TIME_CUSTOM_LABEL = "Or type a time like 07:30..."
_LOOP_WEEKDAYS = "weekdays"
_LOOP_DAILY = "daily"
_LOOP_TOKEN_MISSING = (
    "The agent reads GitHub Actions history on every run, which needs a GitHub token. "
    "Run `opensre integrations setup github`, then `/demo` to continue."
)
_LOOP_FIRST_PASS_FAILED = (
    "The first pass did not produce the report; the schedule stays in place. "
    "Retry with `/loops run {task_id}` or check `/cron logs {task_id}`."
)
_NEXT_TITLE = "What would you like to do next?"
_NEXT_SERVICE = "service"
_NEXT_SERVICE_LABEL = (
    "Keep it running when this shell is closed (installs a background scheduler service)"
)
_NEXT_SLACK = "slack"
_NEXT_EXIT = "exit"
_NEXT_EXIT_LABEL = "Exit demo"
_SERVICE_INSTALLED = (
    "Background scheduler installed. It starts at login and runs every loop on this machine; "
    "remove it with /loops service remove."
)
_DEMO_EXITED = "Demo exited. Run /demo any time to pick another one."
OPTION_CI_AGENT = "ci_agent"


def marker_path() -> Path:
    return OPENSRE_HOME_DIR / MARKER_FILENAME


def _scan_and_show(console: Console | None) -> WorkspaceSnapshot:
    """Scan the home directory under a spinner and paint the activity chart."""
    home = Path.home()
    if console is not None:
        with llm_loader(console, f"Scanning {escape(str(home))} for git repositories"):
            snapshot = scan_workspace(home, days=_SCAN_DAYS)
    else:
        snapshot = scan_workspace(home, days=_SCAN_DAYS)
    if console is not None:
        # Transcript idiom: the lead is an agent note, the chart hangs in the
        # same gutter, so the demo does not read as a different program.
        console.print()
        render_note_block(console, _SNAPSHOT_LEAD)
        console.print(reply_gutter(snapshot_renderable(snapshot), lead=False))
        console.print()
    return snapshot


def _warn(console: Console | None, text: str) -> None:
    if console is not None:
        console.print(reply_gutter(Text(text, style=str(WARNING)), lead=False))


def start_ci_agent_demo(
    session: Session,
    console: Console | None,
    *,
    repository: str | None = None,
) -> bool:
    """Scan, choose a repository and time, then schedule and run the reliability loop."""
    if repository is None:
        snapshot = _scan_and_show(console)
        if not resolve_github_token(None):
            _warn(console, _LOOP_TOKEN_MISSING)
            return False
        repository = choose_repository(snapshot, title=_LOOP_REPOSITORY_TITLE)
    if repository is None:
        return False
    owner, _, repo = repository.partition("/")
    if not owner or not repo:
        _warn(console, f"{repository!r} is not a GitHub repository; use the owner/name form.")
        return False
    try:
        schedule = choose_loop_time()
        if schedule is None:
            return False
        time_text, weekdays = schedule
        scheduled = schedule_ci_reliability_loop(
            owner, repo, time_text=time_text, weekdays=weekdays, timezone=local_timezone()
        )
    except ValueError as exc:
        _warn(console, f"Could not schedule the check: {exc}")
        return False
    reload_loop_scheduler()
    if console is not None:
        headline, *details = loop_card(scheduled)
        console.print()
        render_note_block(console, headline)
        console.print(reply_gutter(Text("\n".join(details)), lead=False))
        console.print()
    _record(OPTION_CI_AGENT)
    _run_first_pass(console, scheduled.task_id, owner=owner, repo=repo)
    return _offer_after_loop(session, console)


def choose_loop_time() -> tuple[str, bool] | None:
    """Ask when the loop runs: ``(time_text, weekdays)`` or ``None`` when the user escapes."""
    timezone = local_timezone()
    selected = repl_choose_one(
        title=_LOOP_TIME_TITLE,
        choices=[
            (_LOOP_WEEKDAYS, f"Weekdays at {DEFAULT_LOOP_TIME} {timezone} (recommended)"),
            (_LOOP_DAILY, f"Every day at {DEFAULT_LOOP_TIME} {timezone}"),
            (_CUSTOM_OPTION, _LOOP_TIME_CUSTOM_LABEL),
        ],
        custom_label=_LOOP_TIME_CUSTOM_LABEL,
        letter_keys=True,
        header=_MENU_HEADER,
    )
    if selected is None or selected.strip() in {"", _CUSTOM_OPTION, _LOOP_TIME_CUSTOM_LABEL}:
        return None
    if selected == _LOOP_WEEKDAYS:
        return DEFAULT_LOOP_TIME, True
    if selected == _LOOP_DAILY:
        return DEFAULT_LOOP_TIME, False
    parse_loop_time(selected)
    return selected.strip(), True


def _run_first_pass(console: Console | None, task_id: str, *, owner: str, repo: str) -> None:
    """Run the loop once now and show the delivered report from the inbox."""
    if console is not None:
        with llm_loader(console, "Running the first pass now; about a minute"):
            delivered = run_loop_now(task_id)
    else:
        delivered = run_loop_now(task_id)
    report = next((m.message for m in get_loop_messages(limit=20) if m.task_id == task_id), "")
    if not delivered or not report_looks_complete(report, owner, repo):
        _warn(console, _LOOP_FIRST_PASS_FAILED.format(task_id=task_id))
        return
    if console is not None:
        console.print()
        render_note_block(console, "The first report, as it will land in /loops messages:")
        console.print(reply_gutter(Markdown(report), lead=False))
        console.print()


def _offer_after_loop(session: Session, console: Console | None) -> bool:
    """Offer the background service and the Slack demo; ``True`` when a prompt was queued."""
    slack = next(skill for skill in getting_started_skills() if skill.name == "slack-handoff")
    choices = [(_NEXT_SLACK, slack.getting_started or slack.name), (_NEXT_EXIT, _NEXT_EXIT_LABEL)]
    service = background_service_state()
    if service.supported and not service.installed:
        choices.insert(0, (_NEXT_SERVICE, _NEXT_SERVICE_LABEL))
    selected = repl_choose_one(
        title=_NEXT_TITLE, choices=choices, letter_keys=True, header=_MENU_HEADER
    )
    if selected == _NEXT_SERVICE:
        _install_service(console)
        return _offer_after_loop(session, console)
    if selected == _NEXT_SLACK:
        session.terminal.set_auto_command(slack.getting_started or slack.name)
        return True
    if console is not None:
        render_note_block(console, _DEMO_EXITED)
    return False


def _install_service(console: Console | None) -> None:
    try:
        state = install_background_service()
    except RuntimeError as exc:
        _warn(console, f"Could not install the background scheduler: {exc}")
        return
    if console is not None:
        render_note_block(console, _SERVICE_INSTALLED)
        console.print(reply_gutter(Text(f"Log: {state.log_path}"), lead=False))
        console.print()


def choose_repository(snapshot: WorkspaceSnapshot, *, title: str = _REPOSITORY_TITLE) -> str | None:
    """Ask which repository to use; ``None`` when the user escapes."""
    choices = [
        (repo.github_full_name, _candidate_label(repo)) for repo in suitable_repositories(snapshot)
    ]
    choices.append((EXAMPLE_REPOSITORY, _EXAMPLE_LABEL))
    choices.append((_CUSTOM_OPTION, _CUSTOM_LABEL))
    selected = repl_choose_one(
        title=title,
        choices=choices,
        custom_label=_CUSTOM_LABEL,
        letter_keys=True,
        header=_MENU_HEADER,
    )
    if _nothing_chosen(selected):
        return None
    assert selected is not None
    return selected.strip()


def _nothing_chosen(selected: str | None) -> bool:
    """Escape, or the custom row submitted without any typed text."""
    return selected is None or selected.strip() in {"", _CUSTOM_OPTION, _CUSTOM_LABEL}


def suitable_repositories(snapshot: WorkspaceSnapshot) -> list[RepoActivity]:
    """Local GitHub checkouts with workflows, the user's own contributions first."""
    candidates = [repo for repo in snapshot.repos if repo.github_full_name and repo.has_workflows]
    candidates.sort(key=lambda repo: (-repo.own_commits, -repo.commits, repo.name.lower()))
    return [repo for repo in candidates if repo.github_full_name != EXAMPLE_REPOSITORY][
        :_MAX_OWN_REPOSITORIES
    ]


def _candidate_label(repo: RepoActivity) -> str:
    yours = f", {repo.own_commits} by you" if repo.own_commits else ""
    return f"{repo.github_full_name} ({repo.commits} commits{yours}, CI configured)"


def _record(option: str) -> None:
    """Persist the last choice for analytics; the next launch still shows the picker."""
    try:
        path = marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"option": option, "chosen_at": datetime.now(UTC).isoformat()}),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("Could not record the onboarding demo choice.", exc_info=True)
