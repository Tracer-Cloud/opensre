"""The repository a demo needs, chosen in the shell before the model runs.

A demo skill declares its repository menu as an ``after_tool`` hook on the
workspace scan. Left to the model, the scan is skipped and the repository is
read out of the request, so the menu never opens. When a demo is picked, the
shell scans the machine and asks the declared question itself; the model then
receives the repository beside the demo answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.agent_harness.spi.grounding import ActionSkill, getting_started_skills
from surfaces.interactive_shell.runtime.startup.ci_agent_demo import (
    choose_repository,
    scan_and_show,
)

if TYPE_CHECKING:
    from rich.console import Console

_SCAN_TOOL = "scan_local_git_workspace"
_MENU_TOOL = "ask_user_choice"
_SCAN_REPOSITORIES = "local_git_scan_repos"


def demo_skill_for(answer: str) -> ActionSkill | None:
    """The demo skill whose menu row is ``answer``, or ``None``."""
    return next(
        (skill for skill in getting_started_skills() if skill.getting_started == answer),
        None,
    )


def repository_question(skill: ActionSkill) -> str | None:
    """The repository question ``skill`` declares after the workspace scan, or ``None``."""
    for hook in skill.after_tool:
        if hook.after != _SCAN_TOOL or hook.call.tool != _MENU_TOOL:
            continue
        if hook.options_from != _SCAN_REPOSITORIES:
            continue
        title = str(hook.call.args.get("title") or "").strip()
        if title:
            return title
    return None


def choose_demo_repository(console: Console | None, question: str) -> str | None:
    """Scan this machine, paint the chart, and ask ``question``; ``None`` when the user escapes."""
    snapshot = scan_and_show(console)
    return choose_repository(snapshot, title=question)


__all__ = ["choose_demo_repository", "demo_skill_for", "repository_question"]
