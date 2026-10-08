"""Schedule and inspect bounded GitHub repair loops on the current host."""

from __future__ import annotations

import time
from typing import Any

from config.constants.capabilities import SCHEDULER_HOST_CAPABILITY, SCHEDULER_HOST_IN_PROCESS
from core.agent_harness.tools import action_context_from_agent_context, capability_values
from core.domain.types.tools import ToolSurface
from core.tool import ERROR_KIND_REFUSED, SideEffectLevel, report_run_error
from core.tool_framework import tool
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
from integrations.github.tools.ci_repair_loop.credentials import account_id, configured_token
from integrations.github.tools.ci_repair_loop.models import RepairRefused, RepairRun
from integrations.github.tools.ci_repair_loop.report import render_report
from integrations.github.tools.ci_repair_loop.responses import object_response
from integrations.github.tools.ci_repair_loop.schedule import schedule_repair
from integrations.github.tools.ci_repair_loop.storage import RepairStore


def _credentials(sources: dict[str, dict]) -> dict[str, Any]:
    return github_creds(sources.get("github", {}))


def _scheduler_in_process(context: Any) -> bool:
    """Whether the host's own scheduler picks tasks up from the store (the hosted gateway)."""
    if context is None:
        return False
    try:
        scope = action_context_from_agent_context(context)
    except RuntimeError:
        return False
    hosts = capability_values(scope.session, SCHEDULER_HOST_CAPABILITY)
    return SCHEDULER_HOST_IN_PROCESS in hosts


_NO_RUNS_YET = "This GitHub account has no CI repair runs yet, so there is nothing to report."


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


#: The tool's error line when the target itself was refused; the reason says which to choose.
_REFUSED_ERROR = "Could not schedule CI repair: the pull request was refused. {reason}"
_SCHEDULE_ERROR = "Could not schedule CI repair: {cause}"
_READ_ERROR = "Could not read the repair report: {cause}"
#: The first line of a failure's own text, at most this long, names the cause.
_CAUSE_MAX_CHARS = 300


def _failure_cause(exc: Exception) -> str:
    """A short reason for a failed schedule or read that the model and telemetry can use.

    GitHub API errors carry status and GitHub's message, and this package's
    ``ValueError`` texts are written for the user. A file error keeps only its
    ``strerror`` (no local path); any other error is named by type only.
    """
    if isinstance(exc, GitHubApiError | ValueError):
        text = str(exc).strip().splitlines()
        first = text[0].strip() if text else ""
        if first:
            return first[:_CAUSE_MAX_CHARS]
    elif isinstance(exc, OSError) and exc.strerror:
        return f"{type(exc).__name__}: {exc.strerror}."
    return f"{type(exc).__name__}."


def _inspection_done(run: RepairRun, *, wait_until_terminal: bool, until: float) -> bool:
    """Whether this read should return instead of sleeping."""
    if run.terminal:
        return True
    if wait_until_terminal:
        return time.time() >= run.deadline
    return time.monotonic() >= until


@tool(
    name="schedule_ci_repair_loop",
    source="github",
    display_name="Schedule bounded CI repair",
    use_cases=["Repair one selected PR in the background"],
    description=(
        "Schedule repair of one open GitHub PR whose branch is in the same repository. "
        "On a hosted gateway, registers with its existing scheduler; on a laptop, starts "
        "and checks the local background scheduler. Uses a real 30-second trigger, stops "
        "after three failed attempts or within ten minutes, and retains a linked outcome "
        "report. Reuses the active run without extending its deadline."
    ),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    accepts_runtime_context=True,
    is_available=github_tool_available(github_source_available),
    extract_params=github_tool_params(_credentials),
    injected_params=GITHUB_INJECTED_PARAMS,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {
                "type": "string",
                "description": "GitHub user or organization that owns the repository.",
            },
            "repo": {
                "type": "string",
                "description": "Repository that holds the pull request.",
            },
            "pr_number": {
                "type": "integer",
                "minimum": 1,
                "description": "Existing PR to repair.",
            },
        },
        "required": ["owner", "repo", "pr_number"],
        "additionalProperties": False,
    },
)
@require_webapp_github
def schedule_ci_repair_loop(
    owner: str,
    repo: str,
    pr_number: int,
    github_token: str | None = None,
    context: Any = None,
    fast_checks: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Authorize exactly one bounded repair scope and return its durable identity."""
    try:
        store = RepairStore()
        run, reused, next_run = schedule_repair(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            github_token=github_token,
            github_connection_id=str(_kwargs.get("github_connection_id") or ""),
            store=store,
            scheduler_in_process=_scheduler_in_process(context),
            fast_checks=fast_checks,
        )
    except RepairRefused as exc:
        return {
            "ok": False,
            "error": _REFUSED_ERROR.format(reason=exc.user_message),
            "error_kind": ERROR_KIND_REFUSED,
            "response_text": exc.user_message,
        }
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
            "error": _SCHEDULE_ERROR.format(cause=_failure_cause(exc)),
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
    description=(
        "Read the linked summary of a CI repair run: what it did, how it ended and why. "
        "Omit task_id for this account's most recent run. "
        "wait_until_terminal waits until the run is terminal or its deadline has passed. "
        "wait_seconds waits at most sixty seconds. Never starts another repair."
    ),
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.READ_ONLY,
    is_available=github_tool_available(github_source_available),
    extract_params=github_tool_params(_credentials),
    injected_params=GITHUB_INJECTED_PARAMS,
    input_schema={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": (
                    "Run id returned by schedule_ci_repair_loop; omit it for the most recent run."
                ),
            },
            "wait_seconds": {
                "type": "integer",
                "minimum": 0,
                "maximum": 60,
                "description": "Seconds to wait for a terminal result; default zero.",
            },
            "wait_until_terminal": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Wait until the run is terminal or its repair deadline has passed. "
                    "One call. Ignores the sixty-second wait_seconds cap."
                ),
            },
        },
        "additionalProperties": False,
    },
)
@require_webapp_github
def get_ci_repair_loop(
    task_id: str = "",
    wait_seconds: int = 0,
    wait_until_terminal: bool = False,
    github_token: str | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Retrieve the same report while the shell is open or after returning later."""
    until = time.monotonic() + min(60, max(0, wait_seconds))
    try:
        store = RepairStore()
        token = configured_token(github_token)
        user = object_response(GitHubRestClient(token).request("GET", "user"))
        actor_id = account_id(user)
        run_id = task_id.strip()
        if not run_id:
            newest = store.newest_for(actor_id)
            if newest is None:
                return {"ok": False, "error": _NO_RUNS_YET}
            run_id = newest.id
        while True:
            run = store.get(run_id)
            if not run.actor_id or run.actor_id != actor_id:
                return {"ok": False, "error": "This repair belongs to a different GitHub account."}
            if _inspection_done(run, wait_until_terminal=wait_until_terminal, until=until):
                return _result(run, store)
            remaining = (
                run.deadline - time.time() if wait_until_terminal else until - time.monotonic()
            )
            time.sleep(min(1, max(0.0, remaining)))
    except (ValueError, OSError, RuntimeError, GitHubApiError) as exc:
        report_run_error(
            exc,
            tool_name="get_ci_repair_loop",
            source="github",
            component=__name__,
            method="RepairStore.get",
        )
        return {
            "ok": False,
            "error": _READ_ERROR.format(cause=_failure_cause(exc)),
            "response_text": "Check the GitHub connection and the run id.",
        }
