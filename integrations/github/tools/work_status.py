"""GitHub work, PR, security, and issue-mutation workflow tools."""

from __future__ import annotations

import math
from typing import Any, Literal, cast

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_creds,
    github_source_available,
)
from integrations.github.tools.workflow import (
    GitHubIssueMutationProposal,
    PullRequestStatus,
    SecurityAlert,
    WorkItem,
    build_issue_mutation_proposal,
)

_HELP_WANTED_LABELS = {"help wanted", "good first issue", "up for grabs", "agent-ready"}
_BLOCKING_MERGEABLE_STATES = {"blocked", "dirty", "behind", "unstable"}
_FAILED_CHECK_CONCLUSIONS = {
    "failure",
    "cancelled",
    "timed_out",
    "action_required",
    "startup_failure",
}
_TERMINAL_CHECK_CONCLUSIONS = _FAILED_CHECK_CONCLUSIONS | {"success", "skipped", "neutral"}


def _github_available(sources: dict[str, dict]) -> bool:
    return bool(github_source_available(sources) or resolve_github_token(None))


def _github_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    return {"owner": gh.get("owner"), "repo": gh.get("repo"), **github_creds(gh)}


def _labels(item: dict[str, Any]) -> list[str]:
    return [
        str(label.get("name", "")).strip()
        for label in item.get("labels", [])
        if isinstance(label, dict)
    ]


def _logins(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [
        str(item.get("login", "")).strip()
        for item in items
        if isinstance(item, dict) and item.get("login")
    ]


def _normalize_issue(item: dict[str, Any]) -> WorkItem:
    labels = _labels(item)
    assignees = _logins(item.get("assignees"))
    label_set = {label.lower() for label in labels}
    if assignees:
        work_status: Literal["taken", "up_for_grabs", "unassigned"] = "taken"
    elif label_set & _HELP_WANTED_LABELS:
        work_status = "up_for_grabs"
    else:
        work_status = "unassigned"
    return WorkItem(
        number=item.get("number") if isinstance(item.get("number"), int) else None,
        title=str(item.get("title", "")),
        state=str(item.get("state", "")),
        url=str(item.get("html_url", "")),
        author=str((item.get("user") or {}).get("login", "")),
        labels=labels,
        assignees=assignees,
        updated_at=str(item.get("updated_at", "")),
        work_status=work_status,
    )


def _count_work_items(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(items),
        "taken": sum(1 for item in items if item.get("work_status") == "taken"),
        "up_for_grabs": sum(1 for item in items if item.get("work_status") == "up_for_grabs"),
        "unassigned": sum(1 for item in items if item.get("work_status") == "unassigned"),
    }


def _map_list_github_work_items(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    items = output.get("items")
    if not isinstance(items, list) or not items:
        return
    counts = output.get("counts") or {}
    record_evidence_entry(
        evidence,
        source="list_github_work_items",
        label="GitHub Work Items",
        summary=(
            f"{len(items)} items: {counts.get('taken', 0)} taken, "
            f"{counts.get('up_for_grabs', 0)} up for grabs, "
            f"{counts.get('unassigned', 0)} unassigned"
        ),
    )


def _map_summarize_github_pr_status(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    prs = output.get("pull_requests")
    if not isinstance(prs, list) or not prs:
        return
    counts = output.get("counts") or {}
    record_evidence_entry(
        evidence,
        source="summarize_github_pr_status",
        label="GitHub PR Status",
        summary=(
            f"{len(prs)} PRs: {counts.get('mergeable', 0)} mergeable, "
            f"{counts.get('blocked', 0)} blocked, "
            f"{counts.get('unknown', 0)} unknown"
        ),
    )


@tool(
    name="list_github_work_items",
    source="github",
    description="List GitHub issues as engineering work items and classify them as taken, up for grabs, or unassigned.",
    use_cases=[
        "Answering which GitHub issues are taken versus available",
        "Building engineering status reports from open issue state",
        "Finding unassigned or agent-ready work without mutating GitHub",
    ],
    anti_examples=["Creating, editing, or closing GitHub issues"],
    requires=["owner", "repo"],
    surfaces=(ToolSurface.CHAT,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    evidence_mapper=_map_list_github_work_items,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "state": {"type": "string", "enum": ["open", "closed", "all"]},
            "labels": {"type": "string"},
            "include_prs": {"type": "boolean"},
            "per_page": {"type": "integer"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_available,
    extract_params=_github_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
)
def list_github_work_items(
    owner: str,
    repo: str,
    state: str = "open",
    labels: str = "",
    include_prs: bool = False,
    per_page: int = 50,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    params: dict[str, Any] = {"state": state, "per_page": max(1, min(per_page, 100))}
    if labels.strip():
        params["labels"] = labels.strip()
    try:
        raw_items = GitHubRestClient(github_token).paginate(
            f"/repos/{owner}/{repo}/issues", params=params
        )
    except GitHubApiError as exc:
        return tool_unavailable(
            "github", str(exc), items=[], counts=_count_work_items([]), side_effects=[]
        )
    items = [
        _normalize_issue(item).to_dict()
        for item in raw_items
        if include_prs or "pull_request" not in item
    ]
    return {
        "source": "github",
        "available": True,
        "owner": owner,
        "repo": repo,
        "items": items,
        "counts": _count_work_items(items),
        "side_effects": [],
    }


def _check_summary(check_runs: list[dict[str, Any]]) -> tuple[str, list[str]]:
    failed = [
        str(run.get("name", "check"))
        for run in check_runs
        if str(run.get("conclusion") or "").lower() in _FAILED_CHECK_CONCLUSIONS
    ]
    pending = [
        str(run.get("name", "check"))
        for run in check_runs
        if str(run.get("status") or "").lower() != "completed"
        or str(run.get("conclusion") or "").lower() not in _TERMINAL_CHECK_CONCLUSIONS
    ]
    if failed:
        return "failed", failed
    if pending:
        return "pending", pending
    return "passing", []


def _normalize_pull_request(
    pr: dict[str, Any], check_runs: list[dict[str, Any]]
) -> PullRequestStatus:
    check_status, check_names = _check_summary(check_runs)
    mergeable = pr.get("mergeable") if isinstance(pr.get("mergeable"), bool) else None
    mergeable_state = str(pr.get("mergeable_state") or "unknown").lower()
    reasons: list[str] = []
    status: Literal["mergeable", "blocked", "unknown"]
    if pr.get("draft"):
        reasons.append("draft")
    if mergeable_state in _BLOCKING_MERGEABLE_STATES:
        reasons.append(f"mergeable_state={mergeable_state}")
    if check_status == "failed":
        reasons.append(f"failed checks: {', '.join(check_names)}")
    elif check_status == "pending":
        reasons.append(f"pending checks: {', '.join(check_names)}")
    if mergeable is None or mergeable_state == "unknown":
        reasons.append("mergeability unknown")
        status = "unknown"
    elif reasons or mergeable is False:
        status = "blocked"
        if mergeable is False and not any(
            reason.startswith("mergeable_state=") for reason in reasons
        ):
            reasons.append("mergeable=false")
    else:
        status = "mergeable"
    return PullRequestStatus(
        number=pr.get("number") if isinstance(pr.get("number"), int) else None,
        title=str(pr.get("title", "")),
        url=str(pr.get("html_url", "")),
        author=str((pr.get("user") or {}).get("login", "")),
        head_ref=str((pr.get("head") or {}).get("ref", "")),
        head_sha=str((pr.get("head") or {}).get("sha", "")),
        draft=bool(pr.get("draft")),
        mergeable=mergeable,
        mergeable_state=mergeable_state,
        check_status=check_status,
        status=status,
        mergeability=status,
        blocking_reasons=reasons,
        updated_at=str(pr.get("updated_at", "")),
    )


def _count_prs(prs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(prs),
        "mergeable": sum(1 for pr in prs if pr.get("status") == "mergeable"),
        "blocked": sum(1 for pr in prs if pr.get("status") == "blocked"),
        "unknown": sum(1 for pr in prs if pr.get("status") == "unknown"),
        "draft": sum(1 for pr in prs if pr.get("draft")),
    }


@tool(
    name="summarize_github_pr_status",
    source="github",
    description="Summarize open GitHub pull requests, authoritative mergeability, checks, and blocking reasons.",
    use_cases=[
        "Answering which PRs are mergeable, blocked, or unknown",
        "Finding failing or pending CI checks for active work",
        "Preparing engineering status updates without changing GitHub state",
    ],
    requires=["owner", "repo"],
    surfaces=(ToolSurface.CHAT,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    evidence_mapper=_map_summarize_github_pr_status,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "state": {"type": "string", "enum": ["open", "closed", "all"]},
            "per_page": {"type": "integer"},
            "include_checks": {"type": "boolean"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_available,
    extract_params=_github_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
)
def summarize_github_pr_status(
    owner: str,
    repo: str,
    state: str = "open",
    per_page: int = 30,
    include_checks: bool = True,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    client = GitHubRestClient(github_token)
    try:
        raw_prs = client.paginate(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": max(1, min(per_page, 100))},
        )
        prs: list[dict[str, Any]] = []
        for list_pr in raw_prs:
            number = list_pr.get("number")
            if not isinstance(number, int):
                continue
            detail_pr = client.request("GET", f"/repos/{owner}/{repo}/pulls/{number}")
            if not isinstance(detail_pr, dict):
                detail_pr = list_pr
            sha = str((detail_pr.get("head") or {}).get("sha", ""))
            check_runs: list[dict[str, Any]] = []
            if include_checks and sha:
                check_payload = client.request(
                    "GET",
                    f"/repos/{owner}/{repo}/commits/{sha}/check-runs",
                    params={"per_page": 100},
                )
                if isinstance(check_payload, dict) and isinstance(
                    check_payload.get("check_runs"), list
                ):
                    check_runs = [
                        run for run in check_payload["check_runs"] if isinstance(run, dict)
                    ]
            prs.append(_normalize_pull_request(detail_pr, check_runs).to_dict())
    except GitHubApiError as exc:
        return tool_unavailable(
            "github", str(exc), pull_requests=[], counts=_count_prs([]), side_effects=[]
        )
    return {
        "source": "github",
        "available": True,
        "owner": owner,
        "repo": repo,
        "pull_requests": prs,
        "counts": _count_prs(prs),
        "side_effects": [],
    }


def _normalize_security_alert(alert_type: str, item: dict[str, Any]) -> SecurityAlert:
    summary = ""
    if alert_type == "dependabot":
        summary = str((item.get("security_advisory") or {}).get("summary", ""))
    elif alert_type == "secret_scanning":
        summary = str(item.get("secret_type", ""))
    elif alert_type == "code_scanning":
        summary = str((item.get("rule") or {}).get("description", ""))
    return SecurityAlert(
        type=alert_type,
        number=item.get("number"),
        state=str(item.get("state", "")),
        summary=summary,
        url=str(item.get("html_url", "")),
    )


def _map_list_github_security_alerts(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    alerts = output.get("alerts", [])
    if alerts:
        count = len(alerts)
        word = "alert" if count == 1 else "alerts"
        record_evidence_entry(
            evidence,
            source="list_github_security_alerts",
            label="GitHub Security Alerts",
            summary=f"{count} {word}",
        )


_ALERT_ENDPOINTS = {
    "dependabot": "dependabot/alerts",
    "secret_scanning": "secret-scanning/alerts",
    "code_scanning": "code-scanning/alerts",
}

_ISSUE_MUTATION_OPERATIONS = {"create", "update", "close"}


@tool(
    name="list_github_security_alerts",
    source="github",
    description="List GitHub Dependabot, secret-scanning, and code-scanning alerts when token scope allows it.",
    use_cases=[
        "Surfacing repository security alerts during work triage",
        "Checking whether secret scanning or code scanning has open alerts",
        "Building a read-only engineering status report with security context",
    ],
    requires=["owner", "repo"],
    surfaces=(ToolSurface.CHAT,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "alert_type": {
                "type": "string",
                "enum": ["all", "dependabot", "secret_scanning", "code_scanning"],
            },
            "state": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_available,
    extract_params=_github_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
    evidence_mapper=_map_list_github_security_alerts,
)
def list_github_security_alerts(
    owner: str,
    repo: str,
    alert_type: str = "all",
    state: str = "open",
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    client = GitHubRestClient(github_token)
    selected = list(_ALERT_ENDPOINTS) if alert_type == "all" else [alert_type]
    alerts: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for kind in selected:
        endpoint = _ALERT_ENDPOINTS.get(kind)
        if endpoint is None:
            errors[kind] = f"Unsupported alert_type: {kind}"
            continue
        try:
            payload = client.paginate(
                f"/repos/{owner}/{repo}/{endpoint}", params={"state": state, "per_page": 100}
            )
        except GitHubApiError as exc:
            errors[kind] = str(exc)
            continue
        alerts.extend(_normalize_security_alert(kind, item).to_dict() for item in payload)
    counts = {
        kind: sum(1 for alert in alerts if alert.get("type") == kind) for kind in _ALERT_ENDPOINTS
    }
    counts["total"] = len(alerts)
    return {
        "source": "github",
        "available": not errors or bool(alerts),
        "owner": owner,
        "repo": repo,
        "alerts": alerts,
        "counts": counts,
        "errors": errors,
        "side_effects": [],
    }


@tool(
    name="propose_github_issue_mutation_from_slack",
    source="github",
    description="Build a read-only proposal for creating, updating, or closing a GitHub issue from an explicit Slack request.",
    use_cases=[
        "Preparing a Slack-sourced GitHub issue change for human approval",
        "Rendering deterministic issue mutation payloads without mutating GitHub",
    ],
    anti_examples=["Directly mutating GitHub", "Inferring tasks from ambiguous Slack discussion"],
    surfaces=(ToolSurface.CHAT,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "operation": {"type": "string", "enum": ["create", "update", "close"]},
            "issue_number": {"type": "integer"},
            "slack_text": {"type": "string"},
            "slack_url": {"type": "string"},
            "title": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string"}},
            "assignees": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["owner", "repo", "operation", "slack_text"],
    },
    is_available=_github_available,
    extract_params=_github_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
)
def propose_github_issue_mutation_from_slack(
    owner: str,
    repo: str,
    operation: Literal["create", "update", "close"],
    slack_text: str,
    slack_url: str = "",
    issue_number: int | None = None,
    title: str = "",
    labels: list[str] | None = None,
    assignees: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    if operation in {"update", "close"} and issue_number is None:
        return tool_unavailable(
            "github", f"issue_number is required for {operation}", side_effects=[]
        )
    proposal = build_issue_mutation_proposal(
        owner=owner,
        repo=repo,
        operation=operation,
        issue_number=issue_number,
        slack_text=slack_text,
        slack_url=slack_url,
        title=title,
        labels=labels,
        assignees=assignees,
    )
    return {
        "source": "github",
        "available": True,
        "proposal": proposal.to_dict(),
        "side_effects": [],
    }


def _mutation_rejected(error: str) -> dict[str, Any]:
    return tool_unavailable(
        "github", error, executed=False, side_effect="github_issue_mutation_rejected"
    )


def _proposal_from_payload(
    payload: Any,
) -> tuple[GitHubIssueMutationProposal | None, str | None]:
    if not isinstance(payload, dict):
        return None, "proposal must be an object"

    required = ("proposal_id", "operation", "owner", "repo", "target", "payload")
    missing = [key for key in required if key not in payload]
    if missing:
        return None, f"proposal missing required field(s): {', '.join(missing)}"

    operation = payload.get("operation")
    if operation not in _ISSUE_MUTATION_OPERATIONS:
        return None, f"unsupported proposal operation: {operation}"

    target = payload.get("target")
    if not isinstance(target, dict):
        return None, "proposal target must be an object"
    mutation_payload = payload.get("payload")
    if not isinstance(mutation_payload, dict):
        return None, "proposal payload must be an object"

    proposal_id = str(payload.get("proposal_id") or "").strip()
    owner = str(payload.get("owner") or "").strip()
    repo = str(payload.get("repo") or "").strip()
    idempotency_marker = str(payload.get("idempotency_marker") or "").strip()
    if not proposal_id:
        return None, "proposal_id is required"
    if not owner or not repo:
        return None, "proposal owner and repo are required"
    if not idempotency_marker or proposal_id not in idempotency_marker:
        return None, "proposal idempotency_marker is missing or does not match proposal_id"

    return (
        GitHubIssueMutationProposal(
            proposal_id=proposal_id,
            operation=cast(Literal["create", "update", "close"], operation),
            owner=owner,
            repo=repo,
            target=target,
            payload=mutation_payload,
            slack_url=str(payload.get("slack_url", "")),
            idempotency_marker=idempotency_marker,
        ),
        None,
    )


def _proposal_marker_text(proposal: GitHubIssueMutationProposal) -> str:
    if proposal.operation == "create":
        return str(proposal.payload.get("body", ""))
    return str(proposal.payload.get("comment_body", ""))


def _validate_proposal_marker(proposal: GitHubIssueMutationProposal) -> str | None:
    if proposal.idempotency_marker not in _proposal_marker_text(proposal):
        return "proposal payload does not include its idempotency marker"
    return None


# One page of the most-recent issues is enough to catch an idempotent retry,
# which lands within moments of the original creation, not months later.
_RECENT_ISSUES_SCAN_LIMIT = 100


def _recent_issues_with_marker(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    marker: str,
) -> list[dict[str, Any]]:
    """Recently-created issues whose body contains ``marker``.

    Uses the issues list endpoint rather than /search/issues: GitHub's search
    index has an observed multi-second propagation lag after issue creation,
    which lets a rapid idempotent retry slip past a search-based dedup check
    and create a duplicate issue (reproduced live).
    """
    result = client.request(
        "GET",
        f"/repos/{owner}/{repo}/issues",
        params={
            "state": "all",
            "per_page": _RECENT_ISSUES_SCAN_LIMIT,
            "sort": "created",
            "direction": "desc",
        },
    )
    if not isinstance(result, list):
        return []
    return [
        item
        for item in result
        if isinstance(item, dict)
        and "pull_request" not in item
        and marker in str(item.get("body") or "")
    ]


def _marker_exists_on_issue(
    client: GitHubRestClient,
    *,
    owner: str,
    repo: str,
    issue_number: int,
    marker: str,
    total_comments: int,
) -> bool:
    """Whether a comment containing ``marker`` already exists on the issue.

    Lists the issue's own comments (bounded to one issue, immediately
    consistent) rather than the repo-wide search API, for the same reason as
    :func:`_recent_issues_with_marker`. Unlike the repo-wide comments
    endpoint, this per-issue one does not support `sort`/`direction` and
    always returns oldest-first, so a plain first-page fetch would miss a
    marker on an issue with more than one page of comments -- exactly the
    case an idempotency check has to catch. Fetches the last page directly,
    computed from the issue's own comment count, so the newest comments
    (where a retry's marker lands) are always the ones scanned.
    """
    per_page = 100
    last_page = max(1, math.ceil(max(total_comments, 1) / per_page))
    result = client.request(
        "GET",
        f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
        params={"per_page": per_page, "page": last_page},
    )
    if not isinstance(result, list):
        return False
    return any(isinstance(item, dict) and marker in str(item.get("body") or "") for item in result)


@tool(
    name="execute_github_issue_mutation",
    source="github",
    description="Execute a GitHub issue mutation proposal. Not exposed to investigation.",
    use_cases=["Executing a previously rendered GitHub issue mutation proposal"],
    anti_examples=[
        "Creating proposals",
        "Running during investigations",
    ],
    surfaces=(ToolSurface.CHAT,),
    side_effect_level=SideEffectLevel.MUTATING,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "proposal": {"type": "object"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo", "proposal"],
    },
    is_available=_github_available,
    extract_params=_github_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
)
def execute_github_issue_mutation(
    owner: str,
    repo: str,
    proposal: dict[str, Any],
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    client = GitHubRestClient(github_token)
    parsed, parse_error = _proposal_from_payload(proposal)
    if parsed is None:
        return _mutation_rejected(parse_error or "invalid proposal")
    if parsed.owner != owner or parsed.repo != repo:
        return _mutation_rejected("proposal owner/repo does not match request")
    marker_error = _validate_proposal_marker(parsed)
    if marker_error is not None:
        return _mutation_rejected(marker_error)
    try:
        if parsed.operation == "create":
            existing_items = _recent_issues_with_marker(
                client,
                owner=owner,
                repo=repo,
                marker=parsed.idempotency_marker,
            )
            if existing_items:
                return {
                    "source": "github",
                    "available": True,
                    "executed": False,
                    "side_effect": "existing_github_issue",
                    "issue": existing_items[0],
                }
            issue = client.request("POST", f"/repos/{owner}/{repo}/issues", body=parsed.payload)
            return {
                "source": "github",
                "available": True,
                "executed": True,
                "side_effect": "created_github_issue",
                "issue": issue,
            }
        issue_number = parsed.target.get("issue_number")
        if not isinstance(issue_number, int):
            return _mutation_rejected("proposal target.issue_number is required")
        issue_before = client.request("GET", f"/repos/{owner}/{repo}/issues/{issue_number}")
        total_comments = issue_before.get("comments", 0) if isinstance(issue_before, dict) else 0
        comment_body = str(parsed.payload.get("comment_body", ""))
        comment_already_recorded = _marker_exists_on_issue(
            client,
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            marker=parsed.idempotency_marker,
            total_comments=total_comments,
        )
        if comment_body and not comment_already_recorded:
            client.request(
                "POST",
                f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
                body={"body": comment_body},
            )
        if parsed.operation == "update":
            patch_body: dict[str, Any] = {
                key: parsed.payload[key]
                for key in ("title", "labels", "assignees")
                if key in parsed.payload
            }
            issue = (
                client.request(
                    "PATCH", f"/repos/{owner}/{repo}/issues/{issue_number}", body=patch_body
                )
                if patch_body
                else {"number": issue_number}
            )
            return {
                "source": "github",
                "available": True,
                "executed": True,
                "side_effect": "updated_github_issue",
                "issue": issue,
                "comment_already_recorded": comment_already_recorded,
            }
        issue = client.request(
            "PATCH",
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            body={"state": "closed", "state_reason": "completed"},
        )
        return {
            "source": "github",
            "available": True,
            "executed": True,
            "side_effect": "closed_github_issue",
            "issue": issue,
            "comment_already_recorded": comment_already_recorded,
        }
    except GitHubApiError as exc:
        return tool_unavailable(
            "github",
            str(exc),
            executed=False,
            side_effect=f"{parsed.operation}_github_issue_failed",
        )
