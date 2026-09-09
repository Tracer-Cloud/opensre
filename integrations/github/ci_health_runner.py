"""Read-only data collection for the scheduled GitHub CI health skill."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from infrastructure.scheduling.scheduler.agent_runner import AgentPayload
from integrations.github import GitHubApiError, GitHubRestClient

CI_HEALTH_SKILL_NAME = "reporting-github-ci-failures"
MAX_OPEN_PRS = 100
MAX_CHECK_RUNS_PER_SHA = 1_000
_CHECK_PAGES_FOR_TRUNCATION_DETECTION = 11
_FAILED_CHECK_CONCLUSIONS = frozenset(
    {"action_required", "cancelled", "failure", "startup_failure", "timed_out"}
)
_FAILED_STATUS_STATES = frozenset({"error", "failure"})
_REPAIR_HANDOFF = (
    "Repair handoff: ask OpenSRE to fix the affected PR or branch interactively. "
    "The fix_github_pr_ci action requires your approval and is never run by this schedule."
)


@dataclass(frozen=True, slots=True)
class _Failure:
    name: str
    url: str
    timestamp: object


def _required_text(payload: AgentPayload, key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise RuntimeError(f"GitHub CI health requires {key} in the scheduled skill inputs.")
    return value


def _segment(value: str) -> str:
    """Encode one untrusted GitHub identifier as exactly one URL path segment."""
    return quote(value, safe="")


def _single_line(value: object, *, fallback: str) -> str:
    rendered = " ".join(str(value or "").split()).strip()
    return rendered or fallback


def _age(timestamp: object, *, now: datetime) -> str:
    raw = str(timestamp or "").strip()
    if not raw:
        return "age unknown"
    try:
        occurred_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return "age unknown"
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    reference = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    seconds = max(0, int((reference.astimezone(UTC) - occurred_at.astimezone(UTC)).total_seconds()))
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m old"
    if seconds < 86400:
        return f"{seconds // 3600}h old"
    return f"{seconds // 86400}d old"


def _failed_check_runs(rows: list[dict[str, Any]]) -> list[_Failure]:
    failures: list[_Failure] = []
    for row in rows:
        conclusion = str(row.get("conclusion") or "").strip().lower()
        if conclusion not in _FAILED_CHECK_CONCLUSIONS:
            continue
        failures.append(
            _Failure(
                name=_single_line(row.get("name"), fallback="Unnamed check"),
                url=_single_line(row.get("html_url"), fallback="link unavailable"),
                timestamp=row.get("completed_at") or row.get("started_at"),
            )
        )
    return failures


def _failed_commit_statuses(payload: object) -> list[_Failure]:
    if not isinstance(payload, dict):
        return []
    raw_statuses = payload.get("statuses")
    if not isinstance(raw_statuses, list):
        return []
    failures: list[_Failure] = []
    for raw in raw_statuses:
        if not isinstance(raw, dict):
            continue
        state = str(raw.get("state") or "").strip().lower()
        if state not in _FAILED_STATUS_STATES:
            continue
        failures.append(
            _Failure(
                name=_single_line(raw.get("context"), fallback="Unnamed status"),
                url=_single_line(raw.get("target_url"), fallback="link unavailable"),
                timestamp=raw.get("updated_at") or raw.get("created_at"),
            )
        )
    return failures


def _failures_for_sha(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    sha: str,
    responsible: str,
    coverage_notices: list[str],
) -> list[_Failure]:
    root = f"/repos/{_segment(owner)}/{_segment(repo)}/commits/{_segment(sha)}"
    check_runs = client.paginate(
        f"{root}/check-runs",
        params={"filter": "latest", "per_page": 100},
        collection_key="check_runs",
        max_pages=_CHECK_PAGES_FOR_TRUNCATION_DETECTION,
    )
    if len(check_runs) > MAX_CHECK_RUNS_PER_SHA:
        coverage_notices.append(
            "Coverage notice: check-run report limited to the first "
            f"{MAX_CHECK_RUNS_PER_SHA} latest checks for {responsible}."
        )
        check_runs = check_runs[:MAX_CHECK_RUNS_PER_SHA]
    combined_status = client.request("GET", f"{root}/status")
    return [*_failed_check_runs(check_runs), *_failed_commit_statuses(combined_status)]


def _format_failures(failures: list[_Failure], *, responsible: str, now: datetime) -> list[str]:
    return [
        f"- {failure.name} — {failure.url} — {_age(failure.timestamp, now=now)} — {responsible}"
        for failure in failures
    ]


def _pull_request_report(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    pr: dict[str, Any],
    now: datetime,
    coverage_notices: list[str],
) -> list[str]:
    number = pr.get("number")
    if not isinstance(number, int):
        raise RuntimeError("GitHub returned a pull request without a numeric identifier.")
    raw_head = pr.get("head")
    head: dict[str, Any] = raw_head if isinstance(raw_head, dict) else {}
    sha = str(head.get("sha") or "").strip()
    branch = _single_line(head.get("ref"), fallback="unknown branch")
    if not sha:
        raise RuntimeError(f"GitHub PR #{number} has no readable head SHA.")
    responsible = f"PR #{number} ({branch})"
    failures = _failures_for_sha(
        client,
        owner=owner,
        repo=repo,
        sha=sha,
        responsible=responsible,
        coverage_notices=coverage_notices,
    )
    return _format_failures(failures, responsible=responsible, now=now)


def _single_pull_request_report(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    pr_number: int,
    now: datetime,
    coverage_notices: list[str],
) -> list[str]:
    path = f"/repos/{_segment(owner)}/{_segment(repo)}/pulls/{pr_number}"
    pr = client.request("GET", path)
    if not isinstance(pr, dict):
        raise RuntimeError(f"GitHub returned an invalid response for PR #{pr_number}.")
    return _pull_request_report(
        client,
        owner=owner,
        repo=repo,
        pr=pr,
        now=now,
        coverage_notices=coverage_notices,
    )


def _branch_report(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    branch: str,
    now: datetime,
    coverage_notices: list[str],
) -> list[str]:
    path = f"/repos/{_segment(owner)}/{_segment(repo)}/branches/{_segment(branch)}"
    branch_payload = client.request("GET", path)
    if not isinstance(branch_payload, dict):
        raise RuntimeError(f"GitHub returned an invalid response for branch {branch!r}.")
    raw_commit = branch_payload.get("commit")
    commit: dict[str, Any] = raw_commit if isinstance(raw_commit, dict) else {}
    sha = str(commit.get("sha") or "").strip()
    if not sha:
        raise RuntimeError(f"GitHub branch {branch!r} has no readable head SHA.")
    responsible = f"branch {_single_line(branch, fallback='unknown')}"
    failures = _failures_for_sha(
        client,
        owner=owner,
        repo=repo,
        sha=sha,
        responsible=responsible,
        coverage_notices=coverage_notices,
    )
    return _format_failures(failures, responsible=responsible, now=now)


def _repository_report(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    now: datetime,
    coverage_notices: list[str],
) -> list[str]:
    root = f"/repos/{_segment(owner)}/{_segment(repo)}"
    repository = client.request("GET", root)
    if not isinstance(repository, dict):
        raise RuntimeError(f"GitHub returned an invalid response for {owner}/{repo}.")
    default_branch = str(repository.get("default_branch") or "").strip()
    if not default_branch:
        raise RuntimeError(f"GitHub repository {owner}/{repo} has no readable default branch.")

    lines = _branch_report(
        client,
        owner=owner,
        repo=repo,
        branch=default_branch,
        now=now,
        coverage_notices=coverage_notices,
    )
    pull_requests = client.paginate(
        f"{root}/pulls",
        params={"state": "open", "per_page": 100},
        max_pages=2,
    )
    if len(pull_requests) > MAX_OPEN_PRS:
        coverage_notices.append(
            f"Coverage notice: report limited to the first {MAX_OPEN_PRS} open PRs."
        )
    for pr in pull_requests[:MAX_OPEN_PRS]:
        lines.extend(
            _pull_request_report(
                client,
                owner=owner,
                repo=repo,
                pr=pr,
                now=now,
                coverage_notices=coverage_notices,
            )
        )
    return lines


def run_github_ci_health(
    payload: AgentPayload,
    *,
    client: GitHubRestClient | None = None,
    now: datetime | None = None,
) -> str:
    """Fetch scoped CI health using GET requests only and render skill context."""
    owner = _required_text(payload, "owner")
    repo = _required_text(payload, "repo")
    branch = str(payload.get("branch") or "").strip()
    raw_pr = str(payload.get("pr_number") or "").strip()
    if branch and raw_pr:
        raise RuntimeError("GitHub CI health accepts either branch or pr_number, not both.")
    try:
        pr_number = int(raw_pr) if raw_pr else None
    except ValueError as exc:
        raise RuntimeError("GitHub CI health pr_number must be a positive integer.") from exc
    if pr_number is not None and pr_number < 1:
        raise RuntimeError("GitHub CI health pr_number must be a positive integer.")

    github = client or GitHubRestClient()
    checked_at = now or datetime.now(UTC)
    coverage_notices: list[str] = []
    try:
        if pr_number is not None:
            failures = _single_pull_request_report(
                github,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
                now=checked_at,
                coverage_notices=coverage_notices,
            )
            scope = f"PR #{pr_number}"
        elif branch:
            failures = _branch_report(
                github,
                owner=owner,
                repo=repo,
                branch=branch,
                now=checked_at,
                coverage_notices=coverage_notices,
            )
            scope = f"branch {_single_line(branch, fallback='unknown')}"
        else:
            failures = _repository_report(
                github,
                owner=owner,
                repo=repo,
                now=checked_at,
                coverage_notices=coverage_notices,
            )
            scope = "default branch and open PRs"
    except GitHubApiError as exc:
        raise RuntimeError(f"GitHub CI health read failed for {owner}/{repo}: {exc}") from exc

    heading = f"GitHub CI health — {owner}/{repo} — {scope}"
    if not failures:
        return "\n".join((heading, *coverage_notices, "No failing checks found."))
    return "\n".join((heading, *coverage_notices, *failures, "", _REPAIR_HANDOFF))


__all__ = [
    "CI_HEALTH_SKILL_NAME",
    "MAX_CHECK_RUNS_PER_SHA",
    "MAX_OPEN_PRS",
    "run_github_ci_health",
]
