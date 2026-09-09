"""GitHub Actions workflow investigation tools - MCP-direct implementation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from core.tool_framework.utils import code_host_unavailable_payload
from integrations.github.envelope import normalize_github_tool_result
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_creds,
    github_source_available,
    resolve_github_mcp_config,
)
from integrations.github.mcp import call_github_mcp_tool


def _extract_json_text(result: dict[str, Any]) -> dict[str, Any] | str | None:
    text = str(result.get("text") or "").strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        return cast(dict[str, Any], parsed)
    except json.JSONDecodeError:
        return text


def _extract_list(result: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Extract list of items from MCP tool result."""
    json_result = _extract_json_text(result)
    items: list[Any] = []
    if isinstance(json_result, dict):
        value = json_result.get(key)
        if isinstance(value, list):
            items = value
    return [item for item in items if isinstance(item, dict)]


def _extract_workflow_jobs(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract workflow jobs from MCP tool result."""
    json_result = _extract_json_text(result)
    if isinstance(json_result, dict) and "jobs" in json_result:
        jobs_raw = json_result["jobs"]
        if isinstance(jobs_raw, dict) and "jobs" in jobs_raw:
            target_list = jobs_raw["jobs"]
        else:
            target_list = jobs_raw
        if isinstance(target_list, list):
            return [_normalize_job(job) for job in target_list if isinstance(job, dict)]
    return []


def _extract_log_text(result: dict[str, Any]) -> tuple[str, int | None]:
    """Extract log text and its original (pre-tail) line count from an MCP tool result.

    ``original_lines`` is the job's total log line count before ``tail_lines``
    truncated it (github-mcp-server's ``get_job_logs`` reports this as
    ``original_length``, despite the name it's a line count, not a character
    count: it's the ring buffer's total line tally, from the same downstream
    fetch that also produces the tailed ``logs_content``). ``None`` when the
    MCP response doesn't report it.

    Text is returned unstripped: ``original_length`` counts blank lines, so
    stripping here would report a complete log as truncated. ``extract_step_log``
    strips every branch it returns, so rendered output is unaffected.
    """
    json_result = _extract_json_text(result)
    if isinstance(json_result, dict) and "logs_content" in json_result:
        text = str(json_result["logs_content"] or "")
        original_lines = json_result.get("original_length")
        return text, original_lines if isinstance(original_lines, int) else None
    return str(result.get("text") or ""), None


def _normalize_step(step: dict[str, Any]) -> dict[str, Any]:
    """Normalize step data."""
    return {
        "name": step.get("name", ""),
        "status": step.get("status", ""),
        "conclusion": step.get("conclusion", ""),
        "number": step.get("number"),
        "started_at": step.get("started_at", ""),
        "completed_at": step.get("completed_at", ""),
    }


def _normalize_job(job: dict[str, Any]) -> dict[str, Any]:
    """Normalize job data including steps."""
    steps_raw = job.get("steps")
    steps: list[dict[str, Any]] = []
    if isinstance(steps_raw, list):
        for item in steps_raw:
            if isinstance(item, dict):
                steps.append(_normalize_step(item))

    return {
        "id": job.get("id"),
        "run_id": job.get("run_id"),
        "name": job.get("name", ""),
        "status": job.get("status", ""),
        "conclusion": job.get("conclusion", ""),
        "started_at": job.get("started_at", ""),
        "completed_at": job.get("completed_at", ""),
        "runner_name": job.get("runner_name", ""),
        "runner_group_name": job.get("runner_group_name", ""),
        "labels": job.get("labels", []),
        "html_url": job.get("html_url", ""),
        "steps": steps,
    }


_RUN_HISTORY_FIELDS = (
    "id",
    "name",
    "status",
    "conclusion",
    "run_number",
    "run_attempt",
    "event",
    "created_at",
    "updated_at",
    "html_url",
)


def _run_history_row(run: dict[str, Any]) -> dict[str, Any]:
    """The fields that answer "did this workflow fail and get re-run on this commit"."""
    return {key: run.get(key) for key in _RUN_HISTORY_FIELDS}


def _normalize_run(run: dict[str, Any]) -> dict[str, Any]:
    """Normalize workflow run data."""
    actor_raw = run.get("actor")
    actor = actor_raw if isinstance(actor_raw, dict) else {}
    triggering_actor_raw = run.get("triggering_actor")
    triggering_actor = triggering_actor_raw if isinstance(triggering_actor_raw, dict) else {}

    pull_requests_raw = run.get("pull_requests")
    pull_requests: list[dict[str, Any]] = []
    if isinstance(pull_requests_raw, list):
        for item in pull_requests_raw:
            if isinstance(item, dict):
                head_raw = item.get("head")
                head = head_raw if isinstance(head_raw, dict) else {}
                pull_requests.append(
                    {
                        "number": item.get("number"),
                        "url": item.get("html_url", ""),
                        "head_branch": head.get("ref", ""),
                    }
                )

    return {
        "id": run.get("id"),
        "name": run.get("name", ""),
        "display_title": run.get("display_title", ""),
        "head_branch": run.get("head_branch", ""),
        "head_sha": run.get("head_sha", ""),
        "event": run.get("event", ""),
        "status": run.get("status", ""),
        "conclusion": run.get("conclusion", ""),
        "run_number": run.get("run_number"),
        "run_attempt": run.get("run_attempt"),
        "html_url": run.get("html_url", ""),
        "created_at": run.get("created_at", ""),
        "updated_at": run.get("updated_at", ""),
        "actor": actor.get("login", "") if isinstance(actor, dict) else "",
        "triggering_actor": triggering_actor.get("login", "")
        if isinstance(triggering_actor, dict)
        else "",
        "pull_requests": pull_requests,
    }


#: The window a rate question should ask for. Not the tool default: this listing
#: also answers "which deploy failed right before the incident", where a run days
#: old is the whole point, so defaulting to a window would hide it from every
#: caller that never asked for one. Rate callers pass this explicitly.
RATE_WINDOW_HOURS = 24

#: No window: return the page as fetched, whatever the age of its runs.
NO_RUN_WINDOW = 0

#: MCP ``actions_list`` listings are repository-wide (it does not apply
#: ``head_sha``). Page newest-first until the commit's cluster is past or
#: the listing ends. Cap pages so one tool call cannot scan the repo.
_HEAD_SHA_MAX_PAGES = 10
_GITHUB_RUNS_PER_PAGE_MAX = 100


def _run_started_at(run: dict[str, Any]) -> datetime | None:
    """Parse a run's ``created_at``; ``None`` when absent or unparsable."""
    raw = str(run.get("created_at") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class WindowedRuns:
    """One page of runs narrowed to a time window.

    ``window_fully_fetched`` is True when the page proves every in-window run
    that exists (under the same filters) is in ``runs``:

    * a dated run older than the window came back (newest-first listing
      scrolled past the cutoff), or
    * the fetched page is shorter than the requested page size (the listing
      is exhausted — no further runs exist to miss).

    Otherwise the page ended inside the window while still full, so older
    in-window runs may exist unfetched and counts over ``runs`` are a floor.
    ``undated`` counts runs whose ``created_at`` could not be read; they are
    in neither ``runs`` nor the older-run evidence for coverage.
    """

    runs: list[dict[str, Any]]
    window_fully_fetched: bool
    undated: int


def window_runs(
    runs: list[dict[str, Any]],
    *,
    window_hours: int,
    now: datetime,
    page_limit: int,
) -> WindowedRuns:
    """Narrow ``runs`` to those started within ``window_hours`` before ``now``.

    ``page_limit`` is the ``per_page`` asked of the API. A shorter result means
    the listing is exhausted, so an all-in-window page is still complete.
    """
    cutoff = now - timedelta(hours=window_hours)
    inside: list[dict[str, Any]] = []
    older = 0
    undated = 0
    for run in runs:
        started = _run_started_at(run)
        if started is None:
            undated += 1
        elif started >= cutoff:
            inside.append(run)
        else:
            older += 1
    page_exhausted = page_limit > 0 and len(runs) < page_limit
    return WindowedRuns(
        runs=inside,
        window_fully_fetched=older > 0 or page_exhausted,
        undated=undated,
    )


@dataclass(frozen=True, slots=True)
class CommitRunHistory:
    """One commit's workflow runs collected from newest-first MCP pages."""

    payload: dict[str, Any]
    runs: list[dict[str, Any]]
    fully_fetched: bool
    fetched_before_filter: int
    pages_fetched: int


def _matches_head_sha(run: dict[str, Any], head_sha: str) -> bool:
    """True when ``run`` is for ``head_sha`` (full SHA or a prefix)."""
    return str(run.get("head_sha") or "").startswith(head_sha)


def _strip_raw_mcp_page(payload: dict[str, Any]) -> None:
    """Drop the raw MCP echo; the normalized rows are the result."""
    for raw_key in ("text", "content", "structured_content"):
        payload.pop(raw_key, None)


def _fetch_workflow_run_page(
    config: Any, arguments: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One ``actions_list`` page and its normalized runs."""
    result = call_github_mcp_tool(config, "actions_list", arguments)
    payload = normalize_github_tool_result(result)
    if not isinstance(payload, dict):
        return {"error": "Unexpected payload format returned from GitHub MCP tool"}, []
    if not payload.get("available"):
        return payload, []
    runs = [_normalize_run(item) for item in _extract_list(result, "workflow_runs")]
    return payload, runs


def _commit_run_history(
    config: Any,
    base_arguments: dict[str, Any],
    *,
    head_sha: str,
    per_page: int,
) -> CommitRunHistory:
    """Collect one commit's runs from newest-first MCP pages.

    Completeness is a short page (listing exhausted) or a full page with no
    matches after at least one match (the listing has moved past this
    commit). Hitting the page cap while pages stay full is incomplete.
    A later page failure keeps already-fetched matches and reports incomplete.
    """
    page_limit = max(1, min(per_page, _GITHUB_RUNS_PER_PAGE_MAX))
    collected: list[dict[str, Any]] = []
    fetched = 0
    pages_fetched = 0
    saw_match = False
    complete = False
    last_payload: dict[str, Any] = {"available": False}

    for page in range(1, _HEAD_SHA_MAX_PAGES + 1):
        arguments = {**base_arguments, "page": page, "per_page": page_limit}
        payload, page_runs = _fetch_workflow_run_page(config, arguments)
        if not payload.get("available"):
            if pages_fetched == 0:
                return CommitRunHistory(payload, [], False, 0, 0)
            break
        last_payload = payload
        pages_fetched += 1
        fetched += len(page_runs)
        matches = [run for run in page_runs if _matches_head_sha(run, head_sha)]
        collected.extend(matches)
        if matches:
            saw_match = True
        if len(page_runs) < page_limit:
            complete = True
            break
        if saw_match and not matches:
            complete = True
            break

    return CommitRunHistory(
        payload=last_payload,
        runs=collected,
        fully_fetched=complete,
        fetched_before_filter=fetched,
        pages_fetched=pages_fetched,
    )


UNGROUPED_SECTION_NAME = "ungrouped"


def _append_log_section(sections: list[dict[str, str]], name: str, lines: list[str]) -> None:
    """Append a non-empty log section preserving original ordering."""
    text = "\n".join(lines).strip()
    if text:
        sections.append({"name": name, "text": text})


def _extract_log_sections(log_text: str) -> list[dict[str, str]]:
    """Extract sections from GitHub Actions log output (marked by ##[group]/##[endgroup])."""
    sections: list[dict[str, str]] = []
    current_name: str | None = None
    current_lines: list[str] = []
    saw_group = False

    for line in log_text.splitlines():
        if line.startswith("##[group]"):
            saw_group = True
            _append_log_section(sections, current_name or UNGROUPED_SECTION_NAME, current_lines)
            current_name = line[len("##[group]") :].strip()
            current_lines = []
            continue
        if line.startswith("##[endgroup]"):
            _append_log_section(sections, current_name or UNGROUPED_SECTION_NAME, current_lines)
            current_name = None
            current_lines = []
            continue
        current_lines.append(line)

    if saw_group:
        _append_log_section(sections, current_name or UNGROUPED_SECTION_NAME, current_lines)

    if not saw_group:
        # No ##[group] markers at all: nothing to select by name/number.
        # extract_step_log's own full-log fallback handles this case.
        return []
    return [section for section in sections if section.get("text")]


def extract_step_log(
    log_text: str,
    *,
    step_name: str = "",
    step_number: int | None = None,
) -> dict[str, Any]:
    """Extract the log text for a specific step in a GitHub Actions job log,
    using grouping markers if available."""
    sections = _extract_log_sections(log_text)
    selected: dict[str, str] | None = None
    match_strategy = "full-log"
    selected_idx = -1

    if step_name:
        needle = step_name.strip().lower()
        for i, section in enumerate(sections):
            if needle and needle in section.get("name", "").lower():
                selected = section
                match_strategy = "step_name"
                selected_idx = i
                break

    group_count = sum(1 for s in sections if s.get("name") != UNGROUPED_SECTION_NAME)
    if selected is None and step_number is not None and 1 <= step_number <= group_count:
        group_counter = 0
        for i, section in enumerate(sections):
            if section.get("name") != UNGROUPED_SECTION_NAME:
                group_counter += 1
            if group_counter == step_number:
                selected = section
                match_strategy = "step_number"
                selected_idx = i
                break

    if selected is None:
        selected = {"name": "full-log", "text": log_text.strip()}

    text = selected.get("text", "")

    # Merge trailing ungrouped annotations into the matched step log
    if selected_idx != -1 and selected_idx + 1 < len(sections):
        next_section = sections[selected_idx + 1]
        if next_section.get("name") == UNGROUPED_SECTION_NAME:
            text += "\n" + next_section.get("text", "")

    return {
        "step_name": selected.get("name", ""),
        "match_strategy": match_strategy,
        "log_text": text,
    }


def _github_actions_is_available(sources: dict[str, dict]) -> bool:
    """Check if GitHub Actions tool should be available."""
    github = sources.get("github", {})
    return bool(github_source_available(sources) and github.get("owner") and github.get("repo"))


def _github_actions_repo_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract repo parameters for GitHub Actions tools."""
    github = sources["github"]
    params: dict[str, Any] = {
        "owner": github["owner"],
        "repo": github["repo"],
        **github_creds(github),
    }
    return params


def _github_actions_run_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract run/job parameters for GitHub Actions tools."""
    github = sources["github"]
    params = _github_actions_repo_params(sources)
    if github.get("run_id") is not None:
        params["run_id"] = github["run_id"]
    if github.get("job_id") is not None:
        params["job_id"] = github["job_id"]
    return params


def _map_list_github_actions_workflow_runs(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    runs = output.get("workflow_runs", [])
    if runs:
        count = len(runs)
        word = "run" if count == 1 else "runs"
        record_evidence_entry(
            evidence,
            source="list_github_actions_workflow_runs",
            label="GitHub Workflow Runs",
            summary=f"{count} {word}",
        )


@tool(
    name="list_github_actions_workflow_runs",
    source="github",
    description=(
        "List GitHub Actions workflow runs for a repository, each with status, "
        "conclusion and run_attempt. With head_sha it is the run history of one "
        "commit: a run_attempt above 1 means that workflow was re-run on that "
        "commit, and its conclusion says whether the re-run passed. The result "
        "reports history_fully_fetched — when false, more runs for that commit "
        "may exist beyond the pages fetched, so a missing attempt is not proof "
        "it did not happen."
    ),
    use_cases=[
        "Checking which deploy or test workflow failed right before an incident",
        "Reviewing recent workflow status, trigger, and branch context",
        "Finding a run that matches an outage window or rollback event",
        "Telling whether a commit's workflow failed and was re-run to green (run_attempt per run)",
    ],
    requires=["owner", "repo"],
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "branch": {"type": "string", "default": ""},
            "status": {"type": "string", "default": ""},
            "event": {"type": "string", "default": ""},
            "head_sha": {
                "type": "string",
                "default": "",
                "description": (
                    "Only return runs for this commit SHA. Pages until the "
                    "commit's history is complete or the page cap; see "
                    "history_fully_fetched."
                ),
            },
            "per_page": {"type": "integer", "default": 30},
            "window_hours": {
                "type": "integer",
                "default": NO_RUN_WINDOW,
                "description": (
                    f"Only return runs started within this many hours; {NO_RUN_WINDOW} "
                    f"(the default) returns the page as fetched. Pass {RATE_WINDOW_HOURS} "
                    "for a rate or count over the last day. The result reports "
                    "window_fully_fetched — when false the page is full and ended "
                    "inside the window, so counts over it are partial; when true "
                    "either an older run proved the cutoff was reached or the "
                    "listing was exhausted (fewer runs than per_page)."
                ),
            },
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_actions_is_available,
    extract_params=_github_actions_repo_params,
    injected_params=GITHUB_INJECTED_PARAMS,
    evidence_mapper=_map_list_github_actions_workflow_runs,
)
def list_github_actions_workflow_runs(
    owner: str,
    repo: str,
    branch: str = "",
    status: str = "",
    event: str = "",
    head_sha: str = "",
    per_page: int = 30,
    window_hours: int = NO_RUN_WINDOW,
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List recent GitHub Actions workflow runs for a repository."""
    config = resolve_github_mcp_config(
        github_url, github_mode, github_token, github_command, github_args
    )
    if config is None:
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub Actions",
            empty_key="workflow_runs",
            empty_value=[],
        )

    workflow_runs_filter: dict[str, Any] = {}
    if branch:
        workflow_runs_filter["branch"] = branch
    if status:
        workflow_runs_filter["status"] = status
    if event:
        workflow_runs_filter["event"] = event
    if head_sha:
        workflow_runs_filter["head_sha"] = head_sha

    arguments: dict[str, Any] = {
        "method": "list_workflow_runs",
        "owner": owner,
        "repo": repo,
        "per_page": per_page,
    }
    if workflow_runs_filter:
        arguments["workflow_runs_filter"] = workflow_runs_filter

    history: CommitRunHistory | None = None
    if head_sha:
        history = _commit_run_history(config, arguments, head_sha=head_sha, per_page=per_page)
        payload = history.payload
        workflow_runs = history.runs
    else:
        result = call_github_mcp_tool(config, "actions_list", arguments)
        payload = normalize_github_tool_result(result)
        workflow_runs = [_normalize_run(item) for item in _extract_list(result, "workflow_runs")]

    if not isinstance(payload, dict):
        return {"error": "Unexpected payload format returned from GitHub MCP tool"}

    if payload.get("available"):
        if history is not None:
            workflow_runs = [_run_history_row(item) for item in workflow_runs]
            payload["runs_fetched_before_commit_filter"] = history.fetched_before_filter
            payload["history_fully_fetched"] = history.fully_fetched
            payload["pages_fetched"] = history.pages_fetched
        _strip_raw_mcp_page(payload)
        if window_hours > NO_RUN_WINDOW:
            page_limit = per_page
            if history is not None:
                page_limit = len(workflow_runs) + 1 if history.fully_fetched else len(workflow_runs)
            windowed = window_runs(
                workflow_runs,
                window_hours=window_hours,
                now=datetime.now(UTC),
                page_limit=page_limit,
            )
            workflow_runs = windowed.runs
            payload["window_fully_fetched"] = windowed.window_fully_fetched
            payload["undated_runs"] = windowed.undated
        payload["workflow_runs"] = workflow_runs
        payload["total"] = len(workflow_runs)
    else:
        payload["workflow_runs"] = []
        payload["total"] = 0

    payload["window_hours"] = window_hours
    payload["branch"] = branch
    payload["status"] = status
    payload["event"] = event
    payload["head_sha"] = head_sha

    return payload


def _map_list_github_actions_active_runs(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    runs = output.get("workflow_runs", [])
    if runs:
        count = len(runs)
        word = "run" if count == 1 else "runs"
        record_evidence_entry(
            evidence,
            source="list_github_actions_active_runs",
            label="GitHub Active Runs",
            summary=f"{count} {word}",
        )


@tool(
    name="list_github_actions_active_runs",
    source="github",
    description="List GitHub Actions workflow runs that are currently queued or in progress.",
    use_cases=[
        "Seeing what deployment jobs are still running during an incident",
        "Spotting queued deploys that may be waiting on a shared runner or lock",
    ],
    requires=["owner", "repo"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "per_page": {"type": "integer", "default": 30},
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo"],
    },
    is_available=_github_actions_is_available,
    extract_params=_github_actions_repo_params,
    injected_params=GITHUB_INJECTED_PARAMS,
    evidence_mapper=_map_list_github_actions_active_runs,
)
def list_github_actions_active_runs(
    owner: str,
    repo: str,
    per_page: int = 30,
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List GitHub Actions workflow runs that are currently queued or in progress."""
    config = resolve_github_mcp_config(
        github_url, github_mode, github_token, github_command, github_args
    )
    if config is None:
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub Actions",
            empty_key="workflow_runs",
            empty_value=[],
        )

    # Fetch queued runs
    queued_result = call_github_mcp_tool(
        config,
        "actions_list",
        {
            "method": "list_workflow_runs",
            "owner": owner,
            "repo": repo,
            "per_page": per_page,
            "workflow_runs_filter": {"status": "queued"},
        },
    )

    # Fetch in_progress runs
    in_progress_result = call_github_mcp_tool(
        config,
        "actions_list",
        {
            "method": "list_workflow_runs",
            "owner": owner,
            "repo": repo,
            "per_page": per_page,
            "workflow_runs_filter": {"status": "in_progress"},
        },
    )

    # Check for errors
    if queued_result.get("is_error") or in_progress_result.get("is_error"):
        error_texts: list[str] = []
        for result in (queued_result, in_progress_result):
            if result.get("is_error") and result.get("text"):
                error_texts.append(str(result.get("text")))

        error_msg = " | ".join(error_texts) if error_texts else "Failed to list active runs"

        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub Actions",
            empty_key="workflow_runs",
            empty_value=[],
        ) | {"error": error_msg}

    # Combine and deduplicate
    combined: list[dict[str, Any]] = []
    seen_ids: set[Any] = set()

    for result in (queued_result, in_progress_result):
        runs_raw = _extract_list(result, "workflow_runs")
        for run in runs_raw:
            run_id = run.get("id")
            if run_id is not None and run_id not in seen_ids:
                seen_ids.add(run_id)
                combined.append(_normalize_run(run))

    combined.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)

    return {
        "source": "github",
        "available": True,
        "workflow_runs": combined,
        "total": len(combined),
    }


def _map_list_github_actions_run_jobs(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    jobs = output.get("jobs", [])
    if jobs:
        count = len(jobs)
        word = "job" if count == 1 else "jobs"
        record_evidence_entry(
            evidence,
            source="list_github_actions_run_jobs",
            label="GitHub Run Jobs",
            summary=f"{count} {word}",
        )


@tool(
    name="list_github_actions_run_jobs",
    source="github",
    description="List jobs and step outcomes for a GitHub Actions workflow run.",
    use_cases=[
        "Finding which job failed in a deployment workflow",
        "Checking step-by-step status for test, build, and deploy jobs",
    ],
    requires=["owner", "repo", "run_id"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "run_id": {"type": "integer"},
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo", "run_id"],
    },
    is_available=_github_actions_is_available,
    extract_params=_github_actions_run_params,
    injected_params=GITHUB_INJECTED_PARAMS,
    evidence_mapper=_map_list_github_actions_run_jobs,
)
def list_github_actions_run_jobs(
    owner: str,
    repo: str,
    run_id: int,
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List jobs and step outcomes for a GitHub Actions workflow run."""
    config = resolve_github_mcp_config(
        github_url, github_mode, github_token, github_command, github_args
    )
    if config is None:
        return code_host_unavailable_payload(
            source="github",
            integration_name="GitHub Actions",
            empty_key="jobs",
            empty_value=[],
        )

    result = call_github_mcp_tool(
        config,
        "actions_list",
        {
            "method": "list_workflow_jobs",
            "owner": owner,
            "repo": repo,
            "resource_id": str(run_id),
        },
    )
    payload = normalize_github_tool_result(result)
    if not isinstance(payload, dict):
        return {"error": "Unexpected payload format returned from GitHub MCP tool"}

    if payload.get("available"):
        jobs = _extract_workflow_jobs(result)
        payload["jobs"] = jobs
        payload["total"] = len(jobs)
    else:
        payload["jobs"] = []
        payload["total"] = 0

    payload["workflow_run_id"] = run_id
    return payload


def _step_log_unavailable_payload(error: Any = None) -> dict[str, Any]:
    """Unavailable/error payload for ``get_github_actions_step_log``.

    Carries the same truncation and retry keys as the success payload so callers
    never have to key-check before reading them.
    """
    payload = code_host_unavailable_payload(
        source="github",
        integration_name="GitHub Actions",
        empty_key="log_text",
        empty_value="",
    ) | {
        "truncated": False,
        "returned_lines": 0,
        "original_lines": None,
        "retry_attempted": False,
        "retry_error": None,
    }
    if error is not None:
        payload["error"] = error
    return payload


def _fetch_job_log(
    config: Any, owner: str, repo: str, job_id: int, tail_lines: int
) -> tuple[str, int | None, str | None]:
    """Fetch job log text via get_job_logs. Returns (text, original_lines, error)."""
    log_result = call_github_mcp_tool(
        config,
        "get_job_logs",
        {
            "owner": owner,
            "repo": repo,
            "job_id": job_id,
            "return_content": True,
            "tail_lines": tail_lines,
        },
    )
    if log_result.get("is_error"):
        return "", None, str(log_result.get("text") or "unknown error")
    text, original_lines = _extract_log_text(log_result)
    return text, original_lines, None


def _map_get_github_actions_step_log(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    if output.get("log_text"):
        lines_count = output.get("returned_lines", 0)
        word = "line" if lines_count == 1 else "lines"
        record_evidence_entry(
            evidence,
            source="get_github_actions_step_log",
            label="GitHub Actions Step Log",
            summary=f"{lines_count} {word}",
        )


@tool(
    name="get_github_actions_step_log",
    source="github",
    description="Fetch the log output for a failed GitHub Actions job step.",
    use_cases=[
        "Reading the error output for the step that broke a deployment",
        "Checking the exact log snippet for a flaky test or secret-related failure",
    ],
    requires=["owner", "repo", "run_id", "job_id"],
    surfaces=(ToolSurface.CHAT,),
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "run_id": {"type": "integer"},
            "job_id": {"type": "integer"},
            "step_name": {"type": "string", "default": ""},
            "step_number": {"type": "integer"},
            "tail_lines": {"type": "integer", "default": 500},
            "github_url": {"type": "string"},
            "github_mode": {"type": "string"},
            "github_token": {"type": "string"},
        },
        "required": ["owner", "repo", "run_id", "job_id"],
    },
    is_available=_github_actions_is_available,
    extract_params=_github_actions_run_params,
    injected_params=GITHUB_INJECTED_PARAMS,
    evidence_mapper=_map_get_github_actions_step_log,
)
def get_github_actions_step_log(
    owner: str,
    repo: str,
    run_id: int,
    job_id: int,
    step_name: str = "",
    step_number: int | None = None,
    tail_lines: int = 500,
    github_url: str | None = None,
    github_mode: str | None = None,
    github_token: str | None = None,
    github_command: str | None = None,
    github_args: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch the log output for a failed GitHub Actions job step."""
    config = resolve_github_mcp_config(
        github_url, github_mode, github_token, github_command, github_args
    )
    if config is None:
        return _step_log_unavailable_payload()

    # Fetch job metadata
    job_result = call_github_mcp_tool(
        config,
        "actions_get",
        {
            "method": "get_workflow_job",
            "owner": owner,
            "repo": repo,
            "resource_id": str(job_id),
        },
    )

    if job_result.get("is_error"):
        return _step_log_unavailable_payload(job_result.get("text"))

    job = _extract_json_text(job_result)
    if not isinstance(job, dict):
        return _step_log_unavailable_payload("Unexpected job metadata format")

    # Fetch job logs
    log_text, original_lines, log_error = _fetch_job_log(config, owner, repo, job_id, tail_lines)
    if log_error is not None:
        return _step_log_unavailable_payload(log_error)

    # Detect first failed step if not specified
    failed_step = ""
    steps_raw = job.get("steps") if isinstance(job, dict) else []
    normalized_steps = []
    if isinstance(steps_raw, list):
        normalized_steps = [_normalize_step(step) for step in steps_raw if isinstance(step, dict)]
        if not step_name:
            for step in steps_raw:
                if not isinstance(step, dict):
                    continue
                if step.get("conclusion") == "failure" or step.get("status") == "failure":
                    failed_step = str(step.get("name") or "")
                    break

    # Extract and filter step log
    extracted = extract_step_log(
        log_text,
        step_name=step_name or failed_step,
        step_number=step_number,
    )

    # A requested step that missed because the tail cut off its ##[group] block
    # looks identical to "no such step" (both fall back to match_strategy
    # "full-log"). Re-fetch a bigger tail, sized from the log's own reported
    # total line count, once before accepting that fallback.
    wants_step = bool(step_name or failed_step or step_number is not None)
    returned_lines = len(log_text.splitlines())
    truncated = original_lines is not None and original_lines > returned_lines
    retry_attempted = False
    retry_error: str | None = None
    if (
        original_lines is not None
        and truncated
        and wants_step
        and extracted["match_strategy"] == "full-log"
    ):
        # get_job_logs already clamps to the server's content window.
        retry_tail_lines = original_lines
        if retry_tail_lines > tail_lines:
            retry_attempted = True
            retry_text, retry_original_lines, retry_error = _fetch_job_log(
                config, owner, repo, job_id, retry_tail_lines
            )
            if retry_error is None:
                retry_extracted = extract_step_log(
                    retry_text,
                    step_name=step_name or failed_step,
                    step_number=step_number,
                )
                # Adopt the bigger body only if it found the step. Otherwise it
                # is the same full-log fallback orders of magnitude larger,
                # headed straight into the agent's context; keep the first
                # response. "full-log" plus retry_attempted and no retry_error
                # already says a wider window was fetched and still missed.
                if retry_extracted["match_strategy"] != "full-log":
                    extracted = retry_extracted
                    log_text = retry_text
                    # A retry without original_length must not erase a known count.
                    if retry_original_lines is not None:
                        original_lines = retry_original_lines
                    returned_lines = len(log_text.splitlines())
                    truncated = original_lines is not None and original_lines > returned_lines

    extracted.update(
        {
            "source": "github",
            "available": True,
            "workflow_run_id": run_id,
            "job_id": job_id,
            "job_name": job.get("name", ""),
            "job_conclusion": job.get("conclusion", ""),
            "job_steps": normalized_steps,
            "truncated": truncated,
            "returned_lines": returned_lines,
            "original_lines": original_lines,
            "retry_attempted": retry_attempted,
            "retry_error": retry_error,
        }
    )
    return extracted


__all__ = [
    "extract_step_log",
    "get_github_actions_step_log",
    "list_github_actions_active_runs",
    "list_github_actions_run_jobs",
    "list_github_actions_workflow_runs",
]
