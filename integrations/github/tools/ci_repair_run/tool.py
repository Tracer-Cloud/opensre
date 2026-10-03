"""One call for the bounded private CI repair demo."""

from __future__ import annotations

import re
from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel, report_run_error
from core.tool_framework import tool
from integrations.github.client import GitHubApiError
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_creds,
    github_source_available,
)
from integrations.github.tools.ci_fix.errors import GitHubCiFixError
from integrations.github.tools.ci_fix.gh import run_gh_json
from integrations.github.tools.ci_repair_demo.tool import finish_ci_repair_demo, seed_ci_repair_demo
from integrations.github.tools.ci_repair_loop.tool import (
    get_ci_repair_loop,
    schedule_ci_repair_loop,
)

_PR_VIEW_FIELDS = "headRefOid,commits,statusCheckRollup"
_PR_VIEW_TIMEOUT_SECONDS = 20
_RUN_ID_IN_DETAILS_URL = re.compile(r"/actions/runs/(\d+)")
_SUCCESS = "SUCCESS"
_NON_BLOCKING = frozenset({_SUCCESS, "NEUTRAL", "SKIPPED"})
_OUTCOME_SUCCESS = "success"
_OUTCOME_FAILED = "failed"
_OUTCOME_BLOCKED = "blocked"
_LOOP_FAILED = "failed"
_LOOP_SUCCEEDED = "succeeded"
_TERMINAL_STATUSES = frozenset({_LOOP_SUCCEEDED, _LOOP_FAILED, "timed_out", "cancelled"})


def _credentials(sources: dict[str, dict]) -> dict[str, Any]:
    return github_creds(sources.get("github", {}))


def _pr_number(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _run_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 0
    return value


def _text(value: object) -> str:
    return str(value or "").strip()


def _seed_target(seeded: dict[str, Any]) -> tuple[str, str, int] | None:
    owner = _text(seeded.get("owner"))
    repo = _text(seeded.get("repo"))
    number = _pr_number(seeded.get("pr_number"))
    if not owner or not repo or number is None:
        return None
    return owner, repo, number


def _conclusion(row: dict[str, Any]) -> str:
    raw = row.get("conclusion")
    if raw is None or not str(raw).strip():
        raw = row.get("state")
    return str(raw or "").strip().upper()


def _rollup(pull: dict[str, Any]) -> list[dict[str, Any]]:
    rows = pull.get("statusCheckRollup")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _checks_passed(rows: list[dict[str, Any]]) -> bool:
    """True when every check is non-blocking and one of them succeeded.

    A ``NEUTRAL`` or unrelated ``SKIPPED`` check does not undo a successful
    repair. An empty rollup, a failure, or a check that is still running does.
    """
    conclusions = [_conclusion(row) for row in rows]
    return (
        bool(conclusions)
        and _SUCCESS in conclusions
        and all(item in _NON_BLOCKING for item in conclusions)
    )


def _passing_run_id(rows: list[dict[str, Any]]) -> int:
    for row in rows:
        if _conclusion(row) != _SUCCESS:
            continue
        link = _text(row.get("detailsUrl") or row.get("targetUrl"))
        match = _RUN_ID_IN_DETAILS_URL.search(link)
        if match:
            return int(match.group(1))
    return 0


def _fix_commit(seed_head: str, pull: dict[str, Any]) -> str:
    head = _text(pull.get("headRefOid"))
    if not head or head == seed_head:
        return ""
    return head


def _outcome(
    status: str,
    *,
    checks_passed: bool,
    failed_run_id: int,
    fix_commit: str,
    passing_run_id: int,
) -> str:
    if (
        status == _LOOP_SUCCEEDED
        and checks_passed
        and failed_run_id > 0
        and fix_commit
        and passing_run_id > 0
    ):
        return _OUTCOME_SUCCESS
    if status == _LOOP_FAILED:
        return _OUTCOME_FAILED
    return _OUTCOME_BLOCKED


def _response_text(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    outcome: str,
    task_id: str,
    failed_run_id: int,
    fix_commit: str,
    passing_run_id: int,
    evidence: str,
) -> str:
    return (
        f"{owner}/{repo}#{pr_number} {outcome}: task {task_id}, "
        f"failed run {failed_run_id or 'none'}, fix {fix_commit or 'none'}, "
        f"passing run {passing_run_id or 'none'}. "
        f"Evidence {evidence or 'none'}. The repository remains."
    )


def _failed(exc: Exception) -> dict[str, Any]:
    report_run_error(
        exc,
        tool_name="run_ci_repair_demo",
        source="github",
        component=__name__,
        method="run_ci_repair_demo",
    )
    return {"ok": False, "error": f"Could not run the CI repair demo: {type(exc).__name__}."}


def _pull(owner: str, repo: str, pr_number: int, github_token: str | None) -> dict[str, Any]:
    payload = run_gh_json(
        ["pr", "view", str(pr_number), "--json", _PR_VIEW_FIELDS],
        repo=f"{owner}/{repo}",
        github_token=github_token,
        timeout=_PR_VIEW_TIMEOUT_SECONDS,
    )
    return payload


def _run(
    owner: str,
    repo: str,
    github_token: str | None,
    context: Any,
) -> dict[str, Any]:
    seeded = seed_ci_repair_demo(owner=owner, repo=repo, github_token=github_token)
    if not seeded.get("ok"):
        return seeded
    target = _seed_target(seeded)
    if target is None:
        return {"ok": False, "error": "The demo seed did not return a pull request."}
    seeded_owner, seeded_repo, pr_number = target
    scheduled = schedule_ci_repair_loop(
        demo=False,
        owner=seeded_owner,
        repo=seeded_repo,
        pr_number=pr_number,
        github_token=github_token,
        context=context,
        fast_checks=True,
    )
    if not scheduled.get("ok"):
        return scheduled
    task_id = _text(scheduled.get("task_id"))
    if not task_id:
        return {"ok": False, "error": "The repair schedule did not return a task id."}
    try:
        return _finish_scheduled(
            seeded_owner,
            seeded_repo,
            pr_number,
            task_id,
            seeded,
            scheduled,
            github_token,
        )
    except (GitHubCiFixError, GitHubApiError, OSError, RuntimeError, ValueError) as exc:
        _remove_schedule(seeded_owner, seeded_repo, pr_number, task_id, github_token)
        failed = _failed(exc)
        failed["task_id"] = task_id
        return failed


def _remove_schedule(
    owner: str,
    repo: str,
    pr_number: int,
    task_id: str,
    github_token: str | None,
) -> dict[str, Any]:
    """Drop the schedule after a read failure so the demo does not keep ticking."""
    try:
        return finish_ci_repair_demo(
            repo=f"{owner}/{repo}",
            pr_number=pr_number,
            loop_id=task_id,
            outcome=_OUTCOME_BLOCKED,
            github_token=github_token,
        )
    except (GitHubCiFixError, GitHubApiError, OSError, RuntimeError, ValueError):
        return {"ok": False}


def _still_running(
    owner: str, repo: str, pr_number: int, task_id: str, status: str
) -> dict[str, Any]:
    text = f"The repair {task_id} is still {status or 'running'}. The schedule was left in place."
    return {
        "ok": False,
        "owner": owner,
        "repo": repo,
        "pr_number": pr_number,
        "task_id": task_id,
        "error": text,
        "response_text": text,
    }


def _finish_scheduled(
    seeded_owner: str,
    seeded_repo: str,
    pr_number: int,
    task_id: str,
    seeded: dict[str, Any],
    scheduled: dict[str, Any],
    github_token: str | None,
) -> dict[str, Any]:
    observed = get_ci_repair_loop(
        task_id=task_id,
        wait_until_terminal=True,
        github_token=github_token,
    )
    if not observed.get("ok"):
        stopped = _remove_schedule(seeded_owner, seeded_repo, pr_number, task_id, github_token)
        refused = dict(observed)
        refused["task_id"] = task_id
        refused["loop_removed"] = stopped.get("loop_removed") is True
        return refused
    status = _text(observed.get("status"))
    if status not in _TERMINAL_STATUSES:
        return _still_running(seeded_owner, seeded_repo, pr_number, task_id, status)
    pull = _pull(seeded_owner, seeded_repo, pr_number, github_token)
    rows = _rollup(pull)
    failed_run_id = _run_id(seeded.get("failed_run_id"))
    fix_commit = _fix_commit(_text(seeded.get("head_sha")), pull)
    passing_run_id = _passing_run_id(rows)
    outcome = _outcome(
        _text(observed.get("status")),
        checks_passed=_checks_passed(rows),
        failed_run_id=failed_run_id,
        fix_commit=fix_commit,
        passing_run_id=passing_run_id,
    )
    finished = finish_ci_repair_demo(
        repo=f"{seeded_owner}/{seeded_repo}",
        pr_number=pr_number,
        loop_id=task_id,
        outcome=outcome,
        failed_run_id=failed_run_id,
        fix_commit=fix_commit,
        passing_run_id=passing_run_id,
        github_token=github_token,
    )
    evidence = _text(finished.get("evidence"))
    pr_url = _text(seeded.get("pr_url")) or _text(scheduled.get("pr_url"))
    result = {
        "ok": finished.get("ok") is True,
        "owner": seeded_owner,
        "repo": seeded_repo,
        "pr_number": pr_number,
        "pr_url": pr_url,
        "task_id": task_id,
        "failed_run_id": failed_run_id,
        "fix_commit": fix_commit,
        "passing_run_id": passing_run_id,
        "outcome": outcome,
        "evidence": evidence,
        "response_text": _response_text(
            owner=seeded_owner,
            repo=seeded_repo,
            pr_number=pr_number,
            outcome=outcome,
            task_id=task_id,
            failed_run_id=failed_run_id,
            fix_commit=fix_commit,
            passing_run_id=passing_run_id,
            evidence=evidence,
        ),
    }
    if finished.get("ok") is not True and finished.get("error"):
        result["ok"] = False
        result["error"] = finished["error"]
    return result


@tool(
    name="run_ci_repair_demo",
    source="github",
    display_name="Run CI repair demo",
    use_cases=["Run the bounded private CI repair demo through seed, repair, and evidence"],
    description=(
        "Seed one private CI repair demo, schedule repair of the pull request it returns, "
        "wait until that repair is terminal, read the pull request head and checks once, "
        "and save evidence. Passes demo false with that owner, repo, and pr_number. "
        "One failed seed or schedule is returned and no second loop is scheduled. "
        "A report that is still running leaves the schedule in place. A failed read "
        "after scheduling removes that schedule and includes the task id. "
        "Does not delete the GitHub repository."
    ),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    accepts_runtime_context=True,
    is_available=github_source_available,
    extract_params=_credentials,
    injected_params=GITHUB_INJECTED_PARAMS,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "GitHub user or organization."},
            "repo": {"type": "string", "description": "Approved demo repository name."},
        },
        "required": ["owner", "repo"],
        "additionalProperties": False,
    },
)
def run_ci_repair_demo(
    owner: str,
    repo: str,
    github_token: str | None = None,
    context: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Seed, schedule, wait, verify, and finish one bounded demo repair."""
    try:
        return _run(owner, repo, github_token, context)
    except (GitHubCiFixError, GitHubApiError, OSError, RuntimeError, ValueError) as exc:
        return _failed(exc)
