"""One-call seed and cleanup for the CI repair onboarding demo."""

from __future__ import annotations

import json
import re
from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import ERROR_KIND_REFUSED, SideEffectLevel, report_run_error
from core.tool_framework import tool
from infrastructure.scheduling.scheduler.storage import get_task, list_tasks, remove_task
from integrations.github.agent_tools import (
    github_tool_available,
    github_tool_params,
    require_webapp_github,
)
from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_creds,
    github_source_available,
)
from integrations.github.tools.ci_repair_demo.cleanup import (
    loop_still_listed,
    split_repo,
    write_evidence,
)
from integrations.github.tools.ci_repair_demo.seed import DemoRefused, seed_demo
from integrations.github.tools.ci_repair_loop.credentials import configured_token
from integrations.github.tools.ci_repair_loop.schedule import repair_task_name


def _credentials(sources: dict[str, dict]) -> dict[str, Any]:
    return github_creds(sources.get("github", {}))


def _refused(exc: DemoRefused) -> dict[str, Any]:
    return {
        "ok": False,
        "error": exc.user_message,
        "error_kind": ERROR_KIND_REFUSED,
        "response_text": exc.user_message,
    }


_GITHUB_MESSAGE_LIMIT = 160
_SECRET_TEXT = re.compile(r"(ghp_|github_pat_|Bearer\s+\S+|\btoken\b)", re.IGNORECASE)


def _safe_github_message(raw: str) -> str:
    """GitHub's short ``message`` field, never the raw body or a secret."""
    text = raw.strip()
    if text[:1] in "{[":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return ""
        if not isinstance(parsed, dict):
            return ""
        text = str(parsed.get("message") or "").strip()
    if not text or len(text) > _GITHUB_MESSAGE_LIMIT or _SECRET_TEXT.search(text):
        return ""
    if any(ord(char) < 32 for char in text):
        return ""
    return text


def _seed_error_text(exc: GitHubApiError) -> str:
    """Name the GitHub call, its status, and a short public message.

    The raw body stays out. Method and path are how a later failure stays
    distinguishable from an unclassified ``GitHubApiError``.
    """
    detail = _safe_github_message(exc.message)
    call = " ".join(part for part in (exc.method.strip(), exc.path.strip()) if part)
    status = f"HTTP {exc.status_code}" if exc.status_code is not None else ""
    parts = [part for part in (call, status, detail) if part]
    if not parts:
        return "Could not seed the CI repair demo: GitHubApiError."
    return "Could not seed the CI repair demo: " + ": ".join(parts) + "."


def _failed(exc: Exception, *, tool_name: str, method: str, action: str) -> dict[str, Any]:
    report_run_error(
        exc,
        tool_name=tool_name,
        source="github",
        component=__name__,
        method=method,
    )
    if isinstance(exc, GitHubApiError) and tool_name == "seed_ci_repair_demo":
        error = _seed_error_text(exc)
    else:
        error = f"Could not {action}: {type(exc).__name__}."
    return {"ok": False, "error": error}


@tool(
    name="seed_ci_repair_demo",
    source="github",
    display_name="Seed CI repair demo",
    use_cases=["Create the private CI repair onboarding demo and its failing pull request"],
    description=(
        "Create or reuse a private CI repair demo. A 404 from GET /repos/{owner}/{repo} "
        "creates that private repository; any other error stops. A repository that is not "
        "an OpenSRE CI repair demo is left unchanged, and this tool seeds a new private "
        "repository named opensre-ci-repair-demo- plus 4 lowercase letters or digits on "
        "the same owner. The result's owner and repo are the repository the plan continues "
        "with. Commits a passing main (calculator.py adding, its unit test, and Demo "
        "calculator CI) and one commit on demo/failing-ci that makes add subtract, opens "
        "that pull request, and returns after the pull-request Actions run has failed. An "
        "existing open demo pull request is reused; one whose repair already landed first "
        "gets one new commit that makes add subtract again (rearmed). Does not list the "
        "organization or search code."
    ),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    is_available=github_tool_available(github_source_available),
    extract_params=github_tool_params(_credentials),
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
@require_webapp_github
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
        "remove that CI repair schedule, and confirm a listing no longer shows it. "
        "Removes a task only when its name is the CI repair for this repository. "
        "Still removes that schedule when the evidence file cannot be written. "
        "Accepts the repository name the user approved, including one without an id suffix. "
        "Does not delete the GitHub repository."
    ),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    is_available=github_tool_available(github_source_available),
    extract_params=github_tool_params(_credentials),
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
@require_webapp_github
def finish_ci_repair_demo(
    repo: str,
    pr_number: int,
    loop_id: str,
    outcome: str,
    failed_run_id: int = 0,
    fix_commit: str = "",
    passing_run_id: int = 0,
    github_token: str | None = None,
    analysis: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Save evidence, remove the demo schedule, and leave the repository in place.

    ``analysis`` (links and root cause, from ``run_ci_repair_demo``) is not in the
    model's schema; it is appended to the evidence file.
    """
    del github_token
    try:
        full_name = "/".join(split_repo(repo))
        _require_demo_task(loop_id, full_name)
    except DemoRefused as exc:
        return _refused(exc)
    except (OSError, RuntimeError, ValueError) as exc:
        return _failed(
            exc,
            tool_name="finish_ci_repair_demo",
            method="finish_demo",
            action="finish the CI repair demo",
        )
    evidence, evidence_error, refused = _save_evidence(
        repo=repo,
        pr_number=pr_number,
        loop_id=loop_id,
        outcome=outcome,
        failed_run_id=failed_run_id,
        fix_commit=fix_commit,
        passing_run_id=passing_run_id,
        analysis=analysis,
    )
    try:
        remove_task(loop_id.strip())
        gone = not loop_still_listed(loop_id, list_tasks())
    except (OSError, RuntimeError, ValueError) as exc:
        return _failed(
            exc,
            tool_name="finish_ci_repair_demo",
            method="finish_demo",
            action="finish the CI repair demo",
        )
    if evidence_error:
        result: dict[str, Any] = {
            "ok": False,
            "error": evidence_error,
            "loop_removed": gone,
            "repository_retained": True,
        }
        if refused:
            result["error_kind"] = ERROR_KIND_REFUSED
        if evidence:
            result["evidence"] = evidence
        return result
    if not gone:
        return {
            "ok": False,
            "error": "The scheduled repair is still listed.",
            "evidence": evidence,
            "loop_removed": False,
            "repository_retained": True,
        }
    return {
        "ok": True,
        "evidence": evidence,
        "loop_removed": True,
        "repository_retained": True,
        "response_text": (
            "Saved demo evidence and removed the scheduled repair. The repository remains."
        ),
    }


def _require_demo_task(loop_id: str, full_name: str) -> None:
    """Refuse a schedule that is not this repository's CI repair."""
    task = get_task(loop_id.strip())
    if task is None:
        return
    owner, repo_name = full_name.split("/", 1)
    if task.name.casefold() != repair_task_name(owner, repo_name).casefold():
        raise DemoRefused("That scheduled task is not the CI repair for this repository.")


def _save_evidence(**kwargs: Any) -> tuple[str, str, bool]:
    """Return ``(path, error, refused)``. A write failure does not skip removal."""
    try:
        return str(write_evidence(**kwargs)), "", False
    except DemoRefused as exc:
        return "", exc.user_message, True
    except (OSError, RuntimeError, ValueError) as exc:
        _failed(
            exc,
            tool_name="finish_ci_repair_demo",
            method="write_evidence",
            action="finish the CI repair demo",
        )
        return "", f"Could not finish the CI repair demo: {type(exc).__name__}.", False
