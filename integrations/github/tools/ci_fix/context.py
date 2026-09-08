"""Resolve GitHub PR or branch CI failures into a coding task."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Final
from urllib.parse import quote

from infrastructure.safety.masking import MaskingPolicy, MaskingRules
from integrations.github.repo_scope import detect_git_remote_repo_scope
from integrations.github.tools.ci_fix.errors import (
    ERR_INVALID_INPUT,
    ERR_NO_FAILING_CHECKS,
    ERR_PR_NOT_OPEN,
    ERR_UNSUPPORTED_PR_BRANCH,
    GitHubCiFixError,
)
from integrations.github.tools.ci_fix.gh import run_gh_json, run_gh_text

_PR_URL_RE = re.compile(
    r"https?://github\.com/"
    r"(?P<owner>[^/\s]+)/(?P<repo>[^/\s#?]+)/pull/(?P<number>\d+)",
    re.IGNORECASE,
)
_ACTIONS_URL_RE = re.compile(
    r"/actions/runs/(?P<run_id>\d+)(?:/job/(?P<job_id>\d+))?",
    re.IGNORECASE,
)
CI_TARGET_BRANCH: Final = "branch"
CI_TARGET_PR: Final = "pr"
# GitHub reports a PR whose head conflicts with its base as DIRTY and will not
# start pull_request workflows for it because no merge commit can be built.
MERGE_STATE_DIRTY: Final = "DIRTY"
_MERGE_STATE_UNKNOWN: Final = "UNKNOWN"
_MERGEABLE_CONFLICTING: Final = "CONFLICTING"
_MERGE_STATE_FIELDS: Final = "mergeStateStatus,mergeable"
_MERGE_STATE_RETRIES = 3
_MERGE_STATE_RETRY_SECONDS = 3.0
# CANCELLED is omitted: cancelled siblings of a real failure are noise, not a
# second root cause for the coding agent to chase.
_FAILED_CONCLUSIONS = frozenset({"ACTION_REQUIRED", "FAILURE", "STARTUP_FAILURE", "TIMED_OUT"})
_FAILED_STATES = frozenset({"ERROR", "FAILURE", "FAILED"})
_PR_FIELDS = ",".join(
    [
        "number",
        "title",
        "url",
        "headRefName",
        "headRepositoryOwner",
        "headRepository",
        "headRefOid",
        "baseRefName",
        "isCrossRepository",
        "mergeStateStatus",
        "mergeable",
        "state",
        "statusCheckRollup",
    ]
)
_BRANCH_RUN_FIELDS = "databaseId,name,workflowName,conclusion,status,url"
_BRANCH_RUNS_KEY = "runs"
_MAX_LOG_CHARS = 7000
_MAX_TASK_LOG_CHARS = 18000


@dataclass(frozen=True)
class PullRequestRef:
    """Repository and PR identity parsed from a GitHub PR URL."""

    owner: str
    repo: str
    number: int


@dataclass(frozen=True)
class FailingCheck:
    """A failing PR check with optional GitHub Actions log context."""

    name: str
    conclusion: str
    details_url: str
    workflow_name: str
    run_id: str = ""
    job_id: str = ""
    log_excerpt: str = ""


@dataclass(frozen=True)
class CiFixContext:
    """Resolved CI failure (PR or branch target) and coding-agent task."""

    owner: str
    repo: str
    number: int | None
    title: str
    url: str
    base_branch: str
    head_branch: str
    head_sha: str
    skipped_check_names: tuple[str, ...]
    failing_checks: tuple[FailingCheck, ...]
    task: str
    target_kind: str = CI_TARGET_PR
    target_branch: str = ""
    merge_state: str = ""

    @property
    def needs_base_merge(self) -> bool:
        """True when GitHub cannot merge the PR head, so its checks will not start."""
        return not self.is_branch_target and self.merge_state == MERGE_STATE_DIRTY

    @property
    def is_branch_target(self) -> bool:
        """True when the fix targets a named branch via a repair worktree, not a PR head."""
        return self.number is None or self.target_kind == CI_TARGET_BRANCH

    @property
    def target_label(self) -> str:
        if self.is_branch_target:
            branch = self.target_branch or self.base_branch or self.head_branch
            return f"{self.owner}/{self.repo}@{branch}"
        return f"{self.owner}/{self.repo}#{self.number}"


def parse_pr_url(pr_url: str | None) -> PullRequestRef | None:
    """Parse a GitHub pull request URL."""
    if not pr_url:
        return None
    match = _PR_URL_RE.search(pr_url.strip())
    if match is None:
        return None
    return PullRequestRef(
        owner=match.group("owner"),
        repo=match.group("repo").removesuffix(".git"),
        number=int(match.group("number")),
    )


def gather_ci_fix_context(
    *,
    owner: str | None = None,
    repo: str | None = None,
    pr_number: int | None = None,
    pr_url: str | None = None,
    workspace: str | None = None,
    github_token: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> CiFixContext:
    """Resolve PR metadata, merge state, failing checks, and log snippets.

    A PR that conflicts with its base is accepted even without failing checks:
    GitHub never starts checks for it, so bringing the base in is the fix.
    """
    parsed_url = parse_pr_url(pr_url)
    repo_owner = (parsed_url.owner if parsed_url else owner or "").strip()
    repo_name = (parsed_url.repo if parsed_url else repo or "").strip().removesuffix(".git")
    number = parsed_url.number if parsed_url else pr_number

    if not repo_owner or not repo_name:
        detected = detect_git_remote_repo_scope(workspace)
        if detected is not None:
            repo_owner, repo_name = detected
    if not repo_owner or not repo_name:
        raise GitHubCiFixError(
            ERR_INVALID_INPUT,
            "owner/repo is required unless pr_url or the workspace origin identifies a GitHub repo; no push was made.",
        )

    repo_full_name = f"{repo_owner}/{repo_name}"
    pr_selector = str(number) if number is not None else ""
    args = ["pr", "view"]
    if pr_selector:
        args.append(pr_selector)
    args.extend(["--json", _PR_FIELDS])
    pr = run_gh_json(args, repo=repo_full_name, github_token=github_token)

    resolved_number = _int_value(pr.get("number"))
    if resolved_number is None:
        raise GitHubCiFixError(
            ERR_INVALID_INPUT,
            "GitHub PR metadata did not include a PR number; no push was made.",
        )

    state = str(pr.get("state") or "").strip().upper()
    if state and state != "OPEN":
        raise GitHubCiFixError(
            ERR_PR_NOT_OPEN,
            (
                f"{repo_full_name}#{resolved_number} is {state.lower()}, not open; "
                "CI fixes push to open PR heads or an explicitly requested branch, "
                "so no push was made."
            ),
        )

    head_repo = _head_repo_full_name(pr)
    is_cross_repo = bool(pr.get("isCrossRepository"))
    if is_cross_repo or head_repo.lower() != repo_full_name.lower():
        head_branch = str(pr.get("headRefName") or "").strip()
        target = f"{head_repo}:{head_branch}" if head_repo else head_branch
        raise GitHubCiFixError(
            ERR_UNSUPPORTED_PR_BRANCH,
            (
                f"{repo_full_name}#{resolved_number} uses branch {target or '(unknown)'}; "
                "OpenSRE only pushes CI fixes to branches in the same repository, so no push was made."
            ),
        )

    merge_state = _settled_merge_state(
        pr, repo=repo_full_name, number=resolved_number, github_token=github_token, sleep=sleep
    )
    rollup = _list_value(pr.get("statusCheckRollup"))
    checks = tuple(
        _failing_check_from_rollup(repo_full_name, item, github_token=github_token)
        for item in rollup
        if _is_failing_check(item)
    )
    if not checks and merge_state != MERGE_STATE_DIRTY:
        raise GitHubCiFixError(
            ERR_NO_FAILING_CHECKS,
            f"No failing CI checks found on {repo_full_name}#{resolved_number}; no push was made.",
        )

    title = str(pr.get("title") or "").strip()
    url = str(pr.get("url") or f"https://github.com/{repo_full_name}/pull/{resolved_number}")
    head_branch = str(pr.get("headRefName") or "").strip()
    ctx = CiFixContext(
        owner=repo_owner,
        repo=repo_name,
        number=resolved_number,
        title=title,
        url=url,
        base_branch=str(pr.get("baseRefName") or "").strip(),
        head_branch=head_branch,
        head_sha=str(pr.get("headRefOid") or "").strip(),
        skipped_check_names=tuple(_check_name(item) for item in rollup if _is_skipped(item)),
        failing_checks=checks,
        task="",
        target_kind=CI_TARGET_PR,
        target_branch=head_branch,
        merge_state=merge_state,
    )
    return replace(ctx, task=_build_task(ctx) if checks else "")


def _settled_merge_state(
    pr: dict[str, Any],
    *,
    repo: str,
    number: int,
    github_token: str | None,
    sleep: Callable[[float], None],
) -> str:
    """Return the PR merge state, re-reading while GitHub is still computing it."""
    state = _merge_state(pr)
    for _attempt in range(_MERGE_STATE_RETRIES):
        if state != _MERGE_STATE_UNKNOWN:
            return state
        sleep(_MERGE_STATE_RETRY_SECONDS)
        state = _merge_state(
            run_gh_json(
                ["pr", "view", str(number), "--json", _MERGE_STATE_FIELDS],
                repo=repo,
                github_token=github_token,
            )
        )
    return state


def _merge_state(pr: dict[str, Any]) -> str:
    state = str(pr.get("mergeStateStatus") or "").strip().upper()
    mergeable = str(pr.get("mergeable") or "").strip().upper()
    if mergeable == _MERGEABLE_CONFLICTING:
        return MERGE_STATE_DIRTY
    return state or _MERGE_STATE_UNKNOWN


def gather_branch_ci_fix_context(
    *,
    branch: str,
    owner: str | None = None,
    repo: str | None = None,
    workspace: str | None = None,
    github_token: str | None = None,
) -> CiFixContext:
    """Resolve a branch's failing workflow runs and log snippets (no PR involved)."""
    branch_name = _normalize_branch(branch)
    if not branch_name:
        raise GitHubCiFixError(
            ERR_INVALID_INPUT,
            "A branch name is required to fix branch CI; no push was made.",
        )
    repo_owner = (owner or "").strip()
    repo_name = (repo or "").strip().removesuffix(".git")
    if not repo_owner or not repo_name:
        detected = detect_git_remote_repo_scope(workspace)
        if detected is not None:
            repo_owner, repo_name = detected
    if not repo_owner or not repo_name:
        raise GitHubCiFixError(
            ERR_INVALID_INPUT,
            "owner/repo is required unless the workspace origin identifies a GitHub repo; no push was made.",
        )

    repo_full_name = f"{repo_owner}/{repo_name}"
    # Branch names may contain URL-reserved characters (feat/x); escape them so
    # the REST path keeps the name as a single segment.
    branch_path = quote(branch_name, safe="")
    head = run_gh_json(
        ["api", f"repos/{repo_full_name}/branches/{branch_path}", "--jq", '{"sha": .commit.sha}'],
        repo=repo_full_name,
        github_token=github_token,
        repo_flag=False,
    )
    head_sha = str(head.get("sha") or "").strip()
    if not head_sha:
        raise GitHubCiFixError(
            ERR_INVALID_INPUT,
            f"Could not resolve the head commit of {repo_full_name}@{branch_name}; no push was made.",
        )

    payload = run_gh_json(
        [
            "run",
            "list",
            "--commit",
            head_sha,
            "--limit",
            "100",
            "--json",
            _BRANCH_RUN_FIELDS,
            "--jq",
            f'{{"{_BRANCH_RUNS_KEY}": .}}',
        ],
        repo=repo_full_name,
        github_token=github_token,
    )
    runs = _list_value(payload.get(_BRANCH_RUNS_KEY))
    checks = tuple(
        _failing_check_from_run(repo_full_name, run, github_token=github_token)
        for run in runs
        if _is_failing_check(run)
    )
    if not checks:
        raise GitHubCiFixError(
            ERR_NO_FAILING_CHECKS,
            (
                f"No failing workflow runs found on {repo_full_name}@{branch_name} "
                f"(head {head_sha[:12]}); no push was made."
            ),
        )

    ctx = CiFixContext(
        owner=repo_owner,
        repo=repo_name,
        number=None,
        title="",
        url=f"https://github.com/{repo_full_name}/tree/{branch_name}",
        base_branch=branch_name,
        head_branch=branch_name,
        head_sha=head_sha,
        skipped_check_names=tuple(_check_name(run) for run in runs if _is_skipped(run)),
        failing_checks=checks,
        task="",
        target_kind=CI_TARGET_BRANCH,
        target_branch=branch_name,
    )
    return replace(ctx, task=_build_task(ctx))


def _failing_check_from_run(
    repo_full_name: str,
    run: dict[str, Any],
    *,
    github_token: str | None,
) -> FailingCheck:
    run_id = str(run.get("databaseId") or "")
    log_excerpt = ""
    if run_id:
        log_excerpt = _fetch_log_excerpt(
            repo_full_name,
            run_id=run_id,
            job_id="",
            github_token=github_token,
            failed_only=True,
        )
    return FailingCheck(
        name=_check_name(run),
        conclusion=str(run.get("conclusion") or "").lower(),
        details_url=str(run.get("url") or ""),
        workflow_name=str(run.get("workflowName") or run.get("name") or ""),
        run_id=run_id,
        log_excerpt=log_excerpt,
    )


def _failing_check_from_rollup(
    repo_full_name: str,
    item: dict[str, Any],
    *,
    github_token: str | None,
) -> FailingCheck:
    details_url = str(item.get("detailsUrl") or item.get("targetUrl") or "")
    run_id, job_id = _actions_ids(details_url)
    log_excerpt = ""
    if run_id:
        log_excerpt = _fetch_log_excerpt(
            repo_full_name,
            run_id=run_id,
            job_id=job_id,
            github_token=github_token,
        )
    return FailingCheck(
        name=str(item.get("name") or item.get("context") or "unnamed check"),
        conclusion=str(item.get("conclusion") or item.get("state") or "").lower(),
        details_url=details_url,
        workflow_name=str(item.get("workflowName") or ""),
        run_id=run_id,
        job_id=job_id,
        log_excerpt=log_excerpt,
    )


def _fetch_log_excerpt(
    repo_full_name: str,
    *,
    run_id: str,
    job_id: str,
    github_token: str | None,
    failed_only: bool = False,
) -> str:
    args = ["run", "view", run_id, "--log-failed" if failed_only else "--log"]
    if job_id:
        args.extend(["--job", job_id])
    try:
        raw = run_gh_text(args, repo=repo_full_name, github_token=github_token, timeout=180)
    except GitHubCiFixError as exc:
        return f"Log unavailable: {exc.message}"
    return _log_excerpt(raw)


def _log_excerpt(raw: str) -> str:
    lines = raw.splitlines()
    include: set[int] = set()
    for index, line in enumerate(lines):
        lowered = line.lower()
        if any(
            marker in lowered
            for marker in ("::error", "error:", "failed", "failure", "traceback", "exception")
        ):
            include.update(range(max(0, index - 3), min(len(lines), index + 8)))
    if include:
        # Emit each line once, in order, with blank separators between regions.
        # Overlapping windows used to be appended repeatedly, so a dense error
        # region near the end of the log could evict the actual failure from
        # the tail-truncated excerpt.
        interesting: list[str] = []
        previous: int | None = None
        for index in sorted(include):
            if previous is not None and index > previous + 1:
                interesting.append("")
            interesting.append(lines[index])
            previous = index
    else:
        interesting = lines[-80:]
    excerpt = "\n".join(interesting).strip()
    return excerpt[-_MAX_LOG_CHARS:]


def _build_task(ctx: CiFixContext) -> str:
    masker = MaskingRules(MaskingPolicy.from_env())
    if ctx.is_branch_target:
        branch = ctx.target_branch or ctx.base_branch or ctx.head_branch
        lines = [
            f"Fix the failing GitHub Actions workflow runs on {ctx.owner}/{ctx.repo} "
            f"branch {branch}.",
            "",
            f"Branch: {branch}",
            f"Failing commit SHA: {ctx.head_sha}",
            f"Branch URL: {ctx.url}",
            "The workspace is a fresh OpenSRE repair branch based on the target branch.",
            "Repair every failing check listed below from all failing workflows on that commit.",
            "",
            "Failing checks and log excerpts:",
        ]
    else:
        lines = [
            f"Fix the failing GitHub Actions CI checks for {ctx.owner}/{ctx.repo} PR #{ctx.number}.",
            "",
            f"PR: {ctx.url}",
            f"Title: {ctx.title}",
            f"Base branch: {ctx.base_branch}",
            f"Head branch to edit and push: {ctx.head_branch}",
            f"Head SHA: {ctx.head_sha}",
        ]
        if ctx.needs_base_merge:
            lines.append(
                f"{ctx.base_branch} has already been merged into the workspace; "
                f"the checks below ran on the pre-merge head {ctx.head_sha}."
            )
        lines.extend(["", "Failing checks and log excerpts:"])
    log_budget = _MAX_TASK_LOG_CHARS
    for check in ctx.failing_checks:
        lines.extend(
            [
                "",
                f"- Check: {check.name}",
                f"  Workflow: {check.workflow_name}",
                f"  Conclusion: {check.conclusion}",
                f"  Details: {check.details_url}",
            ]
        )
        if check.log_excerpt and log_budget > 0:
            excerpt = check.log_excerpt[:log_budget]
            log_budget -= len(excerpt)
            lines.extend(["  Log excerpt:", _indent(excerpt, prefix="    ")])
    lines.extend(
        [
            "",
            "Make the smallest repository change that addresses the observed CI failure.",
            "Do not silence CI, skip tests, or weaken checks unless the log proves the check itself is wrong.",
            "Preserve unrelated user changes. Run the smallest relevant local verification command when practical.",
            "Finish with a concise summary of files changed and verification performed.",
        ]
    )
    return "\n".join(masker.mask(line) for line in lines)


def _actions_ids(details_url: str) -> tuple[str, str]:
    match = _ACTIONS_URL_RE.search(details_url)
    if match is None:
        return "", ""
    return match.group("run_id"), match.group("job_id") or ""


def _head_repo_full_name(pr: dict[str, Any]) -> str:
    head_repo = pr.get("headRepository")
    if isinstance(head_repo, dict):
        name_with_owner = str(head_repo.get("nameWithOwner") or "").strip()
        if name_with_owner:
            return name_with_owner
        name = str(head_repo.get("name") or "").strip()
    else:
        name = ""
    owner = pr.get("headRepositoryOwner")
    owner_login = str(owner.get("login") or "").strip() if isinstance(owner, dict) else ""
    return f"{owner_login}/{name}" if owner_login and name else ""


def _is_failing_check(item: dict[str, Any]) -> bool:
    conclusion = str(item.get("conclusion") or "").strip().upper()
    state = str(item.get("state") or "").strip().upper()
    return conclusion in _FAILED_CONCLUSIONS or state in _FAILED_STATES


def _check_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or item.get("context") or "unnamed check")


def _is_skipped(item: dict[str, Any]) -> bool:
    return str(item.get("conclusion") or "").strip().upper() == "SKIPPED"


def _list_value(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _int_value(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _normalize_branch(branch: str | None) -> str:
    cleaned = str(branch or "").strip()
    for prefix in ("refs/remotes/origin/", "refs/heads/", "origin/"):
        if cleaned.startswith(prefix):
            return cleaned.removeprefix(prefix).strip()
    return cleaned


def _indent(value: str, *, prefix: str) -> str:
    return "\n".join(f"{prefix}{line}" if line else "" for line in value.splitlines())


__all__ = [
    "CI_TARGET_BRANCH",
    "CI_TARGET_PR",
    "MERGE_STATE_DIRTY",
    "CiFixContext",
    "FailingCheck",
    "PullRequestRef",
    "gather_branch_ci_fix_context",
    "gather_ci_fix_context",
    "parse_pr_url",
]
