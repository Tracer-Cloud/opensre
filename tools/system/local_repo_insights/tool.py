"""Action tool: what the user's local repositories say about how they work, with no GitHub."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from config.constants.local_insights import (
    GITHUB_ERROR_KINDS,
    GITHUB_ONLY_METRICS,
    LocalInsightsReason,
)
from core.agent_harness.tools import action_context_from_agent_context
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from infrastructure.analytics.capture import capture_local_repositories_analyzed
from tools.system.local_repo_insights.analysis import (
    MAX_REPOSITORIES,
    TOOL_NAME,
    AnalysisStop,
    LocalAnalysis,
    analyze_repositories,
)

_DEFAULT_DAYS = 30
_MAX_DAYS = 365
_REASONS = tuple(reason.value for reason in LocalInsightsReason)

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {
            "type": "string",
            "enum": list(_REASONS),
            "description": (
                "Why this runs: github_not_connected, github_failed (pass github_error too), "
                "no_github_actions, or requested."
            ),
        },
        "github_error": {
            "type": "string",
            "description": (
                "The error_kind analyze_github_ci_reliability returned, when reason is "
                "github_failed and it named one."
            ),
        },
        "repository": {
            "type": "string",
            "description": "The repository to lead with: owner/repo or a local checkout path.",
        },
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": MAX_REPOSITORIES,
            "description": (
                "Local checkout paths from this session's scan_local_git_workspace result, "
                "most active first. Omit them and the workspace is scanned."
            ),
        },
        "root": {
            "type": "string",
            "description": (
                "Folder to scan when paths is omitted; defaults to the home directory. Name a "
                "skipped folder such as ~/Documents to look there."
            ),
        },
        "days": {
            "type": "integer",
            "minimum": 1,
            "maximum": _MAX_DAYS,
            "description": f"History window in days, default {_DEFAULT_DAYS}.",
        },
    },
    "additionalProperties": False,
}


def _scope(context: Any) -> Any:
    if context is None:
        return None
    try:
        return action_context_from_agent_context(context)
    except RuntimeError:
        return None


def _cancellation(scope: Any) -> Callable[[], bool] | None:
    console = getattr(scope, "console", None)
    if console is None:
        return None
    return lambda: bool(getattr(console, "cancel_requested", False))


def _progress(context: Any) -> Callable[[str], None] | None:
    emit = getattr(context, "emit_update", None)
    if emit is None:
        return None
    return lambda text: emit({"progress": text})


def _github_error(raw: str) -> str:
    """A known GitHub ``error_kind``, ``other`` for anything else, empty when none was given."""
    kind = raw.strip().casefold()
    if not kind:
        return ""
    return kind if kind in GITHUB_ERROR_KINDS else "other"


def _reason(raw: str | None) -> str:
    value = (raw or "").strip()
    return value if value in _REASONS else LocalInsightsReason.REQUESTED.value


def _summary(analysis: LocalAnalysis) -> str:
    if not analysis.repos:
        return f"No local repositories with history from the last {analysis.days} days were found."
    return (
        f"Read {len(analysis.repos)} local "
        f"{'repository' if len(analysis.repos) == 1 else 'repositories'}: "
        f"{analysis.own_commits:,} of your commits "
        f"in the last {analysis.days} days, {len(analysis.insights)} insights."
    )


def _result(analysis: LocalAnalysis, reason: str) -> dict[str, Any]:
    summary = _summary(analysis)
    return {
        "source": "system",
        "success": True,
        "reason": reason,
        "days": analysis.days,
        "repositories": len(analysis.repos),
        "commits": analysis.commits,
        "own_commits": analysis.own_commits,
        "insights": [
            {"kind": insight.kind.value, "label": insight.label, "fact": insight.fact}
            for insight in analysis.insights
        ],
        "github_only_metrics": list(GITHUB_ONLY_METRICS),
        "repos": [
            {
                "name": repo.name,
                "ci": list(repo.providers),
                "commits": repo.commits,
                "own_commits": repo.own_commits,
            }
            for repo in analysis.repos
        ],
        "coverage_notices": list(analysis.notices),
        "skipped_protected": list(analysis.skipped_protected),
        "summary": summary,
        "response_text": summary,
    }


def _leave_value_notes(scope: Any, analysis: LocalAnalysis | None) -> None:
    """Hand the value recorder each insight's name-free summary, keyed by its report label.

    None clears what an earlier analysis left, so a failed run leaves nothing to record.
    """
    notes = getattr(getattr(scope, "session", None), "skill_value_notes", None)
    if not isinstance(notes, dict):
        return
    notes.clear()
    if analysis is not None:
        notes.update(
            {insight.label: (insight.kind.value, insight.summary) for insight in analysis.insights}
        )


def _record(analysis: LocalAnalysis, *, reason: str, github_error: str, duration_ms: int) -> None:
    own = analysis.own_commits
    paired = sum(repo.ai_coauthored for repo in analysis.repos)
    outcome = analysis.stop_reason.value if analysis.stop_reason else "ok"
    if not analysis.repos and analysis.stop_reason is None:
        outcome = "unreadable" if analysis.unreadable else "no_repositories"
    capture_local_repositories_analyzed(
        reason=reason,
        github_error=_github_error(github_error),
        outcome=outcome,
        repositories=len(analysis.repos),
        commits=analysis.commits,
        own_commits=own,
        # Rounded to 5%, from enough commits to mean something.
        ai_coauthored_share=5 * round(20 * paired / own) if own >= 10 else None,
        insight_kinds=[insight.kind.value for insight in analysis.insights],
        ci_providers=Counter(
            provider for repo in analysis.repos for provider in repo.providers or ("none",)
        ),
        hosts=Counter(repo.host for repo in analysis.repos),
        repos_without_ci=sum(1 for repo in analysis.repos if not repo.providers),
        days=analysis.days,
        duration_ms=duration_ms,
    )


@tool(
    name=TOOL_NAME,
    source="system",
    display_name="Analyze local repositories",
    description=(
        "Read the user's local git history and CI files, with no GitHub token and no network, "
        "and return ranked insights about how they work: follow-up fixes, CI trial and error, "
        "AI co-authorship, workflow hygiene, tests with code changes, loose ends. Returns facts "
        "and figures only; never commit messages or code. Read-only."
    ),
    use_cases=[
        "Show a first-time user insights from their own repositories when GitHub is not connected",
        "Keep onboarding going when the GitHub CI analysis cannot read GitHub",
        "Describe the habits of repositories that have no GitHub Actions or use another CI",
    ],
    anti_examples=[
        "GitHub Actions run history, failure rates or CI waiting time (use analyze_github_ci_reliability)",
        "Listing the repositories on this machine (use scan_local_git_workspace)",
    ],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    accepts_runtime_context=True,
    input_schema=_INPUT_SCHEMA,
    tags=("safe", "no-credentials"),
)
def analyze_local_repositories(
    reason: str | None = None,
    github_error: str | None = None,
    repository: str | None = None,
    paths: list[str] | None = None,
    root: str | None = None,
    days: int | None = None,
    context: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Read up to eight local repositories and return their ranked insights.

    A cancelled run returns ``cancelled: True``. Every run is recorded as counts
    and insight kinds, never names, paths or commit text.
    """
    scope = _scope(context)
    _leave_value_notes(scope, None)
    window = min(max(int(days or _DEFAULT_DAYS), 1), _MAX_DAYS)
    why = _reason(reason)
    started = time.monotonic()
    analysis = analyze_repositories(
        days=window,
        paths=[str(path) for path in paths or ()],
        repository=repository or "",
        root=root,
        should_stop=_cancellation(scope),
        on_progress=_progress(context),
    )
    duration_ms = round((time.monotonic() - started) * 1000)
    _record(analysis, reason=why, github_error=github_error or "", duration_ms=duration_ms)
    if not analysis.repos and analysis.unreadable:
        return {
            "source": "system",
            "success": False,
            "error": "None of the chosen local repositories could be read.",
            "response_text": "None of the chosen local repositories could be read.",
        }
    if analysis.stop_reason is AnalysisStop.CANCELLED:
        return {
            "source": "system",
            "success": False,
            "cancelled": True,
            "stop_reason": AnalysisStop.CANCELLED.value,
            "response_text": "The local analysis was cancelled; nothing was shown.",
        }
    _leave_value_notes(scope, analysis)
    return _result(analysis, why)


__all__ = ["analyze_local_repositories"]
