"""First-experience demo picker.

On the first interactive launch the shell asks which demo to run before the
prompt takes stdin. A marker file records the choice so the picker shows once;
``/demo`` reopens it on demand.

The CI/CD analytics demo runs its discovery steps here, deterministically:
the workspace scan paints the activity chart, a second picker asks which
repository to analyze, and only then is a canned prompt auto-submitted for the
analysis and the next-step offer. Mid-turn menus cannot open inside an
auto-submitted turn, so every choice that needs a menu happens before the turn
starts. Routing of the prompt itself stays with the action agent (no intent
heuristics — see ``surfaces/interactive_shell/AGENTS.md``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markup import escape

from config.constants.paths import OPENSRE_HOME_DIR
from infrastructure.analytics.capture import (
    capture_onboarding_demo_prompted,
    capture_onboarding_demo_selected,
    capture_onboarding_demo_skipped,
)
from infrastructure.analytics.source import is_test_run
from infrastructure.terminal.theme import DIM, WARNING
from integrations.github import resolve_github_token
from surfaces.shared.terminal.components.choice_menu import (
    repl_choose_one,
    repl_tty_interactive,
)
from surfaces.shared.terminal.components.loaders import llm_loader
from tools.system.workspace_git_scan.render import render_snapshot
from tools.system.workspace_git_scan.scan import RepoActivity, WorkspaceSnapshot, scan_workspace

if TYPE_CHECKING:
    from rich.console import Console

    from surfaces.interactive_shell.session import Session

logger = logging.getLogger(__name__)

MARKER_FILENAME = "onboarding_demo.json"
EXAMPLE_REPOSITORY = "Tracer-Cloud/opensre"
_MENU_TITLE = "Which demo would you like me to run? (Esc to skip)"
_MENU_EXPLAINER = (
    "For a demo, I'd rather use something real from your machine than a toy example. "
    "Each takes a couple of minutes and I'll use real GitHub repositories on your machine."
)
_CUSTOM_LABEL = "Or type your own answer..."
_CUSTOM_OPTION = "custom"
_SKIPPED_OPTION = "skipped"
_SNAPSHOT_LEAD = "Here's a live snapshot built from your machine:"
_REPOSITORY_TITLE = "Which repository should I analyze?"
_EXAMPLE_LABEL = f"Use the open-source example repository ({EXAMPLE_REPOSITORY})"
_TOKEN_MISSING = (
    "The CI/CD analysis reads GitHub Actions history, which needs a GitHub token. "
    "Run `opensre integrations setup github`, then `/demo` to continue."
)
_MAX_OWN_REPOSITORIES = 3
_SCAN_DAYS = 30

OPTION_CI_ANALYTICS = "ci_analytics"
OPTION_CI_AGENT = "ci_agent"
OPTION_SLACK = "slack"


@dataclass(frozen=True, slots=True)
class DemoSuggestion:
    """One demo shown in the first-experience picker."""

    option: str
    """Stable analytics identifier for this demo."""

    label: str
    """Menu row shown to the user."""

    prompt: str
    """Canned prompt auto-submitted as the first turn when selected."""


DEMO_SUGGESTIONS: tuple[DemoSuggestion, ...] = (
    DemoSuggestion(
        option=OPTION_CI_ANALYTICS,
        label="Explore a repo and analyze its CI/CD performance (recommended)",
        prompt=(
            "Analyze the CI/CD reliability of {repository} for the last 30 days as the "
            "CI/CD analytics demo: show the KPIs and the developer time blocked by "
            "unreliable CI, then offer what to do next."
        ),
    ),
    DemoSuggestion(
        option=OPTION_CI_AGENT,
        label="Set up an agent that improves CI/CD reliability over time",
        prompt=(
            "Onboard me on the CI/CD fixing flow for the current repository, then set up "
            "a read-only recurring weekday CI health check at 8:00 AM."
        ),
    ),
    DemoSuggestion(
        option=OPTION_SLACK,
        label="Connect OpenSRE to Slack and hand off DevOps chores for your team",
        prompt=(
            "Set up the Slack integration and show me how to hand off DevOps chores to "
            "OpenSRE from Slack."
        ),
    ),
)


def marker_path() -> Path:
    return OPENSRE_HOME_DIR / MARKER_FILENAME


def demo_already_offered() -> bool:
    return marker_path().is_file()


def should_offer_demo() -> bool:
    """True on the first interactive launch that has not seen the picker yet."""
    if is_test_run():
        return False
    if not repl_tty_interactive():
        return False
    return not demo_already_offered()


def offer_demo(session: Session, console: Console | None = None, *, force: bool = False) -> bool:
    """Show the picker, run the chosen demo's discovery steps, and queue its prompt.

    Returns True when a demo prompt was queued. Never blocks startup: any
    unexpected failure is logged and the REPL proceeds into the normal prompt.
    """
    try:
        if not force and not should_offer_demo():
            return False
        capture_onboarding_demo_prompted()
        if console is not None:
            console.print(f"[{DIM}]{_MENU_EXPLAINER}[/]")
        selected = repl_choose_one(
            title=_MENU_TITLE,
            choices=[
                *((suggestion.option, suggestion.label) for suggestion in DEMO_SUGGESTIONS),
                (_CUSTOM_OPTION, _CUSTOM_LABEL),
            ],
            custom_label=_CUSTOM_LABEL,
            letter_keys=True,
        )
        if _nothing_chosen(selected):
            capture_onboarding_demo_skipped()
            _record(_SKIPPED_OPTION)
            return False
        assert selected is not None
        suggestion = _suggestion_for(selected)
        if suggestion is None:
            capture_onboarding_demo_selected(option=_CUSTOM_OPTION, custom=True)
            _record(_CUSTOM_OPTION)
            session.terminal.set_auto_prompt(selected)
            return True
        capture_onboarding_demo_selected(option=suggestion.option, custom=False)
        if suggestion.option == OPTION_CI_ANALYTICS:
            started = _start_ci_analytics_demo(session, console, suggestion)
            if started:
                _record(suggestion.option)
            return started
        _record(suggestion.option)
        session.terminal.set_auto_command(suggestion.prompt)
        return True
    except Exception:
        logger.warning("Onboarding demo picker failed.", exc_info=True)
        return False


def _start_ci_analytics_demo(
    session: Session, console: Console | None, suggestion: DemoSuggestion
) -> bool:
    """Scan, let the user pick a repository, then queue the analysis prompt."""
    home = Path.home()
    if console is not None:
        with llm_loader(console, f"Scanning {escape(str(home))} for git repositories"):
            snapshot = scan_workspace(home, days=_SCAN_DAYS)
    else:
        snapshot = scan_workspace(home, days=_SCAN_DAYS)
    if console is not None:
        console.print()
        console.print(_SNAPSHOT_LEAD)
        console.print()
        render_snapshot(console, snapshot)
        console.print()
    if not resolve_github_token(None):
        if console is not None:
            console.print(f"[{WARNING}]{_TOKEN_MISSING}[/]")
        return False
    repository = choose_repository(snapshot)
    if repository is None:
        return False
    if console is not None:
        console.print()
        console.print(
            f"[{DIM}]Analyzing the CI/CD reliability of {escape(repository)} for the last {_SCAN_DAYS} "
            "days. Reading the GitHub Actions history takes about half a minute; the report "
            "appears below when it is ready.[/]"
        )
    # A plain turn keeps the prompt bar and its spinner visible while the model
    # and the analysis run; a work-turn autosubmit would suspend them.
    session.terminal.set_auto_prompt(suggestion.prompt.format(repository=repository))
    return True


def choose_repository(snapshot: WorkspaceSnapshot) -> str | None:
    """Ask which repository to analyze; ``None`` when the user escapes."""
    choices = [
        (repo.github_full_name, _candidate_label(repo)) for repo in suitable_repositories(snapshot)
    ]
    choices.append((EXAMPLE_REPOSITORY, _EXAMPLE_LABEL))
    choices.append((_CUSTOM_OPTION, _CUSTOM_LABEL))
    selected = repl_choose_one(
        title=_REPOSITORY_TITLE,
        choices=choices,
        custom_label=_CUSTOM_LABEL,
        letter_keys=True,
    )
    if _nothing_chosen(selected):
        return None
    assert selected is not None
    return selected.strip()


def _suggestion_for(option: str) -> DemoSuggestion | None:
    for suggestion in DEMO_SUGGESTIONS:
        if suggestion.option == option:
            return suggestion
    return None


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
    """Persist the choice so the picker shows once per machine."""
    try:
        path = marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"option": option, "chosen_at": datetime.now(UTC).isoformat()}),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("Could not record the onboarding demo choice.", exc_info=True)


__all__ = [
    "DEMO_SUGGESTIONS",
    "EXAMPLE_REPOSITORY",
    "MARKER_FILENAME",
    "OPTION_CI_AGENT",
    "OPTION_CI_ANALYTICS",
    "OPTION_SLACK",
    "DemoSuggestion",
    "choose_repository",
    "demo_already_offered",
    "marker_path",
    "offer_demo",
    "should_offer_demo",
    "suitable_repositories",
]
