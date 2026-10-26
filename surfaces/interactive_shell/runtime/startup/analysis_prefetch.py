"""Start the CI analysis demo's slow reads while its menus are answered.

The model takes longer to call ``scan_local_git_workspace`` after the demo is
picked, and ``analyze_github_ci_reliability`` after a repository is picked,
than either read takes. Started here, both run during that model time, and the
tool calls take their results instead of reading again.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import TYPE_CHECKING

from config.constants.skills import ANALYZE_REPO_OPTION, ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME
from core.agent_harness.spi.integrations import resolve_and_cache_integrations
from infrastructure.analytics.source import is_test_run
from integrations.github import prefetch_ci_analysis
from tools.system.workspace_git_scan import prefetch_workspace_scan

if TYPE_CHECKING:
    from surfaces.interactive_shell.session import Session

logger = logging.getLogger(__name__)

# The analysis skill offers repositories as ``owner/repo``; GitHub's naming rules.
_REPOSITORY_RE = re.compile(r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/(?P<repo>[\w.-]{1,100})")


def prefetch_demo_scan() -> None:
    """Start the workspace scan the analysis demo opens with; never in the test harness."""
    if is_test_run():
        return
    try:
        prefetch_workspace_scan()
    except Exception:
        logger.debug("Could not start the workspace scan prefetch", exc_info=True)


def prefetch_after_menu_answer(session: Session, picked: str) -> None:
    """Start the read the analysis skill's next step needs for the answer ``picked``.

    Only while that skill is active: picking it starts the workspace scan, and
    picking an ``owner/repo`` starts that repository's 30-day analysis.
    """
    if is_test_run() or session.active_skill != ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME:
        return
    if picked == ANALYZE_REPO_OPTION:
        prefetch_demo_scan()
        return
    match = _REPOSITORY_RE.fullmatch(picked.strip())
    if match is None:
        return
    # Resolving integrations may fetch remotely; keep it off the menu answer's path.
    threading.Thread(
        target=_prefetch_analysis,
        args=(session, match["owner"], match["repo"]),
        name="ci-analysis-prefetch-start",
        daemon=True,
    ).start()


def _prefetch_analysis(session: Session, owner: str, repo: str) -> None:
    try:
        prefetch_ci_analysis(
            owner, repo, resolved_integrations=resolve_and_cache_integrations(session)
        )
    except Exception:
        logger.debug("Could not start the CI analysis prefetch", exc_info=True)


__all__ = ["prefetch_after_menu_answer", "prefetch_demo_scan"]
