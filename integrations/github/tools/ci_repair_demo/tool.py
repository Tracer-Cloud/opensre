"""One-call seed and cleanup for the CI repair onboarding demo."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import ERROR_KIND_REFUSED, SideEffectLevel, report_run_error
from core.tool_framework import tool
from infrastructure.scheduling.scheduler.storage import list_tasks, remove_task
from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_creds,
    github_source_available,
)
from integrations.github.tools.ci_repair_demo.cleanup import loop_still_listed, write_evidence
from integrations.github.tools.ci_repair_demo.seed import DemoRefused, seed_demo
from integrations.github.tools.ci_repair_loop.credentials import configured_token


def _credentials(sources: dict[str, dict]) -> dict[str, Any]:
    return github_creds(sources.get("github", {}))


def _refused(exc: DemoRefused) -> dict[str, Any]:
    return {
        "ok": False,
        "error": exc.user_message,
        "error_kind": ERROR_KIND_REFUSED,
        "response_text": exc.user_message,
    }


def _failed(exc: Exception, *, tool_name: str, method: str, action: str) -> dict[str, Any]:
    report_run_error(
        exc,
        tool_name=tool_name,
        source="github",
        component=__name__,
        method=method,
    )
    return {"ok": False, "error": f"Could not {action}: {type(exc).__name__}."}


@tool(
    name="seed_ci_repair_demo",
    source="github",
    display_name="Seed CI repair demo",
    use_cases=["Create the private CI repair onboarding demo and its failing pull request"],
    description=(
        "Create or reuse a private CI repair demo. A 404 from GET /repos/{owner}/{repo} "
        "creates that private repository; any other error stops. Commits a passing main "
        "(calculator.py adding, its unit test, and Demo calculator CI) and one commit on "
        "demo/failing-ci that makes add subtract, opens that pull request, and returns "
        "after the pull-request Actions run has failed. An existing open demo pull request "
        "is reused. Does not list the organization or search code."
    ),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    is_available=github_source_available,
    extract_params=_credentials,
    injected_params=GITHUB_INJECTED_PARAMS,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "GitHub user or organization."},
            "repo": {"type": "string", "description": "Demo repository name."},
        },
        "required": ["owner", "repo"],
        "additionalProperties": False,
    },
)
def seed_ci_repair_demo(
    owner: str,
    repo: str,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Seed one failing demo pull request and return its failed Actions run."""
    try:
        client = GitHubRestClient(configured_token(github_token))
        return {"ok": True, **seed_demo(client, owner, repo)}
    except DemoRefused as exc:
        return _refused(exc)
    except (GitHubApiError, OSError, RuntimeError, ValueError) as exc:
        return _failed(
            exc,
            tool_name="seed_ci_repair_demo",
            method="seed_demo",
            action="seed the CI repair demo",
        )


@tool(
    name="finish_ci_repair_demo",
    source="github",
    display_name="Finish CI repair demo",
    use_cases=["Save CI repair demo evidence and remove its scheduled task"],
    description=(
        "Write demo evidence under ~/.opensre/demo-results/ for the approved owner/repo, "
        "remove that scheduled task, and confirm a schedule listing no longer shows it. "
        "Accepts the repository name the user approved, including one without an id suffix. "
        "Does not delete the GitHub repository."
    ),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    is_available=github_source_available,
    extract_params=_credentials,
    injected_params=GITHUB_INJECTED_PARAMS,
    input_schema={
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Approved owner/repo, for example octocat/opensre-ci-repair-demo.",
            },
            "pr_number": {"type": "integer", "minimum": 1},
            "loop_id": {"type": "string", "description": "Scheduled task id to remove."},
            "outcome": {
                "type": "string",
                "enum": ["success", "failed", "blocked"],
            },
            "failed_run_id": {"type": "integer", "minimum": 0},
            "fix_commit": {"type": "string"},
            "passing_run_id": {"type": "integer", "minimum": 0},
        },
        "required": ["repo", "pr_number", "loop_id", "outcome"],
        "additionalProperties": False,
    },
)
def finish_ci_repair_demo(
    repo: str,
    pr_number: int,
    loop_id: str,
    outcome: str,
    failed_run_id: int = 0,
    fix_commit: str = "",
    passing_run_id: int = 0,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Save evidence, remove the demo schedule, and leave the repository in place."""
    del github_token
    try:
        evidence = write_evidence(
            repo=repo,
            pr_number=pr_number,
            loop_id=loop_id,
            outcome=outcome,
            failed_run_id=failed_run_id,
            fix_commit=fix_commit,
            passing_run_id=passing_run_id,
        )
        remove_task(loop_id.strip())
        gone = not loop_still_listed(loop_id, list_tasks())
    except DemoRefused as exc:
        return _refused(exc)
    except (OSError, RuntimeError, ValueError) as exc:
        return _failed(
            exc,
            tool_name="finish_ci_repair_demo",
            method="finish_demo",
            action="finish the CI repair demo",
        )
    if not gone:
        return {
            "ok": False,
            "error": "The scheduled repair is still listed.",
            "evidence": str(evidence),
            "loop_removed": False,
            "repository_retained": True,
        }
    return {
        "ok": True,
        "evidence": str(evidence),
        "loop_removed": True,
        "repository_retained": True,
        "response_text": (
            "Saved demo evidence and removed the scheduled repair. The repository remains."
        ),
    }
