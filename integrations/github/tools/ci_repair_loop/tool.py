"""Schedule and inspect bounded, local GitHub repair loops."""

from __future__ import annotations

import time
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
from integrations.github.tools.ci_repair_loop.models import RepairRun
from integrations.github.tools.ci_repair_loop.report import render_report
from integrations.github.tools.ci_repair_loop.schedule import schedule_repair
from integrations.github.tools.ci_repair_loop.storage import RepairStore


def _credentials(sources: dict[str, dict]) -> dict[str, Any]:
    return github_creds(sources.get("github", {}))


def _result(run: RepairRun, store: RepairStore) -> dict[str, Any]:
    return {
        "ok": True,
        "task_id": run.id,
        "status": run.status.value,
        "terminal": run.terminal,
        "deadline": run.deadline,
        "pr_url": run.pr_url,
        "repository_url": run.repository_url,
        "response_text": render_report(run, store.directory(run.id)),
    }


@tool(
    name="schedule_ci_repair_loop",
    source="github",
    display_name="Schedule bounded CI repair",
    use_cases=[
        "Run the scheduled CI repair onboarding demo",
        "Repair one selected PR in the background",
    ],
    description=(
        "Schedule repair of one GitHub PR, or demo=true for a tiny CI repair demonstration "
        "in a fixed reusable private repository. Starts and checks the local background "
        "scheduler, uses a real minute trigger, stops within ten minutes, and retains a "
        "linked outcome report. Reuses the active run without extending its deadline."
    ),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    requires_approval=True,
    approval_reason="Starts a background service and authorizes a bounded worker to create demo resources or edit and push the selected PR.",
    parallel_safe=False,
    is_available=github_source_available,
    extract_params=_credentials,
    injected_params=GITHUB_INJECTED_PARAMS,
    input_schema={
        "type": "object",
        "properties": {
            "demo": {
                "type": "boolean",
                "description": "Use the reusable private demo repository; default false.",
            },
            "owner": {
                "type": "string",
                "description": "Explicit owner or organization; demo defaults to authenticated user.",
            },
            "repo": {
                "type": "string",
                "description": "Repository for an existing PR; omitted in demo mode.",
            },
            "pr_number": {
                "type": "integer",
                "minimum": 1,
                "description": "Existing PR to repair; omitted in demo mode.",
            },
        },
        "additionalProperties": False,
    },
)
def schedule_ci_repair_loop(
    demo: bool = False,
    owner: str = "",
    repo: str = "",
    pr_number: int = 0,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Authorize exactly one bounded repair scope and return its durable identity."""
    try:
        store = RepairStore()
        run, reused, next_run = schedule_repair(
            demo=demo,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            github_token=github_token,
            store=store,
        )
    except (ValueError, RuntimeError, OSError, GitHubApiError) as exc:
        report_run_error(
            exc,
            tool_name="schedule_ci_repair_loop",
            source="github",
            component=__name__,
            method="schedule_repair",
        )
        return {
            "ok": False,
            "error": f"Could not schedule CI repair: {type(exc).__name__}.",
            "response_text": "Check the GitHub connection and background scheduler setup.",
        }
    return {**_result(run, store), "reused": reused, "next_run": next_run}


@tool(
    name="get_ci_repair_loop",
    source="github",
    display_name="Inspect CI repair",
    use_cases=[
        "Observe an active CI repair run",
        "Retrieve a completed repair report and its evidence links",
    ],
    description="Read the linked summary of a CI repair run. Optionally wait up to sixty seconds for completion; never starts another repair.",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    parallel_safe=True,
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "Run id returned by schedule_ci_repair_loop.",
            },
            "wait_seconds": {
                "type": "integer",
                "minimum": 0,
                "maximum": 60,
                "description": "Seconds to wait for a terminal result; default zero.",
            },
        },
        "required": ["task_id"],
        "additionalProperties": False,
    },
)
def get_ci_repair_loop(task_id: str, wait_seconds: int = 0, **_kwargs: Any) -> dict[str, Any]:
    """Retrieve the same report while the shell is open or after returning later."""
    until = time.monotonic() + min(60, max(0, wait_seconds))
    try:
        store = RepairStore()
        while True:
            run = store.get(task_id)
            if run.terminal or time.monotonic() >= until:
                return _result(run, store)
            time.sleep(min(1, max(0, until - time.monotonic())))
    except (ValueError, OSError, RuntimeError) as exc:
        report_run_error(
            exc,
            tool_name="get_ci_repair_loop",
            source="github",
            component=__name__,
            method="RepairStore.get",
        )
        return {"ok": False, "error": str(exc)}
