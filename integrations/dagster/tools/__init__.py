# ======== from tools/dagster_assets_tool/ ========

"""Dagster assets materialization query tool."""

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.dagster import (
    DagsterConfig,
    dagster_extract_params,
    dagster_is_available,
    list_assets_with_materialization,
)


@tool(
    name="list_dagster_assets",
    description="List Dagster assets and their latest materialization status.",
    use_cases=[
        "Check the latest materialization status of Dagster assets",
        "Find Dagster assets whose latest materialization is old or failed",
    ],
    source="dagster",
    surfaces=(ToolSurface.CHAT,),
    is_available=dagster_is_available,
    injected_params=("api_token", "endpoint"),
    extract_params=dagster_extract_params,
)
def list_dagster_assets(
    endpoint: str,
    api_token: str = "",
    limit: int = 25,
) -> dict[str, Any]:
    """Return assets and the timestamp/status of their most recent materialization."""
    config = DagsterConfig(endpoint=endpoint, api_token=api_token)
    return list_assets_with_materialization(config, limit=limit)


# ======== from tools/dagster_run_logs_tool/ ========

"""Dagster run logs query tool."""

from typing import Any

from core.tool_framework import tool
from integrations.dagster import (
    dagster_extract_params,
    dagster_is_available,
    get_run_logs,
)


@tool(
    name="get_dagster_run_logs",
    description=(
        "Fetch event logs and error details for a specific Dagster run. "
        "IMPORTANT: a single run may contain MULTIPLE step failures if ops "
        "ran in parallel and several failed independently. The response "
        "includes a top-level `summary.failures` list that pre-counts and "
        "pre-classifies each step failure (step_key, exception_class, "
        "cause_message). Always check `summary.failure_count` first; if it "
        "is greater than 1, surface ALL failures in your diagnosis as "
        "distinct root causes, do not pick only one. The underlying "
        "user-code exception lives in `cause_message` (the wrapper is "
        "always a generic DagsterExecutionStepExecutionError). If "
        "`summary.truncated` is true, the run produced more events than "
        "the inspection cap (`summary.events_examined`); treat the "
        "failure_count as a LOWER BOUND and hedge your diagnosis. If "
        "`summary.fetch_error` is set, a mid-pagination error stopped "
        "the fetch early; the failures shown are a partial set."
    ),
    use_cases=[
        "Read the error details and failed steps for a specific Dagster run",
        "Find every failed step in a run whose ops ran in parallel",
    ],
    source="dagster",
    surfaces=(ToolSurface.CHAT,),
    is_available=dagster_is_available,
    injected_params=("api_token", "endpoint"),
    extract_params=dagster_extract_params,
)
def get_dagster_run_logs(
    endpoint: str,
    *,
    api_token: str = "",
    run_id: str,
) -> dict[str, Any]:
    """Return event logs and any failure error message for the given run id."""
    config = DagsterConfig(endpoint=endpoint, api_token=api_token)
    return get_run_logs(config, run_id=run_id)


# ======== from tools/dagster_runs_tool/ ========

"""Dagster runs query tool."""

from typing import Any

from core.tool_framework import tool
from integrations.dagster import (
    dagster_extract_params,
    dagster_is_available,
    list_runs,
)


@tool(
    name="list_dagster_runs",
    description=(
        "List recent Dagster pipeline/job runs with status and duration. "
        "When the alert specifies a pipeline name (commonly in its "
        "`pipeline`, `alert_name`, or `details.pipeline` field), ALWAYS "
        "pass that as `job_name` to scope results. Dagster instances run "
        "many pipelines and without the filter you get an interleaved mix "
        "from every pipeline that contaminates your evidence. Do not call "
        "this tool multiple times trying different filters; set "
        '`job_name` once and pair it with `status="FAILURE"` for '
        "incident investigations."
    ),
    use_cases=[
        "List recent Dagster job runs with their status and duration",
        "Find the runs for a pipeline named in an alert",
    ],
    source="dagster",
    surfaces=(ToolSurface.CHAT,),
    is_available=dagster_is_available,
    injected_params=("api_token", "endpoint"),
    extract_params=dagster_extract_params,
)
def list_dagster_runs(
    endpoint: str,
    api_token: str = "",
    limit: int = 25,
    status: str | None = None,
    job_name: str | None = None,
) -> dict[str, Any]:
    """Return summaries of recent Dagster runs from the configured instance."""
    config = DagsterConfig(endpoint=endpoint, api_token=api_token)
    return list_runs(config, limit=limit, status=status, job_name=job_name)


# ======== from tools/dagster_schedules_tool/ ========

"""Dagster schedule tick history query tool."""

from typing import Any

from core.tool_framework import tool
from integrations.dagster import (
    dagster_extract_params,
    dagster_is_available,
    list_schedule_ticks,
)


@tool(
    name="list_dagster_schedule_ticks",
    description=(
        "Fetch recent tick history for a Dagster schedule. The schedule is "
        "identified by all three ScheduleSelector coordinates: repository "
        "location name, repository name, and schedule name."
    ),
    use_cases=[
        "Check whether a Dagster schedule fired, skipped, or failed recently",
    ],
    source="dagster",
    surfaces=(ToolSurface.CHAT,),
    is_available=dagster_is_available,
    injected_params=("api_token", "endpoint"),
    extract_params=dagster_extract_params,
)
def list_dagster_schedule_ticks(
    endpoint: str,
    *,
    api_token: str = "",
    repository_name: str,
    repository_location_name: str,
    schedule_name: str,
    limit: int = 25,
) -> dict[str, Any]:
    """Return the most recent ticks for the named schedule with status and error."""
    config = DagsterConfig(endpoint=endpoint, api_token=api_token)
    return list_schedule_ticks(
        config,
        repository_name=repository_name,
        repository_location_name=repository_location_name,
        schedule_name=schedule_name,
        limit=limit,
    )


# ======== from tools/dagster_sensors_tool/ ========

"""Dagster sensor tick history query tool."""

from typing import Any

from core.tool_framework import tool
from integrations.dagster import (
    dagster_extract_params,
    dagster_is_available,
    list_sensor_ticks,
)


@tool(
    name="list_dagster_sensor_ticks",
    description=(
        "Fetch recent tick history for a Dagster sensor. The sensor is "
        "identified by all three SensorSelector coordinates: repository "
        "location name, repository name, and sensor name."
    ),
    use_cases=[
        "Check whether a Dagster sensor evaluated or failed recently",
    ],
    source="dagster",
    surfaces=(ToolSurface.CHAT,),
    is_available=dagster_is_available,
    injected_params=("api_token", "endpoint"),
    extract_params=dagster_extract_params,
)
def list_dagster_sensor_ticks(
    endpoint: str,
    *,
    api_token: str = "",
    repository_name: str,
    repository_location_name: str,
    sensor_name: str,
    limit: int = 25,
) -> dict[str, Any]:
    """Return the most recent ticks for the named sensor with status and error."""
    config = DagsterConfig(endpoint=endpoint, api_token=api_token)
    return list_sensor_ticks(
        config,
        repository_name=repository_name,
        repository_location_name=repository_location_name,
        sensor_name=sensor_name,
        limit=limit,
    )
