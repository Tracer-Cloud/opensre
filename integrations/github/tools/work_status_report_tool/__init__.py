"""Slack-ready engineering work status report tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.github.client import resolve_github_token
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_credentials_available,
    github_creds,
    github_source_available,
)
from integrations.github.tools.work_status import list_github_work_items, summarize_github_pr_status
from integrations.github.tools.workflow import build_work_status_report


def _report_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(
        (github_source_available(sources) or resolve_github_token(None))
        and gh.get("owner")
        and gh.get("repo")
    )


def _report_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    return {"owner": gh.get("owner"), "repo": gh.get("repo"), **github_creds(gh)}


def _map_generate_work_status_report(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    if output.get("slack_text"):
        record_evidence_entry(
            evidence,
            source="generate_work_status_report",
            label="Work Status Report",
            summary="Engineering work status report",
        )


@tool(
    name="generate_work_status_report",
    source="github",
    description="Generate a Slack-ready engineering status report from GitHub work items and PR status without mutating state.",
    use_cases=[
        "Answering what is left to do today",
        "Drafting morning check-ins with open work, owners, blockers, and next actions",
        "Summarizing GitHub work status for Slack without changing GitHub",
    ],
    anti_examples=["Creating or updating tasks", "Posting to Slack directly"],
    surfaces=(ToolSurface.CHAT,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "context": {"type": "string"},
            "work_items": {"type": "array"},
            "pull_requests": {"type": "array"},
            "github_token": {"type": "string"},
        },
        "required": [],
    },
    is_available=_report_available,
    is_advertised=github_credentials_available,
    extract_params=_report_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
    evidence_mapper=_map_generate_work_status_report,
)
def generate_work_status_report(
    owner: str = "",
    repo: str = "",
    context: str = "today",
    work_items: list[dict[str, Any]] | None = None,
    pull_requests: list[dict[str, Any]] | None = None,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    errors: list[str] = []
    if work_items is None and owner and repo:
        work_result = list_github_work_items(owner=owner, repo=repo, github_token=github_token)
        if not work_result.get("available", False):
            errors.append(f"work_items: {work_result.get('error', 'unavailable')}")
        work_items = list(work_result.get("items", []))
    if pull_requests is None and owner and repo:
        pr_result = summarize_github_pr_status(owner=owner, repo=repo, github_token=github_token)
        if not pr_result.get("available", False):
            errors.append(f"pull_requests: {pr_result.get('error', 'unavailable')}")
        pull_requests = list(pr_result.get("pull_requests", []))
    report = build_work_status_report(
        work_items=work_items or [],
        pull_requests=pull_requests or [],
        context=context,
        errors=errors,
    )
    return {"source": "github", **report.to_dict()}
